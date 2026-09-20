"""Open-Meteo forecast client.

Open-Meteo needs no API key and works worldwide. We request only the
fields we draw, which keeps the response near 1KB — this matters a lot
on an RP2040, where a careless JSON parse can exhaust the heap. Plain
HTTP is deliberate: it avoids a TLS handshake's ~40KB of buffers.
"""

import gc

try:
    import urequests as requests
except ImportError:
    import requests

from . import util

BASE = "http://api.open-meteo.com/v1/forecast"

_CURRENT = ("temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
            "precipitation,weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m")
_DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,"
          "precipitation_probability_max,wind_gusts_10m_max")

# WMO 4677 weather codes -> (short label, icon key)
WMO = {
    0: ("Clear", "sun"),
    1: ("Mainly clear", "partly"),
    2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Rime fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "heavy_rain"),
    66: ("Freezing rain", "sleet"),
    67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Violent showers", "heavy_rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Storm + hail", "storm"),
    99: ("Storm + hail", "storm"),
}


def describe(code):
    return WMO.get(code, ("Unknown", "cloud"))


def _url(lat, lon, timezone):
    parts = [
        BASE,
        "?latitude=", "%.4f" % lat,
        "&longitude=", "%.4f" % lon,
        "&current=", _CURRENT,
        "&daily=", _DAILY,
        "&forecast_days=2",
        "&timezone=", timezone,
    ]
    if util.imperial():
        parts.append("&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch")
    return "".join(parts)


def fetch(lat, lon, timezone="auto"):
    """Return a flat dict of everything we display, or None on failure."""
    url = _url(lat, lon, timezone)
    util.log("GET", url)
    response = None
    gc.collect()
    try:
        response = requests.get(url)
        if response.status_code != 200:
            util.log("open-meteo HTTP", response.status_code)
            return None
        payload = response.json()
    except Exception as exc:
        util.log("open-meteo failed:", exc)
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        gc.collect()

    try:
        current = payload["current"]
        daily = payload["daily"]
        result = {
            "temp": current.get("temperature_2m"),
            "feels": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "precip": current.get("precipitation"),
            "code": current.get("weather_code"),
            "wind": current.get("wind_speed_10m"),
            "gust": current.get("wind_gusts_10m"),
            "dir": current.get("wind_direction_10m"),
            "is_day": bool(current.get("is_day", 1)),
            "time": current.get("time"),
            "utc_offset": payload.get("utc_offset_seconds", 0),
            "hi": daily["temperature_2m_max"][0],
            "lo": daily["temperature_2m_min"][0],
            "pop": daily["precipitation_probability_max"][0],
            "gust_max": daily["wind_gusts_10m_max"][0],
            "code_today": daily["weather_code"][0],
        }
        if len(daily["time"]) > 1:
            result["hi2"] = daily["temperature_2m_max"][1]
            result["lo2"] = daily["temperature_2m_min"][1]
            result["pop2"] = daily["precipitation_probability_max"][1]
            result["code2"] = daily["weather_code"][1]
            result["gust_max2"] = daily["wind_gusts_10m_max"][1]
    except (KeyError, IndexError, TypeError) as exc:
        util.log("open-meteo shape unexpected:", exc)
        return None
    finally:
        payload = None
        gc.collect()

    return result
