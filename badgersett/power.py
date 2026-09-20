"""Power-source detection.

On a Pico W the VBUS sense line is not GP24 as it is on the plain Pico -
that pin is repurposed - it hangs off the WiFi chip instead, as
`WL_GPIO2`. Reading it needs the CYW43 driver initialised, which it is
by the time MicroPython gives us a prompt.
"""

import machine

from . import util

_USB_PIN = "WL_GPIO2"


def on_usb():
    """True when running from USB, False on battery.

    Returns True if the check itself fails: the callers use this to decide
    whether to spend power, and on an unknown supply the safer assumption
    is the one that does not silently disable a feature the user can see.
    """
    try:
        return bool(machine.Pin(_USB_PIN, machine.Pin.IN).value())
    except Exception as exc:
        util.log("VBUS sense unavailable (%s); assuming USB" % exc)
        return True
