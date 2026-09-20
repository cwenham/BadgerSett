"""BadgerSett main loop.

The badge spends nearly all its life powered down. Each wake follows the
same arc: work out why we woke, read the sensor, refresh from the network
if this was a timed wake, decide whether anything deserves a buzz, draw,
then set the RTC alarm and cut power.

On USB power `sleep_for()` cannot actually cut power, so it blocks until
a button or the alarm fires and then returns — which is why everything
lives inside a `while True` rather than relying on a reset to loop.
"""

import gc
import time

import badger2040
import machine

import config

from . import alerts as alert_rules
from . import metoffice, net, news, nws, sensor, ui, util, weather as weather_api
from .haptics import Haptics
from .sensor import Sensor
from .state import State

_BUTTONS = (
    (badger2040.BUTTON_A, "A"),
    (badger2040.BUTTON_B, "B"),
    (badger2040.BUTTON_C, "C"),
    (badger2040.BUTTON_UP, "UP"),
    (badger2040.BUTTON_DOWN, "DOWN"),
)


# Holding A and C while pressing UP. Reserved for a screen that does not
# exist yet; detected here so the chord never falls through to plain A.
SECRET_CHORD = ("A", "C", "UP")


def _is_secret_chord(buttons):
    return all(name in buttons for name in SECRET_CHORD)


def _secret_action(state, haptic):
    """Reserved. Deliberately does nothing yet.

    Put the fourth screen here when you decide what it should show: set
    a view, or act and leave the current view alone as it does now.
    """
    util.log("secret chord (A+C+UP) detected - reserved, no action")
    return None


def _wake_buttons(display):
    """Which buttons are responsible for this wake, on battery or USB."""
    pressed = []
    for pin, name in _BUTTONS:
        hit = False
        try:
            hit = badger2040.pressed_to_wake(pin)
        except Exception:
            pass
        if not hit:
            try:
                hit = display.pressed(pin)
            except Exception:
                pass
        if hit:
            pressed.append(name)
    return pressed


def _set_clock(weather):
    """Seed both RTCs from the forecast timestamp — no NTP round trip needed."""
    parsed = util.parse_iso(weather.get("time") or "")
    if not parsed:
        return
    year, month, day, hour, minute = parsed
    try:
        machine.RTC().datetime((year, month, day, 0, hour, minute, 0, 0))
        badger2040.pico_rtc_to_pcf()
        util.log("clock set to %04d-%02d-%02d %02d:%02d" % parsed)
    except Exception as exc:
        util.log("clock set failed:", exc)


def _now():
    try:
        stamp = machine.RTC().datetime()
        return stamp[4], stamp[5], stamp   # hour, minute, full tuple
    except Exception:
        return None, None, None


def _minutes_stamp():
    """A monotonic-ish minute counter for the pressure history."""
    try:
        stamp = machine.RTC().datetime()
        return ((stamp[0] * 365 + stamp[1] * 31 + stamp[2]) * 24 + stamp[4]) * 60 + stamp[5]
    except Exception:
        return int(time.time() // 60)


def _refresh_network():
    """Fetch forecast, official warnings and headlines.

    Returns (weather, official, headlines); any element may be None if
    that particular fetch failed, so one bad feed cannot lose the others.
    """
    link = net.connect(getattr(config, "WIFI_SSID", ""),
                       getattr(config, "WIFI_PASSWORD", ""),
                       getattr(config, "WIFI_COUNTRY", "GB"),
                       getattr(config, "WIFI_TIMEOUT", 25))
    if not link:
        return None, None, None

    forecast = official = headlines = None
    try:
        forecast = weather_api.fetch(config.LATITUDE, config.LONGITUDE,
                                     getattr(config, "TIMEZONE", "auto"))
        gc.collect()
        source = getattr(config, "ALERT_SOURCE", "none").lower()
        if source == "metoffice":
            official = metoffice.fetch(getattr(config, "MET_REGION", "se"),
                                       getattr(config, "MET_MIN_COLOUR", "Yellow"))
        elif source == "nws":
            official = nws.fetch(config.LATITUDE, config.LONGITUDE,
                                 getattr(config, "NWS_USER_AGENT", "BadgerSett/1.0"),
                                 getattr(config, "NWS_MIN_SEVERITY", "Severe"))
        gc.collect()
        if getattr(config, "NEWS_ENABLED", True):
            headlines = news.fetch(getattr(config, "NEWS_FEED", "top"),
                                   getattr(config, "NEWS_HEADLINES", 4))
    finally:
        net.disconnect()
        gc.collect()

    return forecast, official, headlines


def run():
    display = badger2040.Badger2040()
    display.set_update_speed(badger2040.UPDATE_NORMAL)

    state = State()
    screen = ui.UI(display, config)

    i2c = None
    bme = haptic = None
    try:
        i2c = sensor.make_i2c(getattr(config, "I2C_SDA", 4), getattr(config, "I2C_SCL", 5))
    except Exception as exc:
        util.log("I2C bus failed:", exc)

    if i2c:
        bme = Sensor(i2c, getattr(config, "BME688_ADDRESS", 0x77))
        if bme.error:
            util.log(bme.error)
        if getattr(config, "HAPTIC_ENABLED", True):
            haptic = Haptics(i2c, getattr(config, "DRV2605_ADDRESS", 0x5A),
                             getattr(config, "HAPTIC_ACTUATOR", "LRA"),
                             state.get("cal"))
            if haptic.error:
                util.log(haptic.error)

    while True:
        gc.collect()
        buttons = _wake_buttons(display)
        try:
            # Clear the latched wake state now that we have read it, so the
            # next loop on USB power does not see this press a second time.
            badger2040.reset_pressed_to_wake()
        except Exception:
            pass

        view = state.get("view", ui.VIEW_BADGE)
        force_refresh = False

        if _is_secret_chord(buttons):
            # Handled first: otherwise the A in the chord would just switch
            # to the badge view and the chord could never be distinguished.
            _secret_action(state, haptic)
        else:
            if "A" in buttons:
                view = ui.VIEW_BADGE
            elif "B" in buttons:
                view = ui.VIEW_DETAIL
            elif "C" in buttons:
                view = ui.VIEW_NEWS
            if "UP" in buttons:
                force_refresh = True
            if "DOWN" in buttons:
                state.set("muted", not state.get("muted"))
                util.log("muted:", state.get("muted"))
        if buttons and haptic and haptic.ready and not state.get("muted"):
            haptic.tick()

        state.set("view", view)

        # -- sensor: cheap, so always read ------------------------------
        indoor = bme.read() if (bme and bme.ok) else None
        if indoor:
            state.update_gas_baseline(indoor.get("gas"),
                                      getattr(config, "GAS_BASELINE_SAMPLES", 20))
            state.push_pressure(indoor.get("pressure"), _minutes_stamp())
            state.set("indoor", indoor)
        else:
            indoor = state.get("indoor")

        # -- network: only on a timed wake, first boot, or UP ------------
        timed_wake = True
        try:
            timed_wake = badger2040.woken_by_rtc()
        except Exception:
            pass

        cached = state.get("weather")
        want_network = force_refresh or cached is None or (
            timed_wake and not (buttons and getattr(config, "SENSOR_ONLY_ON_BUTTON", True)))

        forecast, official = cached, None
        headlines = state.get("news") or []
        online = None
        if want_network:
            display.led(64)
            fresh, official, fresh_news = _refresh_network()
            display.led(0)
            online = fresh is not None
            if fresh:
                forecast = fresh
                state.set("weather", fresh)
                state.set("updated", fresh.get("time"))
                _set_clock(fresh)
            if fresh_news:
                headlines = fresh_news
                state.set("news", fresh_news)
            if not fresh:
                util.log("refresh failed; showing cached data")

        # -- alerts ------------------------------------------------------
        current = alert_rules.evaluate(config, forecast, indoor, state, official)
        hour, minute, _ = _now()
        alert_rules.notify(config, current, state, haptic, hour)

        # -- draw --------------------------------------------------------
        updated = state.get("updated")
        if updated and "T" in updated:
            updated = "Updated " + updated.split("T")[1][:5]
        credit = "Open-Meteo"
        source = getattr(config, "ALERT_SOURCE", "none").lower()
        if source == "metoffice":
            credit += "  |  Warnings: Met Office"
        elif source == "nws":
            credit += "  |  Warnings: NWS"

        status = {
            "credit": credit,
            "updated": updated or "no data yet",
            "online": online,
            "muted": state.get("muted"),
            "sensor": bool(bme and bme.ok),
            "view": view,
        }
        screen.render(view, forecast, indoor, current, state, status, headlines)

        state.save()
        if haptic and haptic.ready:
            haptic.standby()

        # -- sleep -------------------------------------------------------
        minutes = max(1, int(getattr(config, "REFRESH_MINUTES", 30)))
        util.log("sleeping for %d minutes" % minutes)
        gc.collect()
        try:
            badger2040.sleep_for(minutes)
        except Exception as exc:
            # No RTC alarm available (or running on a plain Badger 2040):
            # fall back to a plain wait so the badge still cycles.
            util.log("sleep_for failed (%s); busy-waiting" % exc)
            _wait_for_button(display, minutes * 60)


def _wait_for_button(display, seconds):
    deadline = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        for pin, _ in _BUTTONS:
            try:
                if display.pressed(pin):
                    return
            except Exception:
                pass
        time.sleep(0.1)
