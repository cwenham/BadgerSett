"""US National Weather Service active-alert client.

api.weather.gov is free and needs no key, but an active-alert response
can run to hundreds of kilobytes of description text — far more than the
RP2040 can hold. So we never buffer the body: we pull it down in small
chunks and scan for just the four fields we care about, keeping an
overlap window so a key split across two chunks is still found.

Within each feature the NWS orders properties `...severity, certainty,
urgency, event, ...`, so an `event` marks the end of one record.
"""

import gc

try:
    import urequests as requests
except ImportError:
    import requests

from . import util

BASE = "https://api.weather.gov/alerts/active"
CHUNK = 512
OVERLAP = 96

SEVERITY_RANK = {"unknown": 0, "minor": 1, "moderate": 2, "severe": 3, "extreme": 4}


def _string_after(buffer, key, start=0):
    """Find `"key":"value"` and return (value, index_after) or (None, -1)."""
    marker = '"%s":' % key
    index = buffer.find(marker, start)
    if index < 0:
        return None, -1
    index += len(marker)
    while index < len(buffer) and buffer[index] in ' \t':
        index += 1
    if index >= len(buffer):
        return None, -1
    if buffer[index] == 'n':          # null
        return None, index
    if buffer[index] != '"':
        return None, index
    index += 1
    end = buffer.find('"', index)
    if end < 0:
        return None, -1
    return buffer[index:end], end + 1


def fetch(lat, lon, user_agent, min_severity="Severe", max_alerts=3):
    """Return a list of {event, severity, ends} dicts, most severe first."""
    url = "%s?point=%.4f,%.4f&status=actual&message_type=alert" % (BASE, lat, lon)
    floor = SEVERITY_RANK.get(str(min_severity).lower(), 3)
    util.log("GET", url)

    response = None
    alerts = []
    gc.collect()
    try:
        response = requests.get(url, headers={
            "User-Agent": user_agent,
            "Accept": "application/geo+json",
        })
        if response.status_code != 200:
            util.log("nws HTTP", response.status_code)
            return []

        stream = response.raw
        window = ""
        pending = {}
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            try:
                window += chunk.decode("utf-8")
            except Exception:
                window += "".join(chr(b) for b in chunk if b < 128)

            # Pull every complete record currently visible in the window.
            while True:
                severity, _ = _string_after(window, "severity")
                if severity is not None:
                    pending["severity"] = severity
                ends, _ = _string_after(window, "ends")
                if ends is not None:
                    pending["ends"] = ends
                event, after = _string_after(window, "event")
                if event is None:
                    break
                rank = SEVERITY_RANK.get(pending.get("severity", "").lower(), 0)
                if rank >= floor:
                    alerts.append({
                        "key": "nws:%s" % event,
                        "event": event,
                        "severity": pending.get("severity", "Unknown"),
                        # Extreme -> severe, Severe -> warning, rest -> notice
                        "level": 3 if rank >= 4 else (2 if rank >= 3 else 1),
                        "rank": rank,
                        "ends": pending.get("ends"),
                    })
                pending = {}
                window = window[after:]

            if len(window) > CHUNK + OVERLAP:
                window = window[-OVERLAP:]
            if len(alerts) >= max_alerts:
                break
    except Exception as exc:
        util.log("nws failed:", exc)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        gc.collect()

    alerts.sort(key=lambda a: -a["rank"])
    return alerts[:max_alerts]
