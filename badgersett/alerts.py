"""Alert rules and the decision about when to buzz.

Levels: 1 = notice, 2 = warning, 3 = severe.

Two independent sources feed the forecast side. Official government
warnings (currently the US NWS) are authoritative and used verbatim.
Threshold rules over the Open-Meteo forecast work anywhere in the world
and cover the case where no official feed exists.

Alerts are keyed so that re-running every half hour does not buzz you
every half hour: the haptics only fire for a key that is new, or whose
level has gone up since last time.
"""

from . import util

# WMO codes that are worth a warning on their own.
_CODE_ALERTS = {
    95: (2, "Thunderstorms forecast"),
    96: (3, "Thunderstorms with hail"),
    99: (3, "Severe storms with hail"),
    56: (2, "Freezing drizzle - ice"),
    57: (2, "Freezing drizzle - ice"),
    66: (3, "Freezing rain - ice"),
    67: (3, "Freezing rain - ice"),
    65: (2, "Heavy rain expected"),
    82: (2, "Violent rain showers"),
    75: (2, "Heavy snow expected"),
    86: (2, "Heavy snow showers"),
}


def _forecast_alerts(config, weather):
    found = []
    if not weather:
        return found

    for code in (weather.get("code"), weather.get("code_today"), weather.get("code2")):
        if code in _CODE_ALERTS:
            level, text = _CODE_ALERTS[code]
            found.append({"key": "wx:code:%d" % code, "level": level,
                          "text": text, "source": "forecast"})
            break

    gust = max(weather.get("gust_max") or 0, weather.get("gust") or 0)
    severe = getattr(config, "GUST_SEVERE", 90)
    warn = getattr(config, "GUST_WARN", 60)
    if gust >= severe:
        found.append({"key": "wx:gust", "level": 3,
                      "text": "Damaging gusts %d %s" % (round(gust), util.speed_unit()),
                      "source": "forecast"})
    elif gust >= warn:
        found.append({"key": "wx:gust", "level": 2,
                      "text": "Strong gusts %d %s" % (round(gust), util.speed_unit()),
                      "source": "forecast"})

    hi = weather.get("hi")
    if hi is not None and hi >= getattr(config, "HEAT_WARN", 32):
        found.append({"key": "wx:heat", "level": 2,
                      "text": "Heat: high of %d%s" % (round(hi), util.temp_unit()),
                      "source": "forecast"})
    lo = weather.get("lo")
    if lo is not None and lo <= getattr(config, "COLD_WARN", -5):
        found.append({"key": "wx:cold", "level": 2,
                      "text": "Hard freeze: low %d%s" % (round(lo), util.temp_unit()),
                      "source": "forecast"})
    return found


def _official_alerts(official):
    """Warnings from an official agency, used verbatim.

    Providers (metoffice.py, nws.py) return a common shape, so adding
    another country's service means writing one module, not touching
    these rules.
    """
    found = []
    for item in official or []:
        level = item.get("level")
        if level is None:                       # older provider shape
            rank = item.get("rank", 0)
            level = 3 if rank >= 4 else (2 if rank >= 3 else 1)
        found.append({"key": item.get("key") or ("official:%s" % item["event"]),
                      "level": level, "text": item["event"], "source": "official"})
    return found


def _indoor_alerts(config, indoor, state):
    found = []
    if not indoor:
        return found

    temp = indoor.get("temp")
    if temp is not None:
        if temp >= getattr(config, "TEMP_HIGH_WARN", 30):
            found.append({"key": "in:temp_high", "level": 1,
                          "text": "Indoor %d%s - it is hot in here" % (
                              round(temp), util.temp_unit()), "source": "indoor"})
        elif temp <= getattr(config, "TEMP_LOW_WARN", 15):
            found.append({"key": "in:temp_low", "level": 1,
                          "text": "Indoor %d%s - cold" % (round(temp), util.temp_unit()),
                          "source": "indoor"})

    humidity = indoor.get("humidity")
    if humidity is not None:
        if humidity >= getattr(config, "HUMIDITY_HIGH_WARN", 70):
            found.append({"key": "in:rh_high", "level": 1,
                          "text": "Humidity %d%% - damp/mould risk" % round(humidity),
                          "source": "indoor"})
        elif humidity <= getattr(config, "HUMIDITY_LOW_WARN", 25):
            found.append({"key": "in:rh_low", "level": 1,
                          "text": "Humidity %d%% - very dry" % round(humidity),
                          "source": "indoor"})

    # Gas: relative to the learned baseline, and only when the heater settled.
    if getattr(config, "GAS_ALERTS", True) and indoor.get("stable"):
        gas = indoor.get("gas")
        baseline = state.get("gas_baseline")
        if gas and baseline and state.get("gas_n", 0) >= 5:
            ratio = gas / baseline
            if ratio <= getattr(config, "GAS_DROP_SEVERE", 0.35):
                found.append({"key": "in:gas", "level": 3,
                              "text": "Air quality dropped sharply (%d%% of normal)" % round(ratio * 100),
                              "source": "indoor"})
            elif ratio <= getattr(config, "GAS_DROP_WARN", 0.60):
                found.append({"key": "in:gas", "level": 2,
                              "text": "VOCs elevated (%d%% of normal)" % round(ratio * 100),
                              "source": "indoor"})

    trend = state.pressure_trend()
    if trend is not None and trend <= -abs(getattr(config, "PRESSURE_DROP_WARN", 3.0)):
        found.append({"key": "in:pressure", "level": 2,
                      "text": "Pressure falling %.1f hPa/3h - weather turning" % trend,
                      "source": "indoor"})
    return found


def evaluate(config, weather, indoor, state, official=None):
    """Return all current alerts, most severe first."""
    found = []
    found.extend(_official_alerts(official))
    found.extend(_forecast_alerts(config, weather))
    found.extend(_indoor_alerts(config, indoor, state))

    # Collapse duplicate keys, keeping the highest level.
    best = {}
    for alert in found:
        existing = best.get(alert["key"])
        if not existing or alert["level"] > existing["level"]:
            best[alert["key"]] = alert
    result = list(best.values())
    result.sort(key=lambda a: -a["level"])
    return result


def notify(config, alerts, state, haptic, hour=None):
    """Buzz for anything new or escalated. Returns the level played, or 0."""
    previous = state.get("alerts") or {}
    current = {}
    highest_new = 0

    for alert in alerts:
        key, level = alert["key"], alert["level"]
        current[key] = level
        if level > previous.get(key, 0):
            highest_new = max(highest_new, level)

    state.set("alerts", current)

    if not highest_new:
        return 0
    if state.get("muted"):
        util.log("alert suppressed: muted")
        return 0
    if not getattr(config, "HAPTIC_ENABLED", True) or not haptic or not haptic.ready:
        return 0
    if hour is not None and util.in_quiet_hours(hour, getattr(config, "HAPTIC_QUIET_HOURS", None)):
        # Severe warnings still get through the quiet window.
        if highest_new < 3:
            util.log("alert suppressed: quiet hours")
            return 0

    util.log("buzzing at level", highest_new)
    haptic.alert(highest_new)
    return highest_new
