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
from . import clock, metoffice, net, news, nws, power, sensor, ui, util, weather as weather_api
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

    # Time first, while the link is definitely up. NTP gives UTC; the
    # local offset arrives with the forecast a moment later.
    clock.sync_ntp()

    forecast = official = headlines = None
    try:
        forecast = weather_api.fetch(config.LATITUDE, config.LONGITUDE,
                                     getattr(config, "TIMEZONE", "auto"))
        if forecast:
            clock.apply_utc_offset(forecast.get("utc_offset"))
            clock.persist()
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

    # The RP2040's clock is wiped by every power cut; the PCF85063A keeps
    # running on its own. Restore from it before deciding anything.
    clock.restore()

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
        # The gas heater is only worth running where it can stay warm.
        gas_ok = power.on_usb() or not getattr(config, "GAS_USB_ONLY", True)
        indoor = bme.read(gas_enabled=gas_ok) if (bme and bme.ok) else None
        if indoor:
            state.update_gas_baseline(indoor.get("gas"),
                                      getattr(config, "GAS_BASELINE_SAMPLES", 20))
            state.push_pressure(indoor.get("pressure"), clock.minutes())
            state.set("indoor", indoor)
        else:
            indoor = state.get("indoor")

        # -- network: driven by how old the data is --------------------
        # Deliberately NOT badger2040.woken_by_rtc(): that reports the
        # reason for the *power-on*, so on USB - where sleep_for() cannot
        # actually cut power - it stays False forever and nothing ever
        # refreshes. Worse, the clock is only set by a refresh, so the two
        # deadlock and the badge sits with a frozen timestamp.
        refresh_minutes = max(1, int(getattr(config, "REFRESH_MINUTES", 30)))
        cached = state.get("weather")
        stale = clock.is_stale(state.get("last_refresh"), refresh_minutes)
        want_network = force_refresh or cached is None or stale
        if buttons and not getattr(config, "SENSOR_ONLY_ON_BUTTON", True):
            want_network = True

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
                # The time we actually fetched, from the freshly synced
                # clock - not the forecast's own timestamp, which Open-Meteo
                # quantises to 15-minute buckets.
                state.set("updated", clock.hhmm())
                state.set("last_refresh", clock.minutes())
            if fresh_news:
                headlines = fresh_news
                state.set("news", fresh_news)
            if not fresh:
                util.log("refresh failed; showing cached data")

        # -- alerts ------------------------------------------------------
        current = alert_rules.evaluate(config, forecast, indoor, state, official)
        alert_rules.notify(config, current, state, haptic, clock.hour())

        # -- draw --------------------------------------------------------
        updated = state.get("updated")
        if updated and "T" in updated:           # older state files
            updated = updated.split("T")[1][:5]
        updated = "Updated " + updated if updated else None
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
