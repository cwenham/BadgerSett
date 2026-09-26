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


def networks_from_config(config):
    """Normalise the config into an ordered list of candidate networks.

    Order is preference order: earlier entries win when several are in
    range. Each carries the location it implies, so moving between a
    home and an office network also moves the forecast, the warnings
    region and the altitude used for pressure.

    A single WIFI_SSID/WIFI_PASSWORD still works and becomes a one-entry
    list using the top-level location settings.
    """
    defaults = {
        "lat": getattr(config, "LATITUDE", 0.0),
        "lon": getattr(config, "LONGITUDE", 0.0),
        "met_region": getattr(config, "MET_REGION", "se"),
        "altitude": getattr(config, "ALTITUDE_M", 0),
    }

    entries = getattr(config, "WIFI_NETWORKS", None)
    if not entries:
        ssid = getattr(config, "WIFI_SSID", "")
        if not ssid:
            return []
        entries = [{"ssid": ssid, "password": getattr(config, "WIFI_PASSWORD", "")}]

    out = []
    for entry in entries:
        try:
            ssid = entry.get("ssid")
        except AttributeError:
            util.log("ignoring malformed WIFI_NETWORKS entry:", entry)
            continue
        if not ssid:
            continue
        out.append({
            "ssid": ssid,
            "password": entry.get("password", ""),
            "label": entry.get("label") or ssid,
            "lat": entry.get("lat", defaults["lat"]),
            "lon": entry.get("lon", defaults["lon"]),
            "met_region": entry.get("met_region", defaults["met_region"]),
            "altitude": entry.get("altitude", defaults["altitude"]),
        })
    return out


def scan_ssids(wlan):
    """Every SSID currently in range, as {ssid: strongest_rssi}.

    Several APs may share one SSID, so the strongest wins. Hidden
    networks broadcast an empty SSID and are skipped here - they are
    still connectable by name, which connect_best() falls back to.
    """
    best = {}
    try:
        for entry in wlan.scan():
            ssid = entry[0]
            if isinstance(ssid, bytes):
                ssid = ssid.decode("utf-8", "ignore")
            ssid = (ssid or "").strip()
            if not ssid:
                continue
            rssi = entry[3]
            if ssid not in best or rssi > best[ssid]:
                best[ssid] = rssi
    except Exception as exc:
        util.log("wifi scan failed:", exc)
    return best


def select_networks(networks, visible):
    """Order the configured networks by how worth trying they are.

    Config order is the preference, so the first configured network that
    is in range wins regardless of signal strength - a weak but preferred
    network beats a strong one further down the list. Networks that are
    not visible go last rather than being dropped, so a hidden SSID is
    still attempted.
    """
    seen, unseen = [], []
    for entry in networks:
        (seen if entry["ssid"] in visible else unseen).append(entry)
    return seen + unseen


def connect_best(networks, country="GB", timeout=25, max_attempts=3):
    """Scan, then connect to the most-preferred network in range.

    Returns (ifconfig, network) or (None, None).
    """
    if not networks:
        util.log("no wifi networks configured")
        return None, None

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        network.country(country)
    except Exception:
        pass

    visible = scan_ssids(wlan)
    util.log("%d SSIDs in range" % len(visible))
    ordered = select_networks(networks, visible)

    for index, entry in enumerate(ordered[:max_attempts]):
        where = "in range, %d dBm" % visible[entry["ssid"]] if entry["ssid"] in visible \
            else "not seen in scan; trying anyway"
        util.log("wifi candidate %d: %s (%s)" % (index + 1, entry["ssid"], where))
        config = connect(entry["ssid"], entry["password"], country, timeout)
        if config:
            util.log("connected at %s" % entry["label"])
            return config, entry

    util.log("no configured network could be joined")
    return None, None


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


# WL_REG_ON, the CYW43's regulator enable, is GP23 on a Pico W. Reading
# the SIO input register is the ground truth for whether the wireless chip
# is drawing power, and unlike constructing a Pin object it cannot change
# the state by being asked.
_SIO_GPIO_IN = 0xd0000004
_WL_REG_ON = 23


def radio_powered():
    """True if the CYW43 currently has power.

    Falls back to the interface's own idea of being active on anything
    that is not a Pico W, where GP23 means something else entirely.
    """
    try:
        import machine
        return bool((machine.mem32[_SIO_GPIO_IN] >> _WL_REG_ON) & 1)
    except Exception:
        pass
    try:
        return bool(network.WLAN(network.STA_IF).active())
    except Exception:
        return None


def radio_off():
    """Cut power to the CYW43.

    active(False) is NOT enough. Measured on the badge, WL_REG_ON stays
    high after active(False) and only drops on deinit() - so a radio that
    looks inactive goes on idling at several mA, which on battery is the
    same order as everything else the badge does put together.
    """
    try:
        wlan = network.WLAN(network.STA_IF)
        try:
            wlan.active(False)
        except Exception:
            pass
        wlan.deinit()
    except Exception as exc:
        util.log("radio power-down:", exc)


def disconnect(wlan=None):
    """Drop the link and power down the radio — it is the biggest draw."""
    try:
        wlan = wlan or network.WLAN(network.STA_IF)
        if wlan.isconnected():
            wlan.disconnect()
        wlan.active(False)
        # deinit() is what actually drops WL_REG_ON; active(False) alone
        # leaves the chip powered. Not optional.
        try:
            wlan.deinit()
        except Exception:
            pass
    except Exception as exc:
        util.log("wifi teardown:", exc)
