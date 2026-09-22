"""Radio survey for the secret screen: WiFi access points and BLE devices.

Both radios live on the same CYW43439, so the two scans run one after the
other, each shutting down before the next starts. A full survey costs
about half a second of WiFi and however long `ble_ms` allows - call it
seven seconds - which is why it happens on request rather than on a timer.

Results are cached in their own file rather than in state.json: they are
several kilobytes, and state.json is written on every wake.
"""

import gc
import json
import time

from . import power, util

PATH = "scan.json"
MAX_ENTRIES = 90          # bounds both the file and the memory it parses back

# Bluetooth SIG company identifiers, enough to label what a home tends to
# hear. Anything else is shown as its raw ID.
COMPANIES = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
    0x0087: "Garmin", 0x0157: "Huami", 0x038F: "Xiaomi", 0x0059: "Nordic",
    0x02E5: "Espressif", 0x0171: "Amazon", 0x0499: "Ruuvi", 0x0001: "Ericsson",
    0x000F: "Broadcom", 0x0131: "Cypress", 0x07D0: "Tuya", 0x0110: "Sonos",
}

SECURITY = {0: "open", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA/2", 5: "WPA3"}


def _adv_fields(adv):
    """Split a BLE advertising payload into {type: value}."""
    fields, i = {}, 0
    while i + 1 < len(adv):
        length = adv[i]
        if length == 0:
            break
        fields.setdefault(adv[i + 1], bytes(adv[i + 2:i + 1 + length]))
        i += 1 + length
    return fields


def _addr_kind(addr_type, addr):
    """How identifiable a BLE address is.

    Public addresses are permanent. Random ones carry their type in the
    top two bits: 0b11 static (stable until reboot), 0b01 resolvable (a
    phone rotating every ~15 minutes), 0b00 non-resolvable.
    """
    if addr_type == 0:
        return "public"
    return {3: "static", 1: "rotating", 0: "private"}.get(addr[0] >> 6, "random")


def wifi_scan():
    """Visible access points, strongest first, one entry per SSID."""
    entries = {}
    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        was_active = wlan.active()
        wlan.active(True)
        for result in wlan.scan():
            ssid, _bssid, channel, rssi, security = result[0], result[1], result[2], result[3], result[4]
            if isinstance(ssid, bytes):
                ssid = ssid.decode("utf-8", "ignore")
            ssid = (ssid or "").strip() or "(hidden)"
            current = entries.get(ssid)
            if current is None or rssi > current["r"]:
                entries[ssid] = {"k": "W", "n": ssid, "r": rssi, "i": ssid,
                                 "x": "ch%d %s" % (channel, SECURITY.get(security, "?"))}
        if not was_active:
            wlan.active(False)
    except Exception as exc:
        util.log("wifi scan failed:", exc)
    return list(entries.values())


def ble_scan(duration_ms=6000):
    """BLE advertisers heard in `duration_ms`, one entry per address."""
    found = {}
    ble = None
    try:
        import bluetooth
    except ImportError:
        util.log("no bluetooth module in this firmware")
        return []

    try:
        power.RADIO_BUSY = True      # keep vsys() off the shared SPI bus
        ble = bluetooth.BLE()
        ble.active(True)
        done = [False]

        def on_event(event, data):
            if event == 5:                     # _IRQ_SCAN_RESULT
                addr_type, addr, _adv_type, rssi, adv = data
                key = bytes(addr)
                entry = found.get(key)
                if entry is None:
                    entry = {"k": "B", "n": None, "r": rssi,
                             "x": _addr_kind(addr_type, key), "co": None}
                    found[key] = entry
                if rssi > entry["r"]:
                    entry["r"] = rssi
                fields = _adv_fields(bytes(adv))
                name = fields.get(0x09) or fields.get(0x08)
                if name and not entry["n"]:
                    try:
                        entry["n"] = name.decode("utf-8", "ignore").strip()
                    except Exception:
                        pass
                maker = fields.get(0xFF)
                if maker and len(maker) >= 2 and not entry["co"]:
                    code = maker[0] | (maker[1] << 8)
                    entry["co"] = COMPANIES.get(code, "0x%04X" % code)
            elif event == 6:                   # _IRQ_SCAN_DONE
                done[0] = True

        ble.irq(on_event)
        # Active scan: sends scan requests, so devices reply with names.
        ble.gap_scan(duration_ms, 30000, 30000, True)
        deadline = time.ticks_add(time.ticks_ms(), duration_ms + 2000)
        while not done[0] and time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(100)
    except Exception as exc:
        util.log("ble scan failed:", exc)
    finally:
        if ble is not None:
            try:
                ble.active(False)
            except Exception:
                pass
        power.RADIO_BUSY = False
        gc.collect()

    entries = []
    for addr, entry in found.items():
        label = entry["n"] or entry["co"] or "%02x:%02x:%02x" % (addr[3], addr[4], addr[5])
        detail = entry["x"]
        if entry["n"] and entry["co"]:
            detail = "%s %s" % (entry["co"], entry["x"])
        # "i" identifies the device for the radar's bearing. It must be the
        # address, not the label: a dozen devices all called "Apple" would
        # otherwise hash to one bearing and stack up in a line.
        entries.append({"k": "B", "n": label, "r": entry["r"], "x": detail,
                        "i": "%02x%02x%02x" % (addr[3], addr[4], addr[5])})
    return entries


def survey(ble_ms=6000, include_ble=True):
    """Scan both radios and return one list, strongest first."""
    gc.collect()
    entries = wifi_scan()
    wifi_count = len(entries)
    if include_ble:
        entries.extend(ble_scan(ble_ms))
    entries.sort(key=lambda e: -e["r"])
    del entries[MAX_ENTRIES:]
    util.log("survey: %d wifi, %d bluetooth" % (wifi_count, len(entries) - wifi_count))
    gc.collect()
    return entries


def save(entries, stamp):
    try:
        with open(PATH, "w") as handle:
            json.dump({"t": stamp, "e": entries}, handle)
    except Exception as exc:
        util.log("could not save scan:", exc)


def load():
    """Return (entries, stamp) from the cache, or ([], None)."""
    try:
        with open(PATH) as handle:
            data = json.load(handle)
        return data.get("e") or [], data.get("t")
    except Exception:
        return [], None
