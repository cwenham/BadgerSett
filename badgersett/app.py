"""BadgerSett main loop.

The badge spends nearly all its life powered down. Each wake follows the
same arc: work out why we woke, read the sensor, refresh from the network
if this was a timed wake, decide whether anything deserves a buzz, draw,
then set the RTC alarm and cut power.

On USB power `sleep_for()` cannot actually cut power, so it blocks until
a button or the alarm fires and then returns — which is why everything
lives inside a `while True` rather than relying on a reset to loop.

POWER_MODE chooses between that and staying awake. Awake, the badge
samples the BME688 every few seconds so its gas heater stays conditioned,
and only redraws on a timer, a button, an alert or a due refresh.
"""

import gc
import time

import badger2040
import machine

import config

from . import alerts as alert_rules
from . import clock, metoffice, net, news, nws, power, runlog, sensor, ui, util, weather as weather_api
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


# ---------------------------------------------------------------------------
# Power mode
# ---------------------------------------------------------------------------
POWER_MODES = ("auto", "awake", "sleep")

# Minimum gap between network attempts within one power-on. Without it an
# awake badge that has lost WiFi would see "stale, refresh, fail, still
# stale" and retry the radio - and redraw the e-ink - every few seconds.
RETRY_S = 300
_last_attempt = None


def _power_mode():
    mode = str(getattr(config, "POWER_MODE", "auto")).lower()
    if mode not in POWER_MODES:
        util.log("unknown POWER_MODE %r; using auto" % mode)
        mode = "auto"
    return mode


def _stay_awake(mode):
    """awake: always. sleep: never. auto: only while on USB power."""
    if mode == "awake":
        return True
    if mode == "sleep":
        return False
    return power.on_usb()


def _retry_ok():
    return _last_attempt is None or \
        time.ticks_diff(time.ticks_ms(), _last_attempt) >= RETRY_S * 1000


def _network_due(state, refresh_minutes):
    stale = state.get("weather") is None or \
        clock.is_stale(state.get("last_refresh"), refresh_minutes)
    return stale and _retry_ok()


def _battery_low():
    """Supply voltage if we are on battery and below LOW_BATTERY_V, else None.

    The Badger 2040 W has no charging circuit and no cutoff of its own: it
    will run until the supply sags to about 2.7V, which is below the ~3.0V
    where a LiPo cell starts to be damaged. This is the only thing standing
    between an unattended run-to-empty and a ruined battery.
    """
    limit = getattr(config, "LOW_BATTERY_V", 3.2)
    if not limit or power.on_usb():
        return None
    volts = power.vsys()
    return volts if (volts is not None and volts < limit) else None


def _pressed(display, pin):
    try:
        return display.pressed(pin)
    except Exception:
        return False


def _collect_buttons(display, timeout_ms=2500, quiet_ms=150, initial=None):
    """Every button pressed from now until they have all been released.

    Returning on the first edge would split a chord: A and C held, then UP
    pressed, would arrive as a bare A and select the badge view. So watch
    until the buttons have been quiet for `quiet_ms`, collecting each one.
    """
    # Seed with whatever triggered the call, so a tap shorter than the gap
    # between noticing it and starting to collect is never lost.
    seen = list(initial or [])
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    quiet_since = None
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        held = [name for pin, name in _BUTTONS if _pressed(display, pin)]
        for name in held:
            if name not in seen:
                seen.append(name)
        if held:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.ticks_ms()
        elif time.ticks_diff(time.ticks_ms(), quiet_since) >= quiet_ms:
            break
        time.sleep_ms(20)
    return seen


def _new_indoor_alert(reading, state):
    """True if this reading would raise or escalate an indoor alert."""
    latched = state.get("alerts") or {}
    for alert in alert_rules.evaluate(config, None, reading, state, None):
        if alert["level"] > latched.get(alert["key"], 0):
            return True
    return False


def _awake_wait(display, bme, state, log, refresh_minutes):
    """Stay powered and keep the gas heater conditioned until something
    deserves a redraw. Returns (reason, buttons).

    The heater only fires during a measurement, so conditioning means
    sampling every GAS_SAMPLE_S seconds. Those samples are cheap and do not
    touch the display; the e-ink is only redrawn on the way out.
    """
    sample_s = max(2, int(getattr(config, "GAS_SAMPLE_S", 5)))
    redraw_s = max(sample_s, int(getattr(config, "AWAKE_REDRAW_MINUTES", 5)) * 60)
    deadline = time.ticks_add(time.ticks_ms(), redraw_s * 1000)

    while True:
        reading = None
        if bme and bme.ok:
            reading = bme.read(samples=1, settle=0, gas_enabled=True)
            if reading and _new_indoor_alert(reading, state):
                return "alert", []
        log.maybe_beat(gas=(reading or {}).get("gas_reason", "n/a"))

        if _network_due(state, refresh_minutes):
            return "refresh", []

        next_sample = time.ticks_add(time.ticks_ms(), sample_s * 1000)
        while time.ticks_diff(next_sample, time.ticks_ms()) > 0:
            held = [name for pin, name in _BUTTONS if _pressed(display, pin)]
            if held:
                return "button", _collect_buttons(display, initial=held)
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return "redraw", []
            time.sleep_ms(40)


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
    """Pick a WiFi network, then fetch everything tied to where it is.

    Returns (weather, official, headlines, network); any element may be
    None if that particular fetch failed, so one bad feed cannot lose the
    others. `network` is the entry we actually joined, which carries the
    location the rest of the data belongs to.
    """
    networks = net.networks_from_config(config)
    link, active = net.connect_best(networks,
                                    getattr(config, "WIFI_COUNTRY", "GB"),
                                    getattr(config, "WIFI_TIMEOUT", 25),
                                    getattr(config, "WIFI_MAX_ATTEMPTS", 3))
    if not link:
        return None, None, None, None

    # Time first, while the link is definitely up. NTP gives UTC; the
    # local offset arrives with the forecast a moment later.
    clock.sync_ntp()

    forecast = official = headlines = None
    try:
        forecast = weather_api.fetch(active["lat"], active["lon"],
                                     getattr(config, "TIMEZONE", "auto"))
        if forecast:
            clock.apply_utc_offset(forecast.get("utc_offset"))
            clock.persist()
        gc.collect()
        source = getattr(config, "ALERT_SOURCE", "none").lower()
        if source == "metoffice":
            official = metoffice.fetch(active["met_region"],
                                       getattr(config, "MET_MIN_COLOUR", "Yellow"))
        elif source == "nws":
            official = nws.fetch(active["lat"], active["lon"],
                                 getattr(config, "NWS_USER_AGENT", "BadgerSett/1.0"),
                                 getattr(config, "NWS_MIN_SEVERITY", "Severe"))
        gc.collect()
        if getattr(config, "NEWS_ENABLED", True):
            headlines = news.fetch(getattr(config, "NEWS_FEED", "top"),
                                   getattr(config, "NEWS_HEADLINES", 4))
    finally:
        net.disconnect()
        gc.collect()

    return forecast, official, headlines, active


def run():
    global _last_attempt
    display = badger2040.Badger2040()
    display.set_update_speed(badger2040.UPDATE_NORMAL)

    state = State()
    screen = ui.UI(display, config)

    # The RP2040's clock is wiped by every power cut; the PCF85063A keeps
    # running on its own. Restore from it before deciding anything.
    clock.restore()

    location = state.get("location") or {}

    i2c = None
    bme = haptic = None
    try:
        i2c = sensor.make_i2c(getattr(config, "I2C_SDA", 4), getattr(config, "I2C_SCL", 5))
    except Exception as exc:
        util.log("I2C bus failed:", exc)

    if i2c:
        bme = Sensor(i2c, getattr(config, "BME688_ADDRESS", 0x77),
                     location.get("altitude"))
        if bme.error:
            util.log(bme.error)
        if getattr(config, "HAPTIC_ENABLED", True):
            haptic = Haptics(i2c, getattr(config, "DRV2605_ADDRESS", 0x5A),
                             getattr(config, "HAPTIC_ACTUATOR", "LRA"),
                             state.get("cal"))
            if haptic.error:
                util.log(haptic.error)

    log = runlog.RunLog(getattr(config, "RUNTIME_LOG", False),
                        getattr(config, "RUNTIME_LOG_MINUTES", 10),
                        _power_mode())
    log.write("boot", usb=int(power.on_usb()))
    pending = []

    while True:
        gc.collect()

        low = _battery_low()
        if low is not None:
            util.log("battery low (%.2fV); powering off" % low)
            log.write("lowbat", cutoff=getattr(config, "LOW_BATTERY_V", 3.2))
            screen.message("BATTERY LOW",
                           "%.2f V. Powered off to protect the cell. "
                           "Recharge it on an external charger - this board "
                           "cannot charge it - then press a button." % low)
            state.save()
            # No alarm: stay off until a button. On battery this cuts power
            # and never returns; the e-ink keeps the message on screen.
            badger2040.turn_off()
            continue

        buttons = _wake_buttons(display)
        for name in pending:              # presses caught by the awake loop
            if name not in buttons:
                buttons.append(name)
        pending = []
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
        # Gas is only meaningful while the badge stays awake to keep the
        # heater cycling; a sleeping badge's heater is always cold.
        mode = _power_mode()
        awake = _stay_awake(mode)
        indoor = bme.read(gas_enabled=awake) if (bme and bme.ok) else None
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
        want_network = force_refresh or _network_due(state, refresh_minutes)
        if buttons and not getattr(config, "SENSOR_ONLY_ON_BUTTON", True):
            want_network = True

        forecast, official = cached, None
        headlines = state.get("news") or []
        online = None
        if want_network:
            display.led(64)
            _last_attempt = time.ticks_ms()
            fresh, official, fresh_news, active = _refresh_network()
            log.write("fetch", ok=int(fresh is not None),
                      where=(active or {}).get("label", "none").replace(" ", "_"))
            display.led(0)
            online = fresh is not None
            if active:
                # Remember where we are, so a button wake with no network
                # still labels the cached data with the right place.
                state.set("location", {k: active[k] for k in
                                       ("label", "lat", "lon", "met_region", "altitude")})
                if bme:
                    bme.altitude = active["altitude"]
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
            "location": (state.get("location") or {}).get("label"),
            "awake": awake,
            "view": view,
        }
        screen.render(view, forecast, indoor, current, state, status, headlines)

        state.save()
        if haptic and haptic.ready:
            haptic.standby()

        if awake:
            reason, pending = _awake_wait(display, bme, state, log, refresh_minutes)
            util.log("awake: redrawing for", reason, pending or "")
            continue

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
