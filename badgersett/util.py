"""Small helpers shared across BadgerSett."""

try:
    import config
except ImportError:  # pragma: no cover - only on a badge with no config yet
    raise SystemExit("No config.py found. Copy config.example.py to config.py.")


def log(*args):
    if getattr(config, "DEBUG", False):
        print("[badgersett]", *args)


def imperial():
    return getattr(config, "UNITS", "metric").lower() == "imperial"


def temp_unit():
    return "F" if imperial() else "C"


def speed_unit():
    return "mph" if imperial() else "km/h"


def c_to_display(celsius):
    """BME688 always reports Celsius; convert for display if needed."""
    if celsius is None:
        return None
    return celsius * 9.0 / 5.0 + 32.0 if imperial() else celsius


def clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def dew_point(temp_c, humidity):
    """Magnus-Tetens approximation, returns Celsius."""
    if temp_c is None or humidity is None or humidity <= 0:
        return None
    a, b = 17.62, 243.12
    # math.log is available in MicroPython
    import math
    gamma = (a * temp_c) / (b + temp_c) + math.log(humidity / 100.0)
    return (b * gamma) / (a - gamma)


def sea_level_pressure(pressure_pa, altitude_m, temp_c=15.0):
    """Convert absolute pressure (Pa) to sea-level equivalent (hPa)."""
    if pressure_pa is None:
        return None
    hpa = pressure_pa / 100.0
    if not altitude_m:
        return hpa
    return hpa * pow(1.0 - (0.0065 * altitude_m) / (temp_c + 0.0065 * altitude_m + 273.15), -5.257)


def bearing(degrees):
    dirs = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return dirs[int(round(degrees / 22.5)) % 16]


def truncate(display, text, max_width, scale=1):
    """Trim text with an ellipsis until it fits max_width pixels."""
    if display.measure_text(text, scale) <= max_width:
        return text
    while text and display.measure_text(text + "..", scale) > max_width:
        text = text[:-1]
    return text + ".."


def in_quiet_hours(hour, window):
    if not window:
        return False
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end   # window wraps past midnight


def parse_iso(text):
    """Parse '2026-09-20T17:15' into a tuple; returns None on anything odd."""
    try:
        date, _, clock = text.partition("T")
        y, m, d = (int(p) for p in date.split("-"))
        parts = clock.split(":")
        hh, mm = int(parts[0]), int(parts[1])
        return (y, m, d, hh, mm)
    except Exception:
        return None
