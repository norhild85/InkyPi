import fnmatch
import json
import logging
import os

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from display.mock_display import MockDisplay
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Try to import hardware displays, but don't fail if they're not available
try:
    from display.inky_display import InkyDisplay
except ImportError:
    logger.info("Inky display not available, hardware support disabled")

try:
    from display.waveshare_display import WaveshareDisplay
except ImportError:
    logger.info("Waveshare display not available, hardware support disabled")

class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config):

        """
        Initializes the display manager and selects the correct display type 
        based on the configuration.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """
        
        self.device_config = device_config
     
        display_type = device_config.get_config("display_type", default="inky")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "inky":
            self.display = InkyDisplay(device_config)
        elif fnmatch.fnmatch(display_type, "epd*in*"):  
            # derived from waveshare epd - we assume here that will be consistent
            # otherwise we will have to enshring the manufacturer in the 
            # display_type and then have a display_model parameter.  Will leave
            # that for future use if the need arises.
            #
            # see https://github.com/waveshareteam/e-Paper
            self.display = WaveshareDisplay(device_config)
        else:
            raise ValueError(f"Unsupported display type: {display_type}")

    def display_image(self, image, image_settings=[]):
        
        """
        Delegates image rendering to the appropriate display instance.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): List of settings to modify image rendering.

        Raises:
            ValueError: If no valid display instance is found.
        """

        if not hasattr(self, "display"):
            raise ValueError("No valid display instance initialized.")
        
        # Save the image
        logger.info(f"Saving image to {self.device_config.current_image_file}")
        image.save(self.device_config.current_image_file)

        # Resize and adjust orientation
        image = change_orientation(image, self.device_config.get_config("orientation"))
        image = resize_image(image, self.device_config.get_resolution(), image_settings)
        if self.device_config.get_config("inverted_image"): image = image.rotate(180)
        image = apply_image_enhancement(image, self.device_config.get_config("image_settings"))

        # Pass to the concrete instance to render to the device.
        self.display.display_image(image, image_settings)

    def display_overlay(self, text="Updating...", position=("right", "bottom")):
        """Render a small overlay (text) on the currently displayed image and show it.

        This is intended as a lightweight visual indicator while a longer refresh runs.
        It composes text onto the last-saved image (`device_config.current_image_file`) and
        sends that image directly to the concrete display (bypassing resize/orientation pipeline).
        """
        try:
            # load last saved image if available, otherwise create blank
            if os.path.exists(self.device_config.current_image_file):
                base = Image.open(self.device_config.current_image_file).convert("RGB")
            else:
                base = Image.new("RGB", tuple(self.device_config.get_resolution()), (255, 255, 255))

            draw = ImageDraw.Draw(base)

            # use a larger, more visible default font
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None

            # compute text bounding box using getbbox (modern PIL) or fallback
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except Exception:
                # fallback: estimate based on character count
                text_w = len(text) * 8
                text_h = 14

            padding = 12
            box_w = text_w + padding * 2
            box_h = text_h + padding * 2

            # compute position
            if position[0] == "right":
                x = base.width - box_w - 15
            else:
                x = 15
            if position[1] == "bottom":
                y = base.height - box_h - 15
            else:
                y = 15

            # solid black rectangle for visibility
            draw.rectangle([x, y, x + box_w, y + box_h], fill=(0, 0, 0), outline=(255, 255, 255), width=2)
            # white text
            draw.text((x + padding, y + padding), text, fill=(255, 255, 255), font=font)

            # send directly to concrete display (no resizing/orientation)
            self.display.display_image(base, [])
        except Exception as e:
            logger.exception(f"Failed to render overlay: {e}")