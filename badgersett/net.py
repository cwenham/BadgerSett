"""WiFi bring-up and teardown.

Uses MicroPython's `network` module directly rather than Badger OS's
`display.connect()`, so every setting lives in one config.py.
"""

import time

import network

from . import util

_STATUS_TEXT = {
    getattr(network, "STAT_WRONG_PASSWORD", -3): "wrong password",
    getattr(network, "STAT_NO_AP_FOUND", -2): "no such network",
    getattr(network, "STAT_CONNECT_FAIL", -1): "connect failed",
}


def connect(ssid, password, country="GB", timeout=25):
    """Bring up the station interface. Returns an ifconfig tuple or None."""
    try:
        network.country(country)
    except Exception:
        pass   # older firmware lacks network.country

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        wlan.config(pm=0xA11140)   # disable WiFi power save: faster, steadier
    except Exception:
        pass

    if not wlan.isconnected():
        util.log("connecting to", ssid)
        wlan.connect(ssid, password)

    deadline = time.ticks_add(time.ticks_ms(), int(timeout * 1000))
    while not wlan.isconnected():
        status = wlan.status()
        if status in _STATUS_TEXT:
            util.log("wifi failed:", _STATUS_TEXT[status])
            disconnect(wlan)
            return None
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            util.log("wifi timed out")
            disconnect(wlan)
            return None
        time.sleep(0.25)

    config = wlan.ifconfig()
    util.log("wifi up:", config[0])
    return config


def disconnect(wlan=None):
    """Drop the link and power down the radio — it is the biggest draw."""
    try:
        wlan = wlan or network.WLAN(network.STA_IF)
        if wlan.isconnected():
            wlan.disconnect()
        wlan.active(False)
        try:
            wlan.deinit()
        except Exception:
            pass
    except Exception as exc:
        util.log("wifi teardown:", exc)
