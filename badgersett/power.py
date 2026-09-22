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


def vsys(samples=16):
    """Supply voltage in volts, or None if it cannot be read right now.

    On a Pico W, VSYS/3 is on ADC3 (GP29) - but GP29 doubles as the WiFi
    chip's SPI clock, and GP25 as its chip-select. Reading it therefore
    requires the radio to be off and GP25 driven high, which is only safe
    between network sessions. The CYW43 driver reclaims both pins the next
    time the radio is activated.

    On USB this reads roughly 4.8V. On battery it tracks the cell, less a
    diode drop, which is plenty for a discharge curve.
    """
    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        if wlan.active():
            return None           # would corrupt the radio's SPI bus
        machine.Pin(25, machine.Pin.OUT).value(1)
        machine.Pin(29, machine.Pin.IN)
        adc = machine.ADC(3)
        total = 0
        for _ in range(samples):
            total += adc.read_u16()
        return round(total / samples * 3 * 3.3 / 65535, 3)
    except Exception as exc:
        util.log("VSYS read failed:", exc)
        return None
