"""Power-source detection.

On a Pico W the VBUS sense line is not GP24 as it is on the plain Pico -
that pin is repurposed - it hangs off the WiFi chip instead, as
`WL_GPIO2`. Reading it needs the CYW43 driver initialised, which it is
by the time MicroPython gives us a prompt.
"""

import machine

from . import util

_USB_PIN = "WL_GPIO2"

# Set while Bluetooth is scanning. vsys() must not touch the radio's SPI
# pins during that, and must not ask the bluetooth module either: building
# a BLE object to answer the question pokes the very chip we are trying
# not to disturb. A plain flag costs nothing and cannot misbehave.
RADIO_BUSY = False

# USB puts VSYS at about 4.8V. A LiPo never exceeds ~4.2V and two AAAs sit
# near 3V, so anything above this is USB. (Three fresh alkaline AAs can
# reach 4.8V and would be mistaken for USB - an unusual way to run this.)
USB_VSYS_V = 4.5


def radio_powered():
    """Whether the CYW43 has power. See net.radio_powered()."""
    try:
        from . import net
        return net.radio_powered()
    except Exception:
        return None


def radio_off():
    """Cut power to the CYW43. See net.radio_off()."""
    try:
        from . import net
        net.radio_off()
    except Exception as exc:
        util.log("radio power-down:", exc)


def on_usb():
    """True when running from USB, False on battery.

    WL_GPIO2 is the Pico W's VBUS sense. Testing on this board showed it
    reads correctly even after the radio has been shut down, so it is the
    primary signal: it is a single pin read that disturbs nothing. VSYS is
    the fallback, since taking the radio's SPI pins is the more invasive
    of the two.

    Nothing safety-critical depends on this: the low-battery cutoff reads
    the voltage directly. This only decides whether "auto" mode stays
    awake, so a wrong answer costs power, not a battery.
    """
    # WL_GPIO2 is a pin on the CYW43, not on the RP2040. Reading it powers
    # the wireless chip up - and leaves it up, drawing current until
    # something deinits it. An earlier version used it first because it
    # "disturbs nothing"; measured on the board, it disturbs the most
    # expensive thing there is. So it is only free when the radio is
    # already running, and VSYS answers the same question when it is not.
    if radio_powered():
        try:
            return bool(machine.Pin(_USB_PIN, machine.Pin.IN).value())
        except Exception as exc:
            util.log("VBUS sense unavailable (%s); trying VSYS" % exc)
    volts = vsys()
    if volts is not None:
        return volts > USB_VSYS_V
    # VSYS is blocked (the radio holds the shared SPI pins) and the radio
    # is not up to be asked. Power it just long enough for the read, then
    # put it back exactly as we found it.
    was_powered = radio_powered()
    try:
        return bool(machine.Pin(_USB_PIN, machine.Pin.IN).value())
    except Exception as exc:
        util.log("VBUS sense unavailable (%s); assuming USB" % exc)
        return True
    finally:
        if not was_powered:
            radio_off()


def vsys(samples=16):
    """Supply voltage in volts, or None if it cannot be read right now.

    On a Pico W, VSYS/3 is on ADC3 (GP29) - but GP29 doubles as the radio
    chip's SPI clock, and GP25 as its chip-select. Reading it therefore
    requires the radio to be off and GP25 driven high, which is only safe
    between radio sessions. The CYW43 driver reclaims both pins the next
    time it is activated.

    "The radio" means BOTH WiFi and Bluetooth: they share one CYW43439 and
    one SPI bus. Taking those pins while either is up can leave the driver
    waiting on a chip that never answers - a lockup below Python, where
    even Ctrl-C cannot reach.

    On USB this reads roughly 4.8V. On battery it tracks the cell, less a
    diode drop, which is plenty for a discharge curve.
    """
    try:
        import network
        if network.WLAN(network.STA_IF).active():
            return None           # would corrupt the radio's SPI bus
        if RADIO_BUSY:
            return None           # Bluetooth is using the same chip and bus
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
