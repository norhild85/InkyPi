"""
LED Strip Controller

Controls a short LED strip connected to GPIO pins.
Handles toggling power on button press with initial state of OFF.
"""

import logging
import threading
from gpiozero import OutputDevice
from gpiozero.exc import BadPinFactory

logger = logging.getLogger(__name__)


class LEDStripController:
    """Controls an LED strip connected to GPIO pins."""
    
    # GPIO Pin Configuration (BCM numbering)
    LED_POWER_PIN = 3  # Physical pin 5 (positive/power)
    
    def __init__(self):
        """Initialize the LED strip controller with LED initially off."""
        self.led = None
        self.is_on = False
        self._lock = threading.Lock()
        self._initialize_led()
    
    def _initialize_led(self):
        """Initialize the GPIO pin for LED control."""
        try:
            # Create an OutputDevice for the LED (active_high=True means pin HIGH turns LED on)
            self.led = OutputDevice(self.LED_POWER_PIN, initial_value=False)
            self.is_on = False
            logger.info(f"LED strip controller initialized on BCM pin {self.LED_POWER_PIN}. LED is OFF.")
        except BadPinFactory as e:
            logger.warning(f"GPIO not available for LED control: {e}. LED controller will be disabled.")
            self.led = None
        except Exception as e:
            logger.error(f"Failed to initialize LED strip controller: {e}")
            self.led = None
    
    def toggle(self):
        """Toggle the LED strip power state."""
        if self.led is None:
            logger.warning("LED strip controller is not initialized. Cannot toggle.")
            return
        
        with self._lock:
            try:
                self.is_on = not self.is_on
                if self.is_on:
                    self.led.on()
                    logger.info("LED strip turned ON")
                else:
                    self.led.off()
                    logger.info("LED strip turned OFF")
            except Exception as e:
                logger.error(f"Error toggling LED strip: {e}")
    
    def turn_on(self):
        """Turn the LED strip on."""
        if self.led is None:
            logger.warning("LED strip controller is not initialized. Cannot turn on.")
            return
        
        with self._lock:
            try:
                if not self.is_on:
                    self.led.on()
                    self.is_on = True
                    logger.info("LED strip turned ON")
            except Exception as e:
                logger.error(f"Error turning on LED strip: {e}")
    
    def turn_off(self):
        """Turn the LED strip off."""
        if self.led is None:
            logger.warning("LED strip controller is not initialized. Cannot turn off.")
            return
        
        with self._lock:
            try:
                if self.is_on:
                    self.led.off()
                    self.is_on = False
                    logger.info("LED strip turned OFF")
            except Exception as e:
                logger.error(f"Error turning off LED strip: {e}")
    
    def get_status(self):
        """Get the current status of the LED strip."""
        with self._lock:
            return {"is_on": self.is_on}
    
    def cleanup(self):
        """Clean up GPIO resources."""
        if self.led is not None:
            try:
                self.led.close()
                logger.info("LED strip controller cleaned up.")
            except Exception as e:
                logger.error(f"Error cleaning up LED strip controller: {e}")
