#!/usr/bin/env python3
"""Run BadgerSett's logic on your laptop, with the hardware faked out.

    python3 tools/test_offline.py

This cannot prove the badge works, but it catches the bugs that are
miserable to debug over a USB serial line: layout crashes, bad register
sequences, alert rules that never fire, and the NWS chunked parser.
"""

import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# MicroPython shims
# ---------------------------------------------------------------------------
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_add = lambda t, d: t + d
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)


class FakeDisplay:
    """Records every draw call and flags anything drawn off-panel."""

    W, H = 296, 128

    def __init__(self):
        self.calls = []
        self.font = "bitmap8"
        self.pen = 0
        self.updates = 0
        self.out_of_bounds = []
        self.display = self

    def _note(self, name, *args):
        self.calls.append((name, args))

    def set_pen(self, pen):
        self.pen = pen

    def set_font(self, font):
        self.font = font

    def set_thickness(self, value):
        pass

    def clear(self):
        self._note("clear")

    def update(self):
        self.updates += 1

    def partial_update(self, *a):
        self.updates += 1

    def set_update_speed(self, speed):
        pass

    def led(self, level):
        pass

    def pressed(self, pin):
        return False

    # Measured on real hardware: the widest glyph ("M") is 6px at scale 1 in
    # both bitmap6 and bitmap8, so 6/char is the true upper bound on width.
    def measure_text(self, text, scale=1):
        return int(len(text) * 6 * scale)

    def _glyph_h(self, scale):
        return (6 if self.font == "bitmap6" else 8) * scale

    # NOTE: PicoGraphics.text() defaults to scale=2, NOT 1. Modelling that
    # faithfully is what catches a missing explicit scale= argument, which
    # otherwise renders at double size and overruns the layout.
    def text(self, text, x, y, wordwrap=None, scale=2, *a, **kw):
        self._note("text", text, x, y, scale)
        width = self.measure_text(text, scale)
        height = self._glyph_h(scale)
        if y < 0 or y + height > self.H or x < 0 or x + width > self.W:
            self.out_of_bounds.append(
                ("text", text, x, y, "%dx%d" % (width, height), "scale=%d" % scale))

    def rectangle(self, x, y, w, h):
        self._note("rectangle", x, y, w, h)

    def line(self, *a):
        self._note("line", *a)

    def circle(self, x, y, r):
        self._note("circle", x, y, r)

    def triangle(self, *a):
        self._note("triangle", *a)

    def pixel(self, x, y):
        self._note("pixel", x, y)


class FakeI2C:
    """A DRV2605L-ish register file, good enough to verify the driver."""

    def __init__(self, devices=(0x77, 0x5A)):
        self.devices = list(devices)
        self.regs = {}
        self.writes = []
        self._go_reads = 0

    def scan(self):
        return self.devices

    def writeto_mem(self, addr, reg, data):
        value = data[0]
        self.writes.append((reg, value))
        if reg == 0x01 and value & 0x80:
            value &= ~0x80          # reset bit self-clears
        if reg == 0x0C and value:
            self._go_reads = 0
        self.regs[reg] = value

    def readfrom_mem(self, addr, reg, length):
        if reg == 0x0C:             # GO clears after a couple of polls
            self._go_reads += 1
            if self._go_reads > 2:
                self.regs[0x0C] = 0
        return bytes([self.regs.get(reg, 0)])


def install_fakes():
    badger = types.ModuleType("badger2040")
    badger.WIDTH, badger.HEIGHT = 296, 128
    badger.UPDATE_NORMAL, badger.UPDATE_MEDIUM = 0, 1
    badger.UPDATE_FAST, badger.UPDATE_TURBO = 2, 3
    badger.BUTTON_A, badger.BUTTON_B, badger.BUTTON_C = 12, 13, 14
    badger.BUTTON_UP, badger.BUTTON_DOWN = 15, 11
    badger.Badger2040 = FakeDisplay
    badger.pressed_to_wake = lambda pin: False
    badger.reset_pressed_to_wake = lambda: None
    badger.woken_by_rtc = lambda: True
    badger.woken_by_button = lambda: False
    badger.sleep_for = lambda minutes: None
    badger.pico_rtc_to_pcf = lambda: None
    sys.modules["badger2040"] = badger

    machine = types.ModuleType("machine")

    class RTC:
        def datetime(self, value=None):
            return (2026, 9, 20, 0, 17, 15, 0, 0)

    machine.RTC = RTC
    machine.Pin = lambda n, *a, **kw: n
    machine.I2C = lambda *a, **kw: FakeI2C()
    sys.modules["machine"] = machine

    network = types.ModuleType("network")
    network.STA_IF = 0
    network.STAT_WRONG_PASSWORD = -3
    network.STAT_NO_AP_FOUND = -2
    network.STAT_CONNECT_FAIL = -1
    network.country = lambda c: None

    class WLAN:
        def __init__(self, mode):
            pass

        def active(self, on=None):
            return True

        def config(self, **kw):
            pass

        def isconnected(self):
            return True

        def connect(self, *a):
            pass

        def disconnect(self):
            pass

        def deinit(self):
            pass

        def status(self):
            return 3

        def ifconfig(self):
            return ("192.168.1.50", "255.255.255.0", "192.168.1.1", "1.1.1.1")

    network.WLAN = WLAN
    sys.modules["network"] = network

    bme_mod = types.ModuleType("breakout_bme68x")

    class BreakoutBME68X:
        def __init__(self, i2c, address=0x76):
            self.i2c, self.address = i2c, address
            self.reads = 0

        def configure(self, **kw):
            self.configured = kw

        def read(self):
            self.reads += 1
            # First conversion is the usual warm, dry, unstable lie.
            if self.reads == 1:
                return (28.0, 101300.0, 30.0, 8000.0, 0x00, 0, 0)
            return (22.4, 101320.0, 47.0, 120000.0, 0x10, 0, 0)

    bme_mod.BreakoutBME68X = BreakoutBME68X
    bme_mod.STATUS_HEATER_STABLE = 0x10
    sys.modules["breakout_bme68x"] = bme_mod

    urequests = types.ModuleType("urequests")
    urequests.get = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("no network in the offline harness"))
    sys.modules["urequests"] = urequests

    cfg = types.ModuleType("config")
    cfg.NAME = "Christopher Wenham"
    cfg.TITLE = "Principal Engineer"
    cfg.ORG = "Badger Industries"
    cfg.PHOTO = None
    cfg.WIFI_SSID = "ssid"
    cfg.WIFI_PASSWORD = "pw"
    cfg.WIFI_COUNTRY = "GB"
    cfg.WIFI_TIMEOUT = 5
    cfg.LATITUDE, cfg.LONGITUDE = 50.8279, -0.1687   # Hove
    cfg.TIMEZONE = "auto"
    cfg.UNITS = "metric"
    cfg.ALTITUDE_M = 11
    cfg.REFRESH_MINUTES = 30
    cfg.SENSOR_ONLY_ON_BUTTON = True
    cfg.I2C_SDA, cfg.I2C_SCL = 4, 5
    cfg.BME688_ADDRESS, cfg.DRV2605_ADDRESS = 0x77, 0x5A
    cfg.HAPTIC_ACTUATOR = "LRA"
    cfg.HAPTIC_ENABLED = True
    cfg.HAPTIC_QUIET_HOURS = (22, 7)
    cfg.SHOW_BATTERY = False
    cfg.ALERT_SOURCE = "metoffice"
    cfg.MET_REGION = "se"
    cfg.MET_MIN_COLOUR = "Yellow"
    cfg.NWS_USER_AGENT = "BadgerSett/1.0 (test)"
    cfg.NWS_MIN_SEVERITY = "Severe"
    cfg.GUST_WARN, cfg.GUST_SEVERE = 60, 90
    cfg.HEAT_WARN, cfg.COLD_WARN = 32, -5
    cfg.NEWS_ENABLED = True
    cfg.NEWS_FEED = "top"
    cfg.NEWS_HEADLINES = 4
    cfg.GAS_WARMUP_S = 300
    cfg.GAS_ALERTS = True
    cfg.GAS_DROP_WARN, cfg.GAS_DROP_SEVERE = 0.60, 0.35
    cfg.GAS_BASELINE_SAMPLES = 20
    cfg.TEMP_LOW_WARN, cfg.TEMP_HIGH_WARN = 15, 30
    cfg.HUMIDITY_LOW_WARN, cfg.HUMIDITY_HIGH_WARN = 25, 70
    cfg.PRESSURE_DROP_WARN = 3.0
    cfg.DEBUG = False
    sys.modules["config"] = cfg
    return cfg


CONFIG = install_fakes()

from badgersett import (alerts, haptics, metoffice, news, nws,  # noqa: E402
                        state as state_mod, ui, util, weather)


# ---------------------------------------------------------------------------
SAMPLE_WEATHER = {
    "temp": 19.7, "feels": 17.7, "humidity": 47, "precip": 0.0, "code": 3,
    "wind": 10.4, "gust": 23.0, "dir": 210, "is_day": True,
    "time": "2026-09-20T17:15", "utc_offset": 3600,
    "hi": 19.9, "lo": 15.2, "pop": 38, "gust_max": 28.4, "code_today": 3,
    "hi2": 21.5, "lo2": 13.5, "pop2": 0, "code2": 3, "gust_max2": 16.2,
}

SAMPLE_INDOOR = {
    "temp_c": 22.4, "temp": 22.4, "humidity": 47.0, "pressure_pa": 101320.0,
    "pressure": 1014.6, "gas": 120000.0, "gas_raw": 120000.0, "stable": True,
    "dew_c": 10.8, "dew": 10.8, "gas_trusted": True, "gas_warm_s": 900.0,
}


def test_util():
    print("\nutil")
    check("dew point", abs(util.dew_point(22.4, 47.0) - 10.8) < 0.5,
          util.dew_point(22.4, 47.0))
    slp = util.sea_level_pressure(101320.0, 11, 22.4)
    check("sea-level pressure adds ~1.3hPa", 1014.0 < slp < 1015.5, slp)
    check("bearing N", util.bearing(0) == "N")
    check("bearing SSW", util.bearing(210) == "SSW", util.bearing(210))
    check("quiet hours wrap midnight", util.in_quiet_hours(23, (22, 7)) and
          util.in_quiet_hours(3, (22, 7)) and not util.in_quiet_hours(12, (22, 7)))
    check("quiet hours disabled", not util.in_quiet_hours(23, None))
    check("parse_iso", util.parse_iso("2026-09-20T17:15") == (2026, 9, 20, 17, 15))
    check("parse_iso rejects junk", util.parse_iso("nope") is None)


def test_state(tmp="/tmp/badgersett_state.json"):
    print("\nstate")
    state_mod.PATH = tmp
    if os.path.exists(tmp):
        os.remove(tmp)
    st = state_mod.State()

    for _ in range(10):
        st.update_gas_baseline(120000.0, 20)
    baseline = st.get("gas_baseline")
    check("baseline settles on steady input", abs(baseline - 120000.0) < 1.0, baseline)

    st.update_gas_baseline(20000.0, 20)      # a VOC event
    after = st.get("gas_baseline")
    check("VOC spike does not move the baseline at all", after == baseline, after)

    for _ in range(60):                      # ...but a lasting change is accepted
        st.update_gas_baseline(20000.0, 20)
    check("persistent low reading eventually becomes the new normal",
          st.get("gas_baseline") < 100000.0, st.get("gas_baseline"))

    st2 = state_mod.State()
    for _ in range(6):
        st2.update_gas_baseline(120000.0, 20)
    st2.update_gas_baseline(110000.0, 20)    # an ordinary dip, above the event floor
    check("ordinary dips still nudge the baseline down",
          109000 < st2.get("gas_baseline") < 120000.0, st2.get("gas_baseline"))

    st.push_pressure(1015.0, 0)
    st.push_pressure(1013.0, 120)
    st.push_pressure(1010.5, 200)
    trend = st.pressure_trend(180)
    check("3h pressure trend is negative", trend is not None and trend < -4.0, trend)

    st.set("muted", True)
    st.save()
    reloaded = state_mod.State()
    check("state round-trips through flash", reloaded.get("muted") is True)
    os.remove(tmp)


def test_nws_stream():
    print("\nnws chunked parser")
    features = []
    for event, severity in (("Tornado Warning", "Extreme"),
                            ("Flood Advisory", "Minor"),
                            ("Severe Thunderstorm Warning", "Severe")):
        features.append({
            "properties": {
                "id": "urn:oid:2.49.0.1.840", "areaDesc": "Somewhere, TX",
                "ends": "2026-09-20T19:00:00-05:00", "status": "Actual",
                "severity": severity, "certainty": "Likely", "urgency": "Immediate",
                "event": event, "headline": "X" * 400,
                "description": "Y" * 3000, "instruction": "Z" * 1500,
            }
        })
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()

    class Raw:
        def __init__(self, data):
            self.data, self.pos = data, 0

        def read(self, n):
            chunk = self.data[self.pos:self.pos + n]
            self.pos += len(chunk)
            return chunk

    class Response:
        status_code = 200

        def __init__(self):
            self.raw = Raw(body)      # a fresh stream per request

        def close(self):
            pass

    original = nws.requests
    nws.requests = types.SimpleNamespace(get=lambda url, headers=None: Response())
    try:
        found = nws.fetch(29.76, -95.36, "test", "Severe")
    finally:
        nws.requests = original

    names = [a["event"] for a in found]
    check("severe+ alerts found across chunk boundaries", len(found) == 2, names)
    check("most severe sorts first", names and names[0] == "Tornado Warning", names)
    check("Minor alert filtered out", "Flood Advisory" not in names, names)
    check("severity captured", found and found[0]["severity"] == "Extreme")
    check("nws alerts carry a namespaced key",
          found[0]["key"] == "nws:Tornado Warning", found[0].get("key"))
    check("Extreme maps to level 3, Severe to level 2",
          [a["level"] for a in found] == [3, 2], [a["level"] for a in found])

    nws.requests = types.SimpleNamespace(get=lambda url, headers=None: Response())
    try:
        lenient = nws.fetch(29.76, -95.36, "test", "Minor")
    finally:
        nws.requests = original
    check("min_severity=Minor keeps all three", len(lenient) == 3,
          [a["event"] for a in lenient])


def test_weather_codes():
    print("\nweather codes")
    check("code 0 is clear/sun", weather.describe(0) == ("Clear", "sun"))
    check("code 95 is a storm", weather.describe(95)[1] == "storm")
    check("unknown code degrades gracefully", weather.describe(4242)[1] == "cloud")
    check("every icon key is drawable",
          all(key in ("sun", "moon", "partly", "cloud", "fog", "drizzle", "rain",
                      "heavy_rain", "snow", "sleet", "storm")
              for _, key in weather.WMO.values()))


def test_alerts():
    print("\nalert rules")
    state_mod.PATH = "/tmp/badgersett_alert_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    st = state_mod.State()

    quiet = alerts.evaluate(CONFIG, SAMPLE_WEATHER, SAMPLE_INDOOR, st, [])
    check("calm conditions raise nothing", quiet == [], quiet)

    stormy = dict(SAMPLE_WEATHER, code=96, gust_max=95.0, hi=34.0)
    loud = alerts.evaluate(CONFIG, stormy, SAMPLE_INDOOR, st, [])
    keys = sorted(a["key"] for a in loud)
    check("hail/gust/heat all fire", keys == ["wx:code:96", "wx:gust", "wx:heat"], keys)
    check("severe sorts to the top", loud[0]["level"] == 3, loud[0])

    # A provider dict without "key"/"level" (the older shape) must still work.
    legacy = [{"event": "Tornado Warning", "severity": "Extreme", "rank": 4, "ends": None}]
    with_official = alerts.evaluate(CONFIG, SAMPLE_WEATHER, SAMPLE_INDOOR, st, legacy)
    check("official alert included", with_official[0]["text"] == "Tornado Warning",
          with_official)
    check("legacy provider shape still gets a key and a level",
          with_official[0]["key"] == "official:Tornado Warning"
          and with_official[0]["level"] == 3, with_official[0])

    # Gas: baseline learned, then a sharp drop.
    for _ in range(8):
        st.update_gas_baseline(120000.0, 20)
    bad_air = dict(SAMPLE_INDOOR, gas=30000.0)
    gassy = alerts.evaluate(CONFIG, SAMPLE_WEATHER, bad_air, st, [])
    check("gas drop raises a severe alert",
          any(a["key"] == "in:gas" and a["level"] == 3 for a in gassy), gassy)

    unstable = dict(bad_air, stable=False, gas=None)
    check("unstable heater is not trusted",
          not any(a["key"] == "in:gas"
                  for a in alerts.evaluate(CONFIG, SAMPLE_WEATHER, unstable, st, [])))

    print("\nnotification de-duplication")

    class FakeHaptic:
        ready = True

        def __init__(self):
            self.played = []

        def alert(self, level):
            self.played.append(level)

    hap = FakeHaptic()
    first = alerts.notify(CONFIG, loud, st, hap, hour=12)
    check("new alert buzzes at its level", first == 3 and hap.played == [3], hap.played)

    again = alerts.notify(CONFIG, loud, st, hap, hour=12)
    check("same alert does not buzz twice", again == 0 and hap.played == [3], hap.played)

    st.set("alerts", {"wx:gust": 2})
    escalation = [{"key": "wx:gust", "level": 3, "text": "Damaging gusts", "source": "forecast"}]
    bumped = alerts.notify(CONFIG, escalation, st, hap, hour=12)
    check("a warning escalating to severe buzzes again", bumped == 3, bumped)

    st.set("alerts", {"wx:gust": 3})
    calmed = [{"key": "wx:gust", "level": 2, "text": "Strong gusts", "source": "forecast"}]
    check("de-escalation does not buzz",
          alerts.notify(CONFIG, calmed, st, hap, hour=12) == 0)
    check("state tracks the current level after de-escalation",
          st.get("alerts") == {"wx:gust": 2}, st.get("alerts"))

    st.set("alerts", {})
    quiet_hap = FakeHaptic()
    level2 = [{"key": "wx:gust", "level": 2, "text": "Gusts", "source": "forecast"}]
    check("quiet hours mute a level-2 alert",
          alerts.notify(CONFIG, level2, st, quiet_hap, hour=2) == 0)
    st.set("alerts", {})
    level3 = [{"key": "wx:code:99", "level": 3, "text": "Storm", "source": "forecast"}]
    check("quiet hours still allow severe",
          alerts.notify(CONFIG, level3, st, quiet_hap, hour=2) == 3)

    st.set("alerts", {})
    st.set("muted", True)
    muted_hap = FakeHaptic()
    check("mute suppresses everything",
          alerts.notify(CONFIG, level3, st, muted_hap, hour=12) == 0)
    st.set("muted", False)
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


def test_haptics():
    print("\nDRV2605L driver")
    bus = FakeI2C()
    driver = haptics.Haptics(bus, 0x5A, "LRA")
    check("driver comes up", driver.ready, driver.error)
    writes = dict(bus.writes)
    check("LRA bit set in feedback", bus.regs.get(0x1A, 0) & 0x80 == 0x80,
          hex(bus.regs.get(0x1A, 0)))
    check("LRA library selected", bus.regs.get(0x03) == 6, bus.regs.get(0x03))
    check("left in internal-trigger mode", bus.regs.get(0x01) == 0x00,
          hex(bus.regs.get(0x01, 0xFF)))
    check("reset issued first", bus.writes[0] == (0x01, 0x80), bus.writes[0])

    bus.writes = []
    driver.play((15, 14, 15))
    seq = [(r, v) for r, v in bus.writes if 0x04 <= r <= 0x0B]
    check("sequencer slots filled then zero-terminated",
          seq[:4] == [(0x04, 15), (0x05, 14), (0x06, 15), (0x07, 0)], seq[:4])
    check("GO fired", (0x0C, 1) in bus.writes)

    erm = haptics.Haptics(FakeI2C(), 0x5A, "ERM")
    check("ERM clears the LRA bit", erm.ready and not (erm._read(0x1A) & 0x80))

    missing = haptics.Haptics(FakeI2C(devices=[0x77]), 0x5A, "LRA")
    check("absent chip reports an error, does not raise",
          not missing.ready and "no DRV2605L" in missing.error, missing.error)
    check("play on a dead driver is a no-op", missing.play((1,)) is False)

    check("every pattern level is defined",
          all(level in haptics.PATTERNS for level in (1, 2, 3)))
    check("all effect ids are in ROM range 1-123",
          all(1 <= e <= 123 for pattern in haptics.PATTERNS.values() for e in pattern))


def test_layouts():
    print("\nscreen layouts")
    state_mod.PATH = "/tmp/badgersett_ui_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    st = state_mod.State()
    for _ in range(8):
        st.update_gas_baseline(120000.0, 20)
    st.push_pressure(1016.0, 0)
    st.push_pressure(1012.0, 200)

    status = {"updated": "Updated 17:15", "online": True, "muted": False,
              "sensor": True, "view": 0,
              "credit": "Open-Meteo  |  Warnings: Met Office"}
    alert_list = [
        {"key": "nws:Tornado Warning", "level": 3, "text": "Tornado Warning", "source": "nws"},
        {"key": "wx:gust", "level": 2, "text": "Strong gusts 71 km/h", "source": "forecast"},
    ]

    headlines = [
        "German Chancellor Merz calls state election a 'disaster' for his party but vows to stay on",
        "Key takeaways from BBC interview as Earl Spencer defends claims about King",
        "Watch: Emotional Earl Spencer says he misses sister Diana every day",
        "Specialist courts for rape cases to be rolled out across England and Wales",
    ]

    scenarios = (
        ("badge, full data", 0, SAMPLE_WEATHER, SAMPLE_INDOOR, alert_list, None),
        ("badge, no alerts", 0, SAMPLE_WEATHER, SAMPLE_INDOOR, [], None),
        ("badge, offline cold start", 0, None, None, [], None),
        ("detail, full data", 1, SAMPLE_WEATHER, SAMPLE_INDOOR, [], None),
        ("detail, with alert", 1, SAMPLE_WEATHER, SAMPLE_INDOOR, alert_list, None),
        ("detail, no forecast", 1, None, SAMPLE_INDOOR, [], None),
        ("detail, no sensor", 1, SAMPLE_WEATHER, None, [], None),
        ("news, four headlines", 2, SAMPLE_WEATHER, SAMPLE_INDOOR, [], headlines),
        ("news, empty", 2, SAMPLE_WEATHER, SAMPLE_INDOOR, [], []),
        ("secret view falls back to badge", 3, SAMPLE_WEATHER, SAMPLE_INDOOR, [], None),
    )

    for label, view, wx, indoor, alist, heads in scenarios:
        display = FakeDisplay()
        screen = ui.UI(display, CONFIG)
        try:
            screen.render(view, wx, indoor, alist, st, dict(status, view=view), heads)
            drew = len(display.calls) > 5 and display.updates == 1
            check("%s renders" % label, drew,
                  "calls=%d updates=%d" % (len(display.calls), display.updates))
            if display.out_of_bounds:
                check("%s stays on screen" % label, False, display.out_of_bounds[:3])
            else:
                check("%s stays on screen" % label, True)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check("%s renders" % label, False, exc)

    # A name far too long for the pane must not overflow it.
    long_cfg = types.SimpleNamespace(NAME="Bartholomew Fitzwilliam-Cholmondeley III",
                                     TITLE="Senior Principal Distinguished Engineer, Platform",
                                     ORG="A Very Long Organisation Name Indeed", PHOTO=None)
    display = FakeDisplay()
    screen = ui.UI(display, long_cfg)
    screen.badge(SAMPLE_WEATHER, SAMPLE_INDOOR, alert_list, status)
    widest = max((display.measure_text(t, s) + x
                  for kind, (t, x, y, s) in
                  ((c[0], c[1]) for c in display.calls if c[0] == "text")), default=0)
    check("long name/title are truncated to the panel", widest <= 296, widest)

    # Imperial units must flow through the same layouts.
    CONFIG.UNITS = "imperial"
    display = FakeDisplay()
    ui.UI(display, CONFIG).badge(dict(SAMPLE_WEATHER, temp=67.5, hi=68, lo=59),
                                 dict(SAMPLE_INDOOR, temp=72.3), [], status)
    check("imperial renders with F", any(c[0] == "text" and c[1][0] == "F"
                                         for c in display.calls))

    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, SAMPLE_INDOOR, st, [], status)
    check("detail view carries the Met Office attribution",
          any(c[0] == "text" and "Met Office" in str(c[1][0]) for c in display.calls))
    check("attribution stays on the panel", not display.out_of_bounds,
          display.out_of_bounds[:2])
    texts = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("detail view shows outside and inside together",
          "OUTSIDE" in texts and "INSIDE" in texts and "Humidity" in texts
          and "Gusts" in texts, texts[:90])

    display = FakeDisplay()
    ui.UI(display, CONFIG).news_view(headlines, dict(status, view=2))
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("news view wraps long headlines rather than clipping",
          "Merz" in drawn and not display.out_of_bounds, display.out_of_bounds[:2])
    check("news view credits the BBC", "BBC News" in drawn)
    CONFIG.UNITS = "metric"
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


QUIET_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Met Office warnings for London &amp; South East England</title>
  <pubDate>Sun, 20 Sep 2026 20:05:23 GMT</pubDate>
</channel></rss>"""

BUSY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Met Office warnings for London &amp; South East England</title>
  <item>
    <title>Yellow warning of rain affecting London &amp; South East England</title>
    <link>https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings</link>
  </item>
  <item>
    <title>Amber warning of wind affecting London &amp; South East England</title>
  </item>
  <item>
    <title>Yellow warning of wind affecting London &amp; South East England</title>
  </item>
  <item>
    <title>Something entirely unexpected</title>
  </item>
</channel></rss>"""


def _rss_response(body):
    class Raw:
        def __init__(self):
            self.data, self.pos = body.encode(), 0

        def read(self, n):
            chunk = self.data[self.pos:self.pos + n]
            self.pos += len(chunk)
            return chunk

    class Response:
        status_code = 200

        def __init__(self):
            self.raw = Raw()

        def close(self):
            pass

    return Response


def test_metoffice():
    print("\nMet Office warnings (UK)")
    check("yellow rain parses",
          metoffice.parse_title("Yellow warning of rain affecting London &amp; South East England")
          == ("yellow", "rain"))
    check("amber parses", metoffice.parse_title(
          "Amber warning of wind affecting Wales") == ("amber", "wind"))
    check("red parses", metoffice.parse_title(
          "Red warning of snow affecting Grampian") == ("red", "snow"))
    check("multi-word hazard kept whole", metoffice.parse_title(
          "Yellow warning of thunderstorms and rain affecting Wales")
          == ("yellow", "thunderstorms and rain"))
    # An actual title from the SE feed on 2026-08-12, kept as a fixture so a
    # change in the Met Office's wording shows up here rather than on the badge.
    check("real-world title from the live feed parses",
          metoffice.parse_title(
              "Amber warning of extreme heat affecting London &amp; South East England")
          == ("amber", "extreme heat"))
    check("unparseable title ignored",
          metoffice.parse_title("Something entirely unexpected") == (None, None))
    check("unknown colour ignored",
          metoffice.parse_title("Purple warning of frogs affecting Hove") == (None, None))

    original = metoffice.requests
    try:
        metoffice.requests = types.SimpleNamespace(
            get=lambda url, headers=None: _rss_response(QUIET_RSS)())
        check("quiet feed yields no alerts", metoffice.fetch("se") == [])

        urls = []

        def capture(url, headers=None):
            urls.append(url)
            return _rss_response(BUSY_RSS)()

        metoffice.requests = types.SimpleNamespace(get=capture)
        found = metoffice.fetch("se", "Yellow")
        events = [a["event"] for a in found]
        check("region appended to the feed URL", urls and urls[0].endswith("/se"), urls)
        check("amber sorts above yellow", events and events[0] == "Amber: wind", events)
        check("duplicate hazard collapses to its worst colour",
              len(found) == 2, events)
        check("junk title skipped", "Something entirely unexpected" not in str(events))
        check("levels map from colour",
              [a["level"] for a in found] == [2, 1], [a["level"] for a in found])
        check("keys are stable per hazard",
              sorted(a["key"] for a in found) == ["met:rain", "met:wind"],
              [a["key"] for a in found])

        metoffice.requests = types.SimpleNamespace(get=capture)
        amber_only = metoffice.fetch("se", "Amber")
        check("min_colour=Amber drops the yellows",
              [a["event"] for a in amber_only] == ["Amber: wind"],
              [a["event"] for a in amber_only])

        urls[:] = []
        metoffice.requests = types.SimpleNamespace(get=capture)
        metoffice.fetch("nonsense")
        check("unknown region falls back to the UK-wide feed",
              urls and urls[0].endswith("/uk"), urls)
    finally:
        metoffice.requests = original

    check("Hove's region is in the table", metoffice.REGIONS["se"].startswith("London"))
    check("all 17 documented regions present", len(metoffice.REGIONS) == 17,
          len(metoffice.REGIONS))


def test_metoffice_alerts():
    print("\nMet Office warnings feed the alert rules")
    state_mod.PATH = "/tmp/badgersett_met_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    st = state_mod.State()

    official = [{"key": "met:wind", "event": "Amber: wind", "severity": "Amber",
                 "level": 2, "rank": 2, "ends": None}]
    found = alerts.evaluate(CONFIG, SAMPLE_WEATHER, SAMPLE_INDOOR, st, official)
    check("Met Office warning becomes an alert",
          found and found[0]["key"] == "met:wind" and found[0]["level"] == 2, found)
    check("text is the human-readable warning",
          found[0]["text"] == "Amber: wind", found[0]["text"])

    class FakeHaptic:
        ready = True

        def __init__(self):
            self.played = []

        def alert(self, level):
            self.played.append(level)

    hap = FakeHaptic()
    check("amber buzzes at level 2",
          alerts.notify(CONFIG, found, st, hap, hour=12) == 2, hap.played)

    upgraded = [dict(found[0], level=3, text="Red: wind")]
    check("yellow -> amber -> red escalation buzzes again",
          alerts.notify(CONFIG, upgraded, st, hap, hour=12) == 3, hap.played)
    check("red overrides quiet hours",
          alerts.notify(CONFIG, [dict(found[0], key="met:snow", level=3,
                                      text="Red: snow")], st, hap, hour=3) == 3)
    st.set("alerts", {})
    check("yellow is suppressed during quiet hours",
          alerts.notify(CONFIG, [dict(found[0], key="met:rain", level=1,
                                      text="Yellow: rain")], st, hap, hour=3) == 0)
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


BBC_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
  <title><![CDATA[BBC News]]></title>
  <description><![CDATA[BBC News - News Front Page]]></description>
  <image><url>http://x/y.gif</url><title>BBC News</title><link>http://bbc.co.uk</link></image>
  <copyright><![CDATA[Copyright: (C) British Broadcasting Corporation]]></copyright>
  <item>
    <title><![CDATA[German Chancellor Merz calls state election a 'disaster']]></title>
    <description><![CDATA[%s]]></description>
  </item>
  <item>
    <title><![CDATA[Rain &amp; wind warnings issued for the south coast]]></title>
    <description><![CDATA[%s]]></description>
  </item>
  <item>
    <title>Plain title with no CDATA wrapper</title>
  </item>
  <item>
    <title><![CDATA[A fourth headline]]></title>
  </item>
  <item>
    <title><![CDATA[A fifth headline that should not be fetched]]></title>
  </item>
</channel></rss>""" % ("padding " * 60, "padding " * 60)


def test_news():
    print("\nBBC news feed")

    def respond(body):
        class Raw:
            def __init__(self):
                self.data, self.pos = body.encode(), 0

            def read(self, n):
                chunk = self.data[self.pos:self.pos + n]
                self.pos += len(chunk)
                return chunk

        class Response:
            status_code = 200

            def __init__(self):
                self.raw = Raw()

            def close(self):
                pass

        return Response

    original = news.requests
    try:
        urls = []

        def capture(url, headers=None):
            urls.append(url)
            return respond(BBC_RSS)()

        news.requests = types.SimpleNamespace(get=capture)
        got = news.fetch("top", 4)
        check("four headlines returned", len(got) == 4, got)
        check("CDATA stripped", got[0].startswith("German Chancellor"), got[0])
        check("entities decoded", "&" in got[1] and "&amp;" not in got[1], got[1])
        check("plain (non-CDATA) title also works",
              got[2] == "Plain title with no CDATA wrapper", got[2])
        check("channel and image titles excluded", "BBC News" not in got, got)
        check("stops at the requested count, ignoring later items",
              not any("fifth" in h for h in got), got)
        check("feed key resolved to a BBC URL",
              urls and urls[0] == news.FEEDS["top"], urls)

        news.requests = types.SimpleNamespace(get=capture)
        check("count is honoured", len(news.fetch("top", 2)) == 2)

        urls[:] = []
        news.requests = types.SimpleNamespace(get=capture)
        news.fetch("sussex", 1)
        check("regional feed key works", "sussex" in urls[0], urls)

        urls[:] = []
        news.requests = types.SimpleNamespace(get=capture)
        news.fetch("https://feeds.bbci.co.uk/news/uk/rss.xml", 1)
        check("a full URL passes through", urls[0].endswith("/news/uk/rss.xml"), urls)

        urls[:] = []
        news.requests = types.SimpleNamespace(get=capture)
        news.fetch("not-a-feed", 1)
        check("an unknown key falls back to top stories",
              urls and urls[0] == news.FEEDS["top"], urls)

        # A title split across a chunk boundary must still be recovered.
        news.CHUNK = 7
        news.requests = types.SimpleNamespace(get=capture)
        tiny = news.fetch("top", 4)
        news.CHUNK = 512
        check("titles spanning chunk boundaries survive", len(tiny) == 4, tiny)
        check("same headlines regardless of chunk size", tiny == got, tiny)

        class Dead:
            status_code = 503

            def close(self):
                pass

        news.requests = types.SimpleNamespace(get=lambda url, headers=None: Dead())
        check("an HTTP error returns no headlines, and does not raise",
              news.fetch("top", 4) == [])
    finally:
        news.requests = original


def test_wrap():
    print("\nword wrap")
    d = FakeDisplay()
    d.set_font("bitmap6")
    long_line = ("German Chancellor Merz calls state election a 'disaster' "
                 "for his party but vows to stay on")
    lines = util.wrap(d, long_line, 274, 1, 2)
    check("wraps to the requested number of lines", len(lines) == 2, lines)
    check("every line fits the width",
          all(d.measure_text(l, 1) <= 274 for l in lines),
          [(l, d.measure_text(l, 1)) for l in lines])
    check("no words are lost mid-wrap",
          lines[0].startswith("German Chancellor"), lines[0])
    check("a headline that fits is NOT marked truncated",
          not lines[-1].endswith(".."), lines[-1])

    # Same text forced into one line must be ellipsised.
    one = util.wrap(d, long_line, 274, 1, 1)
    check("overflow is marked with an ellipsis", one[-1].endswith(".."), one[-1])
    check("the ellipsised line still fits", d.measure_text(one[-1], 1) <= 274,
          d.measure_text(one[-1], 1))

    short = util.wrap(d, "Short one", 274, 1, 2)
    check("short text stays on one line, unmarked",
          short == ["Short one"], short)

    huge = util.wrap(d, "Supercalifragilisticexpialidocious" * 3, 60, 1, 2)
    check("an unbreakable word is hard-broken rather than overflowing",
          all(d.measure_text(l, 1) <= 60 for l in huge), huge)
    check("empty text degrades to a single empty line", util.wrap(d, "", 100) == [""])


def test_gas_warmup_gate():
    print("\nBME688 gas warm-up gate")
    from badgersett import sensor as sensor_mod

    bus = FakeI2C()
    bme = sensor_mod.Sensor(bus, 0x77)
    check("sensor comes up", bme.ok, bme.error)

    # samples=2 so the first (warm, dry, pre-heater) conversion is discarded,
    # exactly as the app does with samples=4.
    cold = bme.read(samples=2, settle=0, warmup=300)
    check("a cold reading reports gas_raw", cold["gas_raw"] == 120000.0, cold["gas_raw"])
    check("...but does not trust it", cold["gas"] is None and not cold["gas_trusted"],
          (cold["gas"], cold["gas_trusted"]))
    check("temperature is trusted immediately", cold["temp_c"] == 22.4, cold["temp_c"])
    check("humidity is trusted immediately", cold["humidity"] == 47.0)

    # Pretend the heater has been running for ten minutes.
    bme.heater_started = time.ticks_add(time.ticks_ms(), -600000)
    warm = bme.read(samples=2, settle=0, warmup=300)
    check("after warm-up the gas reading is trusted",
          warm["gas"] == 120000.0 and warm["gas_trusted"], (warm["gas"], warm["gas_trusted"]))
    check("warm seconds are reported", warm["gas_warm_s"] >= 600, warm["gas_warm_s"])

    # The gate must feed through to the baseline and the alerts.
    state_mod.PATH = "/tmp/badgersett_gas_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    st = state_mod.State()
    st.update_gas_baseline(cold["gas"], 20)
    check("an untrusted reading never enters the baseline",
          st.get("gas_baseline") is None, st.get("gas_baseline"))
    st.update_gas_baseline(warm["gas"], 20)
    check("a trusted reading does", st.get("gas_baseline") == 120000.0,
          st.get("gas_baseline"))

    for _ in range(8):
        st.update_gas_baseline(warm["gas"], 20)
    bad_cold = dict(cold, gas=None, gas_raw=30000.0)
    check("no gas alert can fire from an untrusted reading",
          not any(a["key"] == "in:gas"
                  for a in alerts.evaluate(CONFIG, SAMPLE_WEATHER, bad_cold, st, [])))
    bad_warm = dict(warm, gas=30000.0, gas_raw=30000.0)
    check("a trusted drop still alerts",
          any(a["key"] == "in:gas"
              for a in alerts.evaluate(CONFIG, SAMPLE_WEATHER, bad_warm, st, [])))
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None

    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, cold, st, [],
                                       {"updated": "Updated 23:45", "online": True,
                                        "muted": False, "sensor": True, "view": 1,
                                        "credit": "Open-Meteo"})
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("the screen says 'warming', not an air-quality verdict",
          "warming" in drawn and "clean" not in drawn, drawn[-80:])


def test_buttons():
    print("\nbutton mapping and the secret chord")
    from badgersett import app

    check("A+C+UP is the secret chord", app._is_secret_chord(["A", "C", "UP"]))
    check("order does not matter", app._is_secret_chord(["UP", "C", "A"]))
    check("extra buttons still count", app._is_secret_chord(["A", "B", "C", "UP"]))
    check("A+C alone is not the chord", not app._is_secret_chord(["A", "C"]))
    check("A+UP alone is not the chord", not app._is_secret_chord(["A", "UP"]))
    check("C+UP alone is not the chord", not app._is_secret_chord(["C", "UP"]))
    check("a bare A is not the chord", not app._is_secret_chord(["A"]))
    check("nothing pressed is not the chord", not app._is_secret_chord([]))
    check("the reserved action is a no-op returning None",
          app._secret_action(None, None) is None)
    check("chord constant is A, C and UP",
          sorted(app.SECRET_CHORD) == ["A", "C", "UP"], app.SECRET_CHORD)
    check("views are badge/detail/news/secret",
          (ui.VIEW_BADGE, ui.VIEW_DETAIL, ui.VIEW_NEWS, ui.VIEW_SECRET) == (0, 1, 2, 3))


def test_app_cycle():
    print("\nfull wake cycle (app.py)")
    from badgersett import app, nws as nws_mod, weather as weather_mod

    state_mod.PATH = "/tmp/badgersett_app_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)

    open_meteo = {
        "current": {"time": "2026-09-20T17:15", "temperature_2m": 19.7,
                    "relative_humidity_2m": 47, "apparent_temperature": 17.7,
                    "is_day": 1, "precipitation": 0.0, "weather_code": 96,
                    "wind_speed_10m": 10.4, "wind_gusts_10m": 23.0,
                    "wind_direction_10m": 210},
        "utc_offset_seconds": 3600,
        "daily": {"time": ["2026-09-20", "2026-09-21"], "weather_code": [96, 3],
                  "temperature_2m_max": [19.9, 21.5], "temperature_2m_min": [15.2, 13.5],
                  "precipitation_probability_max": [38, 0],
                  "wind_gusts_10m_max": [95.0, 16.2]},
    }

    class MeteoResponse:
        status_code = 200

        def json(self):
            return open_meteo

        def close(self):
            pass

    calls = []
    met_rss = _rss_response(BUSY_RSS)

    def bbc_response():
        class Raw:
            def __init__(self):
                self.data, self.pos = BBC_RSS.encode(), 0

            def read(self, n):
                chunk = self.data[self.pos:self.pos + n]
                self.pos += len(chunk)
                return chunk

        class Response:
            status_code = 200

            def __init__(self):
                self.raw = Raw()

            def close(self):
                pass

        return Response()

    def fake_get(url, headers=None):
        calls.append(url)
        if "metoffice" in url:
            return met_rss()
        if "bbci.co.uk" in url:
            return bbc_response()
        return MeteoResponse()

    from badgersett import metoffice as met_mod, news as news_mod
    fake_requests = types.SimpleNamespace(get=fake_get)
    weather_mod.requests = fake_requests
    nws_mod.requests = fake_requests
    met_mod.requests = fake_requests
    news_mod.requests = fake_requests

    displays = []
    original_display = sys.modules["badger2040"].Badger2040

    def make_display():
        display = FakeDisplay()
        displays.append(display)
        return display

    sys.modules["badger2040"].Badger2040 = make_display

    class StopLoop(BaseException):   # NOT Exception: app.py catches those as a sleep failure
        pass

    slept = []

    def fake_sleep(minutes):
        slept.append(minutes)
        raise StopLoop()

    sys.modules["badger2040"].sleep_for = fake_sleep

    try:
        app.run()
    except StopLoop:
        pass
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("one full wake cycle completes", False, exc)
        return
    finally:
        sys.modules["badger2040"].Badger2040 = original_display

    check("one full wake cycle completes", True)
    check("the screen was drawn exactly once", displays and displays[0].updates == 1,
          displays[0].updates if displays else "no display")
    check("it slept for the configured interval", slept == [CONFIG.REFRESH_MINUTES], slept)
    check("the forecast was fetched", any("open-meteo" in u for u in calls), calls)
    check("Met Office warnings were fetched",
          any("metoffice.gov.uk" in u and u.endswith("/se") for u in calls), calls)
    check("the US NWS was not contacted",
          not any("weather.gov" in u for u in calls), calls)

    saved = state_mod.State()
    check("forecast cached for the next button wake",
          (saved.get("weather") or {}).get("temp") == 19.7, saved.get("weather"))
    check("indoor reading cached",
          (saved.get("indoor") or {}).get("humidity") == 47.0, saved.get("indoor"))
    check("update timestamp recorded", saved.get("updated") == "2026-09-20T17:15",
          saved.get("updated"))
    latched = sorted((saved.get("alerts") or {}).keys())
    check("forecast rules and Met Office warnings latch together",
          latched == ["met:rain", "met:wind", "wx:code:96", "wx:gust"], latched)
    # A cold cycle must NOT seed the baseline: the gas heater has only been
    # running for milliseconds, so the reading is mid burn-in and worthless.
    check("a cold cycle does not seed the gas baseline",
          saved.get("gas_baseline") is None, saved.get("gas_baseline"))
    check("BBC headlines were fetched",
          any("bbci.co.uk" in u for u in calls), calls)
    cached_news = saved.get("news") or []
    check("headlines cached for an offline button-C press",
          len(cached_news) == CONFIG.NEWS_HEADLINES, cached_news)
    check("cached headline text is clean",
          cached_news and cached_news[0].startswith("German Chancellor"), cached_news[:1])
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


def main():
    print("BadgerSett offline checks")
    test_util()
    test_state()
    test_nws_stream()
    test_weather_codes()
    test_alerts()
    test_haptics()
    test_metoffice()
    test_metoffice_alerts()
    test_news()
    test_wrap()
    test_gas_warmup_gate()
    test_buttons()
    test_layouts()
    test_app_cycle()
    print("\n%s" % ("-" * 46))
    if FAILURES:
        print("%d check(s) FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
