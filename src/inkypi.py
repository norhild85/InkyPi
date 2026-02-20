#!/usr/bin/env python3

# set up logging
import os, logging.config

from pi_heif import register_heif_opener

logging.config.fileConfig(os.path.join(os.path.dirname(__file__), 'config', 'logging.conf'))

# suppress warning from inky library https://github.com/pimoroni/inky/issues/205
import warnings
warnings.filterwarnings("ignore", message=".*Busy Wait: Held high.*")

import os
import random
import time
import sys
import json
import logging
import threading
import argparse
from datetime import datetime
import pytz
from utils.app_utils import generate_startup_image
from utils.led_controller import LEDStripController
from flask import Flask, request, send_from_directory
from werkzeug.serving import is_running_from_reloader
from config import Config
from display.display_manager import DisplayManager
from refresh_task import RefreshTask, PlaylistRefresh
from blueprints.main import main_bp
from blueprints.settings import settings_bp
from blueprints.plugin import plugin_bp
from blueprints.playlist import playlist_bp
from jinja2 import ChoiceLoader, FileSystemLoader
from plugins.plugin_registry import load_plugins
from waitress import serve

try:
    from gpiozero import Button
    from gpiozero.exc import BadPinFactory
    GPIO_AVAILABLE = True
except (ImportError, BadPinFactory):
    GPIO_AVAILABLE = False


logger = logging.getLogger(__name__)

# GPIO Pin Configuration (BCM numbering)
BUTTON_REFRESH_PIN = 6  # Physical pin 31
BUTTON_NEXT_PIN = 26    # Physical pin 37
BUTTON_LED_TOGGLE_PIN = 19  # Physical pin 35 (for LED strip toggle)

# Parse command line arguments
parser = argparse.ArgumentParser(description='InkyPi Display Server')
parser.add_argument('--dev', action='store_true', help='Run in development mode')
args = parser.parse_args()

# Set development mode settings
if args.dev:
    Config.config_file = os.path.join(Config.BASE_DIR, "config", "device_dev.json")
    DEV_MODE = True
    PORT = 8080
    logger.info("Starting InkyPi in DEVELOPMENT mode on port 8080")
else:
    DEV_MODE = False
    PORT = 80
    logger.info("Starting InkyPi in PRODUCTION mode on port 80")
logging.getLogger('waitress.queue').setLevel(logging.ERROR)
app = Flask(__name__)
template_dirs = [
   os.path.join(os.path.dirname(__file__), "templates"),    # Default template folder
   os.path.join(os.path.dirname(__file__), "plugins"),      # Plugin templates
]
app.jinja_loader = ChoiceLoader([FileSystemLoader(directory) for directory in template_dirs])

device_config = Config()

def setup_buttons(refresh_task, device_config, led_controller):
    """Initializes GPIO buttons and assigns actions for refresh, next plugin, and LED toggle."""
    if not GPIO_AVAILABLE:
        logger.warning("gpiozero library not found or failed to initialize. Button support will be disabled.")
        return

    logger.info("Setting up GPIO buttons.")

    def handle_refresh():
        """Refreshes the currently displayed plugin instance."""
        logger.info("Button 1 pressed: Refreshing current view.")
        try:
            playlist_manager = device_config.get_playlist_manager()
            if not playlist_manager.active_playlist:
                logger.warning("No active playlist. Cannot refresh.")
                return

            playlist = playlist_manager.get_playlist(playlist_manager.active_playlist)
            if not playlist or not playlist.plugins:
                logger.warning("Active playlist not found or is empty. Cannot refresh.")
                return

            if playlist.current_plugin_index is None:
                # This can happen if a playlist becomes active but no plugin has been displayed yet.
                # Default to the first plugin in the list.
                plugin_instance = playlist.plugins[0]
            else:
                plugin_instance = playlist.plugins[playlist.current_plugin_index]

            logger.info(f"Refreshing current plugin: {plugin_instance.name}")
            refresh_action = PlaylistRefresh(playlist, plugin_instance, force=True)
            # Run manual update asynchronously so button press doesn't block callers.
            def _do_refresh(action):
                try:
                    display_manager.display_overlay("Updating...")
                    refresh_task.manual_update(action)
                except Exception as e:
                    logger.error(f"Error during async manual update: {e}", exc_info=True)

            threading.Thread(target=_do_refresh, args=(refresh_action,), daemon=True).start()

        except Exception as e:
            logger.error(f"Error during button-triggered refresh: {e}", exc_info=True)

    def handle_next_plugin():
        """Advances to the next plugin in the active playlist."""
        logger.info("Button 4 pressed: Displaying next plugin in playlist.")
        try:
            playlist_manager = device_config.get_playlist_manager()
            if not playlist_manager.active_playlist:
                logger.warning("No active playlist. Cannot advance.")
                return

            playlist = playlist_manager.get_playlist(playlist_manager.active_playlist)
            if not playlist or not playlist.plugins:
                logger.warning("Active playlist not found or is empty. Cannot advance.")
                return

            # get_next_plugin() advances the playlist's current_plugin_index
            plugin_instance = playlist.get_next_plugin()
            logger.info(f"Advancing to next plugin: {plugin_instance.name}")
            
            # Force a refresh to display the new plugin immediately (async)
            refresh_action = PlaylistRefresh(playlist, plugin_instance, force=True)

            def _do_next_refresh(action):
                try:
                    display_manager.display_overlay("Updating...")
                    refresh_task.manual_update(action)
                except Exception as e:
                    logger.error(f"Error during async next-plugin refresh: {e}", exc_info=True)

            threading.Thread(target=_do_next_refresh, args=(refresh_action,), daemon=True).start()

        except Exception as e:
            logger.error(f"Error during button-triggered next plugin: {e}", exc_info=True)

    def handle_led_toggle():
        """Toggles the LED strip power."""
        logger.info("Button pressed: Toggling LED strip.")
        try:
            led_controller.toggle()
        except Exception as e:
            logger.error(f"Error during button-triggered LED toggle: {e}", exc_info=True)

    try:
        # Button pins are based on your request (physical pins 31, 37, 36), which correspond
        # to BCM pins 6, 26, and 27. Assumes buttons connect GPIO to GND when pressed.
        button1 = Button(BUTTON_REFRESH_PIN, pull_up=True, bounce_time=0.1)   # Refresh
        button4 = Button(BUTTON_NEXT_PIN, pull_up=True, bounce_time=0.1)  # Next in playlist
        button_led = Button(BUTTON_LED_TOGGLE_PIN, pull_up=True, bounce_time=0.1)  # LED toggle

        button1.when_pressed = handle_refresh
        button4.when_pressed = handle_next_plugin
        button_led.when_pressed = handle_led_toggle
        
        logger.info(f"GPIO buttons configured successfully for pins BCM {BUTTON_REFRESH_PIN} (Refresh), BCM {BUTTON_NEXT_PIN} (Next Plugin), and BCM {BUTTON_LED_TOGGLE_PIN} (LED Toggle).")
        
        # Keep a reference to the button objects to prevent them from being garbage collected,
        # which would cause the callbacks to stop working.
        app.config['GPIO_BUTTONS'] = [button1, button4, button_led]

    except Exception as e:
        logger.error(f"Failed to initialize GPIO buttons: {e}")
        logger.error("This is expected if not running on a non-Raspberry Pi machine.")


display_manager = DisplayManager(device_config)
refresh_task = RefreshTask(device_config, display_manager)
led_controller = LEDStripController()

load_plugins(device_config.get_plugins())

# Store dependencies
app.config['DEVICE_CONFIG'] = device_config
app.config['DISPLAY_MANAGER'] = display_manager
app.config['REFRESH_TASK'] = refresh_task
app.config['LED_CONTROLLER'] = led_controller

# Set additional parameters
app.config['MAX_FORM_PARTS'] = 10_000

# Register Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(playlist_bp)

# Register opener for HEIF/HEIC images
register_heif_opener()

if __name__ == '__main__':

    # start the background refresh task
    refresh_task.start()

    # Set up GPIO buttons if not in development mode
    if not DEV_MODE:
        setup_buttons(refresh_task, device_config, led_controller)

    # display default inkypi image on startup
    if device_config.get_config("startup") is True:
        logger.info("Startup flag is set, displaying startup image")
        img = generate_startup_image(device_config.get_resolution())
        display_manager.display_image(img)
        device_config.update_value("startup", False, write=True)

    try:
        # Run the Flask app
        app.secret_key = str(random.randint(100000,999999))

        # Get local IP address for display (only in dev mode when running on non-Pi)
        if DEV_MODE:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                logger.info(f"Serving on http://{local_ip}:{PORT}")
            except:
                pass  # Ignore if we can't get the IP

        serve(app, host="0.0.0.0", port=PORT, threads=1)
    finally:
        refresh_task.stop()
        led_controller.cleanup()
