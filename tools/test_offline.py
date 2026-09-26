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

    def set_framebuffer(self, buf):
        self.framebuffer = buf

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
        # Settable, so clock code can be exercised properly.
        _dt = [2026, 9, 20, 0, 17, 15, 0, 0]

        def datetime(self, value=None):
            if value is not None:
                RTC._dt = list(value)
                return None
            return tuple(RTC._dt)

    machine.RTC = RTC

    # On MicroPython time.time() is derived from the RTC. Model that, or
    # clock code that converts between them cannot be tested honestly.
    # localtime==gmtime there too: no timezone database on the badge.
    import calendar

    def _rtc_time():
        d = RTC._dt
        return calendar.timegm((d[0], d[1], d[2], d[4], d[5], d[6], 0, 0, 0))

    time.time = _rtc_time
    time.localtime = time.gmtime
    class _Pin:
        IN = 0
        OUT = 1
        usb_present = 1          # tests flip this to simulate battery

        def __init__(self, name, mode=None, *a, **kw):
            self.name = name

        def value(self, v=None):
            return _Pin.usb_present if self.name == "WL_GPIO2" else 0

    machine.Pin = _Pin

    class _ADC:
        value = 31774            # ~4.80 V on VSYS: USB, as the badge sits on the bench
        def __init__(self, channel):
            pass

        def read_u16(self):
            return _ADC.value

    machine.ADC = _ADC
    machine.I2C = lambda *a, **kw: FakeI2C()
    sys.modules["machine"] = machine

    network = types.ModuleType("network")
    network.STA_IF = 0
    network.STAT_WRONG_PASSWORD = -3
    network.STAT_NO_AP_FOUND = -2
    network.STAT_CONNECT_FAIL = -1
    network.country = lambda c: None

    class WLAN:
        visible = [(b"OfficeNet", b"\x00", 1, -55, 0, False),
                   (b"HomeNet", b"\x01", 6, -78, 0, False),
                   (b"HomeNet", b"\x02", 11, -66, 0, False),   # same SSID, 2 APs
                   (b"", b"\x03", 3, -40, 0, True),            # hidden
                   (b"Neighbour", b"\x04", 9, -90, 0, False)]
        attempts = []
        joinable = None          # None = anything works

        def __init__(self, mode):
            pass

        is_active = False

        def active(self, on=None):
            if on is not None:
                WLAN.is_active = bool(on)
                return None
            return WLAN.is_active

        def config(self, **kw):
            pass

        def scan(self):
            return WLAN.visible

        def isconnected(self):
            if not WLAN.attempts:
                return False
            if WLAN.joinable is None:
                return True
            return WLAN.attempts[-1] == WLAN.joinable

        def connect(self, ssid, password=None):
            WLAN.attempts.append(ssid)

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

    bt = types.ModuleType("bluetooth")

    def adv(name=None, company=None):
        out = b""
        if name:
            out += bytes([1 + len(name), 0x09]) + name.encode()
        if company is not None:
            payload = bytes([company & 0xFF, company >> 8]) + b"\x01\x02"
            out += bytes([1 + len(payload), 0xFF]) + payload
        return out

    class FakeBLE:
        # (addr_type, addr, adv_type, rssi, adv)
        devices = [
            (0, b"\x11\x22\x33\x44\x55\x66", 0, -43, adv("PowerStation-42")),
            (1, b"\x7f\xff\xff\xaa\xbb\xcc", 0, -53, adv(None, 0x004C)),
            (1, b"\x7f\xff\xff\xaa\xbb\xcc", 0, -49, adv(None, 0x004C)),  # again, louder
            (1, b"\x40\x00\x00\x01\x02\x03", 0, -71, adv(None, 0x00E0)),
            (0, b"\x00\x11\x22\x33\x44\x55", 0, -88, adv("TY", 0x07D0)),
            # Advertises neither a name nor a maker: only an address to go on.
            (1, b"\xc0\xde\xca\xfe\xba\xbe", 0, -95, b""),
        ]
        fail = False

        def __init__(self):
            self._irq = None
            self._active = False

        def active(self, on=None):
            if on is not None:
                self._active = bool(on)
                return None
            return self._active

        def irq(self, handler):
            self._irq = handler

        def gap_scan(self, duration_ms, interval_us, window_us, active):
            if FakeBLE.fail:
                raise OSError("radio busy")
            for device in FakeBLE.devices:
                self._irq(5, device)
            self._irq(6, None)

    # Real MicroPython returns one shared BLE object; model that, or the
    # "is the radio up?" guard cannot be tested.
    _ble_singleton = []

    def _get_ble():
        if not _ble_singleton:
            _ble_singleton.append(FakeBLE.__new__(FakeBLE))
            FakeBLE.__init__(_ble_singleton[0])
        return _ble_singleton[0]

    bt.BLE = _get_ble
    bt.FakeBLE = FakeBLE
    sys.modules["bluetooth"] = bt

    ntp = types.ModuleType("ntptime")
    ntp.host = "pool.ntp.org"
    ntp.calls = []

    def _settime():
        ntp.calls.append(ntp.host)
        if getattr(ntp, "fail", False):
            raise OSError("ntp timeout")
        machine.RTC().datetime((2026, 9, 20, 0, 16, 15, 0, 0))   # UTC

    ntp.settime = _settime
    sys.modules["ntptime"] = ntp

    urequests = types.ModuleType("urequests")
    urequests.get = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("no network in the offline harness"))
    sys.modules["urequests"] = urequests

    cfg = types.ModuleType("config")
    cfg.NAME = "Christopher Wenham"
    cfg.TITLE = "Principal Engineer"
    cfg.ORG = "Badger Industries"
    cfg.PHOTO = None          # most layout tests use the initials fallback
    cfg.WIFI_NETWORKS = [
        {"label": "Home", "ssid": "HomeNet", "password": "hpw",
         "lat": 50.8279, "lon": -0.1687, "met_region": "se", "altitude": 10},
        {"label": "Office", "ssid": "OfficeNet", "password": "opw",
         "lat": 53.4808, "lon": -2.2426, "met_region": "nw", "altitude": 38},
    ]
    cfg.WIFI_MAX_ATTEMPTS = 3
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
    cfg.BME688_ENABLED = True
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
    cfg.GAS_MAX_GAP_S = 60
    # Existing tests exercise the sleep path; awake mode has its own tests.
    cfg.POWER_MODE = "sleep"
    cfg.GAS_SAMPLE_S = 5
    cfg.AWAKE_REDRAW_MINUTES = 5
    cfg.RUNTIME_LOG = False
    cfg.RUNTIME_LOG_MINUTES = 10
    cfg.LOW_BATTERY_V = 3.2
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
    display = FakeDisplay()
    clean = dict(SAMPLE_INDOOR, gas=138000.0, gas_raw=138000.0)   # 115% of baseline
    st.set("gas_baseline", 120000.0)
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, clean, st, [], status)
    air = [str(c[1][0]) for c in display.calls
           if c[0] == "text" and str(c[1][0]).startswith("Air")]
    check("air is shown as a signed deviation, not a >100% figure",
          air and air[0].startswith("Air +15%"), air)
    dirty = dict(SAMPLE_INDOOR, gas=60000.0, gas_raw=60000.0)     # 50%
    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, dirty, st, [], status)
    air = [str(c[1][0]) for c in display.calls
           if c[0] == "text" and str(c[1][0]).startswith("Air")]
    check("a drop reads as a negative deviation and POOR",
          air and air[0].startswith("Air -50%") and "POOR" in air[0], air)
    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, SAMPLE_INDOOR, st, [], status)

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

        # A dead feed must not read as an all-clear: [] means "nothing in
        # force", None means "we do not know". Conflating them once hid a
        # month of silently failing warnings.
        def boom(url, headers=None):
            raise OSError(12, "ENOMEM")

        metoffice.requests = types.SimpleNamespace(get=boom)
        check("a failed fetch returns None, not an empty all-clear",
              metoffice.fetch("se") is None)

        class Gone:
            status_code = 503

            def close(self):
                pass

        metoffice.requests = types.SimpleNamespace(get=lambda u, headers=None: Gone())
        check("an HTTP error returns None too", metoffice.fetch("se") is None)
        metoffice.requests = types.SimpleNamespace(
            get=lambda url, headers=None: _rss_response(QUIET_RSS)())

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

    # On battery the heater is not run at all.
    battery = bme.read(samples=2, settle=0, warmup=300, gas_enabled=False)
    check("on battery the gas reading is refused outright",
          battery["gas"] is None and battery["gas_reason"] == "usb only",
          (battery["gas"], battery["gas_reason"]))
    check("...even once the heater would have been warm enough",
          bme.read(samples=2, settle=0, warmup=0, gas_enabled=False)["gas"] is None)
    check("temperature still works on battery", battery["temp_c"] == 22.4)

    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, battery, st, [],
                                       {"updated": "u", "online": True, "muted": False,
                                        "sensor": True, "view": 1, "credit": "c"})
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("the screen explains gas is USB-only on battery",
          "usb only" in drawn.lower(), drawn[-70:])


def set_supply(volts):
    """Put the fake VSYS at `volts` (USB ~4.8, a LiPo 3.0-4.2)."""
    sys.modules["machine"].ADC.value = int(volts / (3 * 3.3) * 65535)


def test_power_detection():
    print("\npower source detection")
    from badgersett import power
    machine_mod = sys.modules["machine"]
    WLAN = sys.modules["network"].WLAN
    WLAN.is_active = False

    # The VBUS pin is the primary signal: one pin read, nothing disturbed.
    machine_mod.Pin.usb_present = 1
    set_supply(4.8)
    check("VBUS high reads as USB", power.on_usb() is True)
    machine_mod.Pin.usb_present = 0
    set_supply(3.9)
    check("VBUS low reads as battery", power.on_usb() is False)

    # VSYS is only the fallback, for when the pin cannot be read at all.
    broken = machine_mod.Pin

    class NoVbusPin(broken):
        """Only the named VBUS pin fails; numbered pins still work, so the
        VSYS fallback has something to stand on."""

        def __init__(self, name, mode=None, *a, **kw):
            if name == "WL_GPIO2":
                raise RuntimeError("no such pin")
            broken.__init__(self, name, mode, *a, **kw)

    machine_mod.Pin = NoVbusPin
    check("with no VBUS pin it falls back to VSYS (battery)",
          power.on_usb() is False)
    set_supply(4.8)
    check("...and the same fallback sees USB at 4.8V", power.on_usb() is True)
    machine_mod.Pin = broken
    machine_mod.Pin.usb_present = 1
    set_supply(4.8)

    WLAN.is_active = True
    check("VSYS refuses to read while the WiFi radio is up",
          power.vsys() is None)
    WLAN.is_active = False
    check("...and reads once it is down", power.vsys() is not None)


def test_wifi_selection():
    print("\nmulti-network WiFi")
    from badgersett import net
    WLAN = sys.modules["network"].WLAN

    nets = net.networks_from_config(CONFIG)
    check("both configured networks parsed", len(nets) == 2, nets)
    check("config order is preserved",
          [n["ssid"] for n in nets] == ["HomeNet", "OfficeNet"])
    check("each network carries its own location",
          nets[0]["lat"] == 50.8279 and nets[1]["lat"] == 53.4808)
    check("each carries its own warnings region",
          (nets[0]["met_region"], nets[1]["met_region"]) == ("se", "nw"))
    check("each carries its own altitude",
          (nets[0]["altitude"], nets[1]["altitude"]) == (10, 38))

    visible = net.scan_ssids(WLAN(0))
    check("scan de-duplicates an SSID seen on two APs",
          sorted(visible) == ["HomeNet", "Neighbour", "OfficeNet"], sorted(visible))
    check("the strongest AP wins for a duplicated SSID",
          visible["HomeNet"] == -66, visible["HomeNet"])
    check("hidden (empty) SSIDs are skipped", "" not in visible)

    # The headline requirement: config order beats signal strength.
    ordered = net.select_networks(nets, visible)
    check("preferred network wins despite a weaker signal",
          ordered[0]["ssid"] == "HomeNet", [n["ssid"] for n in ordered])

    ordered = net.select_networks(nets, {"OfficeNet": -55})
    check("an out-of-range preference yields to one in range",
          ordered[0]["ssid"] == "OfficeNet", [n["ssid"] for n in ordered])
    check("...but the absent one is still kept as a fallback",
          ordered[1]["ssid"] == "HomeNet", [n["ssid"] for n in ordered])

    ordered = net.select_networks(nets, {})
    check("with nothing visible, config order is tried anyway (hidden SSIDs)",
          [n["ssid"] for n in ordered] == ["HomeNet", "OfficeNet"])

    WLAN.attempts, WLAN.joinable = [], None
    link, active = net.connect_best(nets, "GB", 5)
    check("connect_best joins the preferred network",
          active and active["ssid"] == "HomeNet", active)
    check("it reports the location that network implies",
          active["label"] == "Home" and active["met_region"] == "se", active)
    check("only one connection was attempted", WLAN.attempts == ["HomeNet"],
          WLAN.attempts)

    # Preferred network present but unjoinable (wrong password): fall through.
    WLAN.attempts, WLAN.joinable = [], "OfficeNet"
    link, active = net.connect_best(nets, "GB", 1)
    check("a failing preferred network falls through to the next",
          active and active["ssid"] == "OfficeNet", active)
    check("both were tried, in preference order",
          WLAN.attempts == ["HomeNet", "OfficeNet"], WLAN.attempts)

    WLAN.attempts, WLAN.joinable = [], "NothingHere"
    link, active = net.connect_best(nets, "GB", 1)
    check("no joinable network returns (None, None)",
          link is None and active is None, (link, active))

    check("max_attempts caps how many are tried",
          len(WLAN.attempts) <= 3, WLAN.attempts)

    WLAN.attempts, WLAN.joinable = [], None
    check("an empty network list is handled",
          net.connect_best([], "GB", 1) == (None, None))

    # Legacy single-network config must still work.
    legacy = types.SimpleNamespace(WIFI_SSID="OldNet", WIFI_PASSWORD="p",
                                   LATITUDE=1.0, LONGITUDE=2.0,
                                   MET_REGION="wl", ALTITUDE_M=5)
    one = net.networks_from_config(legacy)
    check("a legacy WIFI_SSID config still works",
          len(one) == 1 and one[0]["ssid"] == "OldNet", one)
    check("legacy entry inherits the top-level location",
          one[0]["lat"] == 1.0 and one[0]["met_region"] == "wl", one[0])
    check("a config with no wifi at all yields an empty list",
          net.networks_from_config(types.SimpleNamespace()) == [])

    malformed = types.SimpleNamespace(WIFI_NETWORKS=[
        {"ssid": "Good", "password": "x"}, {"password": "no ssid"}, "junk"])
    check("malformed entries are skipped, not fatal",
          [n["ssid"] for n in net.networks_from_config(malformed)] == ["Good"])


class VirtualTime:
    """Stands in for app.time so the awake loop runs instantly."""

    def __init__(self):
        self.now = 1000

    def ticks_ms(self):
        return self.now

    def ticks_add(self, t, d):
        return t + d

    def ticks_diff(self, a, b):
        return a - b

    def sleep_ms(self, ms):
        self.now += ms

    def sleep(self, s):
        self.now += int(s * 1000)


def test_power_modes():
    print("\npower modes and the awake loop")
    from badgersett import app, runlog as runlog_mod
    Pin = sys.modules["machine"].Pin

    for mode, usb, expected in (("awake", 0, True), ("awake", 1, True),
                                ("sleep", 0, False), ("sleep", 1, False),
                                ("auto", 1, True), ("auto", 0, False)):
        Pin.usb_present = usb
        set_supply(4.8 if usb else 3.9)
        check("%s on %s -> %s" % (mode, "USB" if usb else "battery",
                                  "awake" if expected else "sleep"),
              app._stay_awake(mode) is expected)
    Pin.usb_present = 1
    set_supply(4.8)

    CONFIG.POWER_MODE = "nonsense"
    check("an unknown POWER_MODE falls back to auto", app._power_mode() == "auto")
    CONFIG.POWER_MODE = "sleep"

    # A display whose buttons follow a script, one frame per poll.
    class ScriptedDisplay(FakeDisplay):
        def __init__(self, frames):
            FakeDisplay.__init__(self)
            self.frames = list(frames)
            self.polls = 0

        def pressed(self, pin):
            names = dict((p, n) for p, n in app._BUTTONS)
            frame = self.frames[min(self.polls // len(app._BUTTONS),
                                    len(self.frames) - 1)] if self.frames else ()
            self.polls += 1
            return names[pin] in frame

    vt = VirtualTime()
    real_time = app.time
    app.time = vt
    try:
        # The chord: A and C held, then UP added, then everything released.
        frames = [("A",), ("A", "C"), ("A", "C", "UP"), ("A", "C", "UP"), (), (), ()] + [()] * 20
        got = app._collect_buttons(ScriptedDisplay(frames))
        check("a held chord is collected whole", sorted(got) == ["A", "C", "UP"], got)
        check("...and recognised as the secret chord", app._is_secret_chord(got))

        got = app._collect_buttons(ScriptedDisplay([("B",), ()] + [()] * 20))
        check("a single press stays a single press", got == ["B"], got)

        class StubBME:
            ok = True

            def __init__(self, reading):
                self.reading, self.reads = reading, 0

            def read(self, **kw):
                self.reads += 1
                return dict(self.reading)

        log = runlog_mod.RunLog(enabled=False)
        st_path = "/tmp/badgersett_awake_state.json"
        state_mod.PATH = st_path
        if os.path.exists(st_path):
            os.remove(st_path)
        st = state_mod.State()
        from badgersett import clock
        st.set("weather", dict(SAMPLE_WEATHER))
        st.set("last_refresh", clock.minutes())       # fresh: no refresh due
        CONFIG.GAS_SAMPLE_S, CONFIG.AWAKE_REDRAW_MINUTES = 5, 5

        calm = StubBME(dict(SAMPLE_INDOOR))
        app._last_attempt = None
        reason, pressed, _ = app._awake_wait(ScriptedDisplay([()]), calm, st, log, 30)
        check("with nothing happening it wakes for the redraw timer",
              reason == "redraw", reason)
        check("it sampled the sensor repeatedly to keep the heater warm",
              calm.reads >= 50, calm.reads)      # 5 min / 5 s = 60

        reason, pressed, _ = app._awake_wait(
            ScriptedDisplay([(), (), ("C",), ()] + [()] * 20), calm, st, log, 30)
        check("a button press ends the wait early", reason == "button", reason)
        check("the button is handed back to the main loop", pressed == ["C"], pressed)

        # A tap so brief it is gone by the time collection starts.
        reason, pressed, _ = app._awake_wait(
            ScriptedDisplay([(), ("B",)] + [()] * 30), calm, st, log, 30)
        check("even a very brief tap is not lost", pressed == ["B"], (reason, pressed))

        for _ in range(8):
            st.update_gas_baseline(120000.0, 20)
        st.set("alerts", {})
        smoky = StubBME(dict(SAMPLE_INDOOR, gas=30000.0, gas_raw=30000.0))
        reason, _, carried = app._awake_wait(ScriptedDisplay([()]), smoky, st, log, 30)
        check("a gas drop wakes it", reason == "alert", reason)
        check("...once confirmed by a second sample, not on one noisy reading",
              smoky.reads == 2, smoky.reads)
        check("the triggering reading is handed back to the main loop",
              carried and carried["gas"] == 30000.0, carried)

        class Blip:
            ok = True

            def __init__(self):
                self.reads = 0

            def read(self, **kw):
                self.reads += 1
                gas = 30000.0 if self.reads == 1 else 120000.0   # one bad sample
                return dict(SAMPLE_INDOOR, gas=gas, gas_raw=gas)

        st.set("alerts", {})
        reason, _, _ = app._awake_wait(ScriptedDisplay([()]), Blip(), st, log, 30)
        check("a single noisy sample does not wake it", reason == "redraw", reason)

        # Once latched, the same alert must not keep firing, or the e-ink
        # would be redrawn every five seconds for as long as it persists.
        st.set("alerts", {"in:gas": 3})
        reason, _, _ = app._awake_wait(ScriptedDisplay([()]), smoky, st, log, 30)
        check("an already-latched alert does not re-trigger", reason == "redraw", reason)

        st.set("alerts", {})
        st.set("last_refresh", None)                  # due
        app._last_attempt = None
        reason, _, _ = app._awake_wait(ScriptedDisplay([()]), calm, st, log, 30)
        check("a due refresh wakes it", reason == "refresh", reason)

        # Just tried and failed: must NOT spin on the radio. The redraw timer
        # is set shorter than RETRY_S so the two cannot expire together.
        CONFIG.AWAKE_REDRAW_MINUTES = 2
        app._last_attempt = vt.ticks_ms()
        reason, _, _ = app._awake_wait(ScriptedDisplay([()]), calm, st, log, 30)
        check("after a failed attempt it backs off instead of retrying at once",
              reason == "redraw", reason)
        CONFIG.AWAKE_REDRAW_MINUTES = 5
        vt.now += app.RETRY_S * 1000
        reason, _, _ = app._awake_wait(ScriptedDisplay([()]), calm, st, log, 30)
        check("...and retries once RETRY_S has passed", reason == "refresh", reason)
        os.remove(st_path) if os.path.exists(st_path) else None
    finally:
        app.time = real_time
        app._last_attempt = None


def test_redraw_flipflop():
    print("\nregression: the ~28 second redraw loop")
    from badgersett import app, runlog as runlog_mod

    class CadenceBME:
        """A gas channel whose value depends on how it is sampled, as the
        real BME688's does: a burst of heater pulses reads high, a single
        paced sample reads several times lower."""
        ok = True

        def __init__(self):
            self.burst_reads = 0

        def read(self, samples=4, settle=0.35, warmup=None, gas_enabled=True):
            if samples > 1:
                self.burst_reads += 1
            gas = 76000.0 if samples > 1 else 17000.0
            return dict(SAMPLE_INDOOR, gas=gas, gas_raw=gas,
                        gas_trusted=True, gas_reason="ok")

    class Quiet(FakeDisplay):
        def pressed(self, pin):
            return False

    class Haptic:
        ready = True

        def __init__(self):
            self.played = []

        def alert(self, level):
            self.played.append(level)

    vt = VirtualTime()
    real_time = app.time
    app.time = vt
    log = runlog_mod.RunLog(enabled=False)

    def fresh_state():
        state_mod.PATH = "/tmp/badgersett_flipflop.json"
        if os.path.exists(state_mod.PATH):
            os.remove(state_mod.PATH)
        st = state_mod.State()
        from badgersett import clock
        st.set("weather", dict(SAMPLE_WEATHER))
        st.set("last_refresh", clock.minutes())
        # The baseline the main loop learned from its own burst readings.
        st.set("gas_baseline", 76000.0)
        st.set("gas_n", 20)
        return st

    def run_cycles(judge, cycles=12):
        st, bme, hap = fresh_state(), CadenceBME(), Haptic()
        reasons, carried = [], None
        for _ in range(cycles):
            reason, _, carried = app._awake_wait(Quiet(), bme, st, log, 30)
            reasons.append(reason)
            reading = judge(bme, carried)
            current = alerts.evaluate(CONFIG, None, reading, st, None)
            alerts.notify(CONFIG, current, st, hap, hour=12)
        return reasons, hap.played, bme

    try:
        # How it was: the main loop took its own (burst) reading.
        before, buzzes, _ = run_cycles(lambda bme, carried: bme.read())
        check("REPRODUCED: judging a different reading woke it every cycle",
              before.count("alert") == len(before), before)
        check("...silently - the main loop never agreed, so it never buzzed",
              buzzes == [], buzzes)

        # Now: the main loop judges the awake loop's own reading.
        after, buzzes, bme = run_cycles(
            lambda bme, carried: app._indoor_reading(bme, True, carried))
        check("FIXED: the alert fires once and then stays latched",
              after[0] == "alert" and after[1:].count("alert") == 0, after)
        check("...the rest are ordinary timed redraws",
              after[1:] == ["redraw"] * (len(after) - 1), after)
        check("...and it buzzes once, as a real alert should", buzzes == [3], buzzes)
        check("awake, the main loop takes no burst reading of its own",
              bme.burst_reads == 0, bme.burst_reads)

        check("with nothing carried (first pass after boot) it reads normally",
              app._indoor_reading(CadenceBME(), True, None)["gas"] == 76000.0)
        check("asleep, it always reads fresh",
              app._indoor_reading(CadenceBME(), False, {"gas": 1})["gas"] == 76000.0)
    finally:
        app.time = real_time
        if os.path.exists(state_mod.PATH):
            os.remove(state_mod.PATH)


def test_battery_cutoff():
    print("\nlow-battery cutoff")
    from badgersett import app
    Pin = sys.modules["machine"].Pin
    ADC = sys.modules["machine"].ADC
    WLAN = sys.modules["network"].WLAN
    WLAN.is_active = False

    def volts(v):
        ADC.value = int(v / (3 * 3.3) * 65535)

    volts(3.9)
    check("a healthy battery is left alone", app._battery_low() is None)
    volts(4.8)
    check("USB's 4.8V never trips it", app._battery_low() is None)
    volts(3.1)
    low = app._battery_low()
    check("below the cutoff it reports the voltage", low is not None and low < 3.2, low)

    # The bug: on_usb() returned True whenever the VBUS pin could not be
    # read, and the cutoff trusted it - silently switching itself off.
    broken = sys.modules["machine"].Pin
    real_on_usb = __import__("badgersett.power", fromlist=["on_usb"]).on_usb
    import badgersett.power as power_mod
    power_mod.on_usb = lambda: True          # "I think this is USB"
    check("REGRESSION: a wrong 'on USB' answer can no longer disable the cutoff",
          app._battery_low() is not None)
    power_mod.on_usb = real_on_usb
    CONFIG.LOW_BATTERY_V = None
    check("LOW_BATTERY_V = None disables it", app._battery_low() is None)
    CONFIG.LOW_BATTERY_V = 3.2

    # And in the real loop: it must power off before doing anything else.
    state_mod.PATH = "/tmp/badgersett_lowbat.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    volts(3.05)

    class Off(BaseException):
        pass

    calls = []
    badger = sys.modules["badger2040"]
    original_off = getattr(badger, "turn_off", None)

    def fake_off():
        calls.append("off")
        raise Off()

    badger.turn_off = fake_off
    displays = []
    original_display = badger.Badger2040

    def make_display():
        d = FakeDisplay()
        displays.append(d)
        return d

    badger.Badger2040 = make_display
    try:
        app.run()
    except Off:
        pass
    finally:
        badger.Badger2040 = original_display
        if original_off:
            badger.turn_off = original_off
    check("the loop powers off at the cutoff", calls == ["off"], calls)
    drawn = " ".join(str(c[1][0]) for c in displays[0].calls if c[0] == "text")
    check("it leaves a BATTERY LOW message on the e-ink", "BATTERY LOW" in drawn, drawn[:60])
    check("the message says this board cannot charge it",
          "cannot charge" in drawn, drawn[-120:])
    volts(4.8)
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


def test_gas_gap_reset():
    print("\ngas conditioning must be continuous")
    from badgersett import sensor as sensor_mod
    bme = sensor_mod.Sensor(FakeI2C(), 0x77)
    bme.heater_started = time.ticks_add(time.ticks_ms(), -600000)   # 10 min ago
    bme._last_read = time.ticks_add(time.ticks_ms(), -2000)         # sampled 2s ago
    r = bme.read(samples=2, settle=0, warmup=300, gas_enabled=True)
    check("regular sampling keeps the conditioning time", r["gas_trusted"],
          (r["gas_warm_s"], r["gas_reason"]))

    # The bug this fixes: a long idle wait with the object kept alive.
    bme._last_read = time.ticks_add(time.ticks_ms(), -30 * 60 * 1000)
    r = bme.read(samples=2, settle=0, warmup=300, gas_enabled=True)
    check("a 30-minute idle gap restarts conditioning", not r["gas_trusted"],
          (r["gas_warm_s"], r["gas_reason"]))
    check("...and says it is warming, not ok", r["gas_reason"].startswith("warming"),
          r["gas_reason"])


def test_pressure_spacing():
    print("\npressure history spacing")
    state_mod.PATH = "/tmp/badgersett_p_state.json"
    if os.path.exists(state_mod.PATH):
        os.remove(state_mod.PATH)
    st = state_mod.State()
    for minute in range(0, 60, 5):            # an awake badge, every 5 min
        st.push_pressure(1012.0, minute)
    check("samples closer than 15 minutes are not stored",
          len(st.get("pressure")) == 4, st.get("pressure"))
    st2 = state_mod.State()
    st2.set("pressure", [])
    for minute in range(0, 240, 5):
        st2.push_pressure(1016.0 - minute / 60.0, minute)
    check("four hours at 5-minute loops still yields a 3-hour trend",
          st2.pressure_trend() is not None, st2.get("pressure"))
    os.remove(state_mod.PATH) if os.path.exists(state_mod.PATH) else None


def test_runlog():
    print("\nruntime log")
    from badgersett import power, runlog as runlog_mod
    WLAN = sys.modules["network"].WLAN
    path = "/tmp/badgersett_runtime.log"
    runlog_mod.PATH = path
    if os.path.exists(path):
        os.remove(path)

    WLAN.is_active = False
    check("VSYS reads when the radio is off", power.vsys() is not None, power.vsys())
    check("VSYS is converted to volts", 4.6 < power.vsys() < 5.0, power.vsys())
    WLAN.is_active = True
    check("VSYS refuses while the WiFi radio is on", power.vsys() is None)
    WLAN.is_active = False

    # Bluetooth shares the chip and the SPI bus, so it must block it too -
    # signalled by a flag, not by asking the bluetooth module, which would
    # mean poking the very chip we are trying not to disturb.
    power.RADIO_BUSY = True
    check("VSYS refuses while Bluetooth is scanning", power.vsys() is None)
    power.RADIO_BUSY = False
    check("...and reads again afterwards", power.vsys() is not None)

    # The cap has to hold without ever loading the file into RAM: on the
    # badge a readlines() of a 96KB log raises MemoryError, the trim never
    # happens again, and the log grows until the filesystem is full.
    line = "2026-09-26 12:00 beat  up=1m vsys=4.100 mode=awake\n"
    with open(path, "w") as handle:
        handle.write(line * ((runlog_mod.MAX_BYTES // len(line)) + 200))
    before = os.path.getsize(path)
    runlog_mod.RunLog(enabled=True)._trim()
    after = os.path.getsize(path)
    check("an oversized log is trimmed", after < before, (before, after))
    check("...to roughly half", before * 0.4 < after < before * 0.6, (before, after))
    with open(path) as handle:
        kept = handle.read()
    check("...on a line boundary, with no half-line left at the front",
          kept.startswith("2026-") and kept.endswith("\n"), kept[:30])
    check("...and no temporary file is left behind",
          not os.path.exists(path + ".tmp"))
    os.remove(path)

    from badgersett import scan as scan_probe
    seen = []
    real_vsys = power.vsys
    power.vsys = lambda *a, **k: seen.append(power.RADIO_BUSY)
    scan_probe.ble_scan(20)
    power.vsys = real_vsys
    check("the flag is cleared again after a scan", power.RADIO_BUSY is False)

    off = runlog_mod.RunLog(enabled=False)
    off.write("boot")
    check("a disabled log writes nothing", not os.path.exists(path))

    log = runlog_mod.RunLog(enabled=True, every_minutes=10, mode="awake")
    log.write("boot", usb=0)
    log.maybe_beat(gas="ok")
    log.maybe_beat(gas="ok")                   # too soon: suppressed
    lines = open(path).read().splitlines()
    check("boot and first beat were written", len(lines) == 2, lines)
    check("lines carry uptime, voltage and mode",
          all("up=" in l and "vsys=4." in l and "mode=awake" in l for l in lines),
          lines)
    check("extra fields are recorded", "gas=ok" in lines[1], lines[1])

    runlog_mod.MAX_BYTES = 400
    for i in range(40):
        log.write("beat", n=i)
    lines = open(path).read().splitlines()
    check("the log is trimmed rather than growing without bound",
          os.path.getsize(path) < 1200, os.path.getsize(path))
    check("trimming keeps the newest lines", "n=39" in lines[-1], lines[-1])
    runlog_mod.MAX_BYTES = 96000
    os.remove(path)


def test_photo_blit():
    print("\nphoto blit (no PNG decoder)")
    import types as _types
    fb_path = "/tmp/badgersett_photo.fb"
    stride = ui.HEIGHT // 8
    wanted = ui.PHOTO_W * stride
    # A recognisable pattern: alternate columns solid black / solid white.
    data = bytearray()
    for x in range(ui.PHOTO_W):
        data.extend(b"\x00" * stride if x % 2 == 0 else b"\xff" * stride)
    open(fb_path, "wb").write(bytes(data))

    cfg = _types.SimpleNamespace(NAME="Chris Wenham", TITLE="T", ORG="", PHOTO=fb_path)
    fb = bytearray(b"\xff" * (ui.WIDTH * stride))
    display = FakeDisplay()
    screen = ui.UI(display, cfg, fb)
    check("the blit reports success", screen._photo() is True)
    check("it wrote exactly the photo's bytes",
          bytes(fb[:wanted]) == bytes(data), (fb[:4], data[:4]))
    check("it left the rest of the panel alone",
          set(fb[wanted:]) == {0xFF}, sorted(set(fb[wanted:]))[:3])
    check("%d bytes, not 48KB" % wanted, wanted == 1536, wanted)

    # Anything wrong falls back to initials rather than failing.
    display = FakeDisplay()
    bad = _types.SimpleNamespace(NAME="Chris Wenham", TITLE="T", ORG="",
                                 PHOTO="/tmp/badgersett_missing.fb")
    screen = ui.UI(display, bad, bytearray(ui.WIDTH * stride))
    check("a missing file falls back to initials", screen._photo() is False)
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("...and the initials are drawn", "CW" in drawn, drawn)

    open(fb_path, "wb").write(b"\x00" * 100)      # wrong size
    display = FakeDisplay()
    screen = ui.UI(display, cfg, bytearray(ui.WIDTH * stride))
    check("a wrong-sized file falls back too", screen._photo() is False)

    display = FakeDisplay()
    screen = ui.UI(display, cfg, None)
    check("with no framebuffer it falls back", screen._photo() is False)
    os.remove(fb_path)


def test_scanner():
    print("\nradio scanner (the secret screen)")
    from badgersett import scan as scan_mod
    WLAN = sys.modules["network"].WLAN
    FakeBLE = sys.modules["bluetooth"].FakeBLE   # the class, not the BLE() factory
    scan_mod.PATH = "/tmp/badgersett_scan.json"
    if os.path.exists(scan_mod.PATH):
        os.remove(scan_mod.PATH)
    WLAN.is_active = False
    FakeBLE.fail = False

    check("advertising payload split into fields",
          scan_mod._adv_fields(b"\x05\x09abcd")[0x09] == b"abcd")
    check("a truncated payload does not raise",
          scan_mod._adv_fields(b"\x05") == {})
    check("public addresses are named as such",
          scan_mod._addr_kind(0, b"\x11" * 6) == "public")
    check("a phone's rotating address is identified",
          scan_mod._addr_kind(1, b"\x40\x00\x00\x00\x00\x00") == "rotating")
    check("a static random address is distinguished",
          scan_mod._addr_kind(1, b"\xc0\x00\x00\x00\x00\x00") == "static")

    wifi = scan_mod.wifi_scan()
    names = sorted(e["n"] for e in wifi)
    check("wifi scan finds the access points",
          names == ["(hidden)", "HomeNet", "Neighbour", "OfficeNet"], names)
    check("duplicate APs collapse to the strongest",
          [e["r"] for e in wifi if e["n"] == "HomeNet"] == [-66],
          [e for e in wifi if e["n"] == "HomeNet"])
    check("a hidden SSID is labelled, not dropped",
          not any(e["n"] == "" for e in wifi))
    check("wifi entries carry channel and security",
          all("ch" in e["x"] for e in wifi), wifi[:1])
    check("the radio is left off afterwards", WLAN.is_active is False)

    ble = scan_mod.ble_scan(50)
    check("bluetooth scan finds each distinct address", len(ble) == 5, ble)
    labels = [e["n"] for e in ble]
    check("a named device uses its name", "PowerStation-42" in labels, labels)
    check("an unnamed device falls back to its maker", "Apple" in labels, labels)
    check("otherwise it falls back to part of the address",
          any(":" in l for l in labels), labels)
    apple = [e for e in ble if e["n"] == "Apple"][0]
    check("repeat sightings keep the strongest signal", apple["r"] == -49, apple)
    check("a rotating address is flagged as such", "rotating" in apple["x"], apple)

    FakeBLE.fail = True
    check("a failing bluetooth radio yields nothing, and does not raise",
          scan_mod.ble_scan(50) == [])
    FakeBLE.fail = False

    entries = scan_mod.survey(50, True)
    check("survey merges both radios", len(entries) == 9, len(entries))
    check("sorted strongest first",
          entries == sorted(entries, key=lambda e: -e["r"]), [e["r"] for e in entries])
    check("both kinds present", {e["k"] for e in entries} == {"W", "B"})
    check("bluetooth can be skipped",
          all(e["k"] == "W" for e in scan_mod.survey(50, False)))

    scan_mod.save(entries, 1234)
    back, stamp = scan_mod.load()
    check("a scan round-trips through its own file",
          len(back) == len(entries) and stamp == 1234, (len(back), stamp))
    scan_mod.PATH = "/tmp/badgersett_missing.json"
    check("a missing cache is not an error", scan_mod.load() == ([], None))
    scan_mod.PATH = "/tmp/badgersett_scan.json"

    long_list = [{"k": "B", "n": "dev%d" % i, "r": -40 - i, "x": "static"}
                 for i in range(200)]
    scan_mod.save(long_list, 1)
    check("the cache is bounded", len(scan_mod.survey(50, False)) <= scan_mod.MAX_ENTRIES)
    os.remove(scan_mod.PATH) if os.path.exists(scan_mod.PATH) else None


def test_scanner_screens():
    print("\nscanner screens")
    from badgersett import scan as scan_mod
    status = {"updated": "Updated 17:15", "online": True, "muted": False,
              "sensor": True, "view": 3, "credit": "x"}
    entries = [{"k": "W", "n": "Acme-Guest", "r": -69, "x": "ch6 WPA2"},
               {"k": "B", "n": "PowerStation-42", "r": -43, "x": "public"},
               {"k": "B", "n": "Apple", "r": -53, "x": "rotating"}]
    entries += [{"k": "B", "n": "dev-%d" % i, "r": -60 - i, "x": "static"}
                for i in range(20)]
    entries.sort(key=lambda e: -e["r"])

    for mode in ("list", "radar"):
        display = FakeDisplay()
        ui.UI(display, CONFIG).render(3, None, None, [], None,
                                      dict(status), None, (entries, 0, mode, 24))
        drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
        check("%s renders" % mode, display.updates == 1 and len(display.calls) > 10)
        check("%s stays on the panel" % mode, not display.out_of_bounds,
              display.out_of_bounds[:2])
        check("%s shows the loudest device" % mode, "PowerStation-42" in drawn, drawn[:70])
        check("%s names its controls" % mode, "A exit" in drawn, drawn[-60:])

    display = FakeDisplay()
    ui.UI(display, CONFIG).scan_list(entries, 1, dict(status), 24)
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("page 2 shows different devices", "PowerStation-42" not in drawn, drawn[:70])
    check("page 2 says which page it is", "2/3" in drawn, drawn[:40])

    display = FakeDisplay()
    page = ui.UI(display, CONFIG).scan_list(entries, 99, dict(status), 24)
    check("a page past the end is clamped", page == 2, page)

    display = FakeDisplay()
    ui.UI(display, CONFIG).scan_list([], 0, dict(status), None)
    drawn = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("an empty scan explains itself", "scan again" in drawn, drawn)
    check("an empty scan stays on the panel", not display.out_of_bounds)

    display = FakeDisplay()
    ui.UI(display, CONFIG).scan_radar([], 0, dict(status), None)
    check("an empty radar renders", display.calls and not display.out_of_bounds)

    # The radar's side list must page too: it used to ignore the page
    # entirely, so DOWN redrew an identical screen.
    check("the two modes have their own page sizes",
          ui.rows_for("radar") == ui.RADAR_ROWS and ui.rows_for("list") == ui.SCAN_ROWS)

    def radar_text(page):
        d = FakeDisplay()
        ui.UI(d, CONFIG).scan_radar(entries, page, dict(status), 5)
        return " ".join(str(c[1][0]) for c in d.calls if c[0] == "text"), d

    first, d0 = radar_text(0)
    second, d1 = radar_text(1)
    check("radar page 1 lists the strongest", "PowerStation-42" in first, first[:60])
    check("radar page 2 lists different devices",
          "PowerStation-42" not in second and second != first, second[:60])
    check("radar shows which page it is on", "1/" in first and "2/" in second,
          (first[:40], second[:40]))
    check("radar page 2 labels the rank range", "#7-12" in second, second[:50])
    check("both radar pages stay on the panel",
          not d0.out_of_bounds and not d1.out_of_bounds,
          (d0.out_of_bounds[:1], d1.out_of_bounds[:1]))
    check("the listed devices are ringed on the plot",
          len([c for c in d0.calls if c[0] == "rectangle"]) >
          len([c for c in FakeDisplay().calls if c[0] == "rectangle"]))

    last, _ = radar_text(99)
    check("a radar page past the end is clamped", "PowerStation-42" not in last)

    # The radar must sit centred in its box, left of the divider.
    x0, y0, x1, y1 = ui.RADAR_BOX
    left_gap = ui.RADAR_CX - ui.RADAR_R - x0
    right_gap = x1 - (ui.RADAR_CX + ui.RADAR_R)
    check("the radar is horizontally centred in its box",
          abs(left_gap - right_gap) <= 1, (left_gap, right_gap))
    check("it fits inside its box vertically",
          ui.RADAR_CY - ui.RADAR_R >= y0 and ui.RADAR_CY + ui.RADAR_R <= y1,
          (ui.RADAR_CY, ui.RADAR_R))
    check("it does not reach across the divider",
          ui.RADAR_CX + ui.RADAR_R <= ui.SCAN_PANEL_X - 6,
          (ui.RADAR_CX + ui.RADAR_R, ui.SCAN_PANEL_X))


def test_clock():
    print("\nclock and refresh scheduling")
    from badgersett import clock
    rtc = sys.modules["machine"].RTC()
    ntp = sys.modules["ntptime"]

    rtc.datetime((2026, 9, 20, 0, 17, 15, 0, 0))
    check("a 2026 clock counts as set", clock.is_set())
    check("hhmm reads the RTC", clock.hhmm() == "17:15", clock.hhmm())
    check("hour reads the RTC", clock.hour() == 17, clock.hour())

    rtc.datetime((2000, 1, 1, 0, 11, 15, 0, 0))
    check("an unset 2000 clock is not 'set'", not clock.is_set())
    check("minutes() is None with no clock", clock.minutes() is None)
    check("hour() is None with no clock", clock.hour() is None)

    # The bug that froze the badge: unknown state must mean "refresh now".
    check("no clock means a refresh is due", clock.is_stale(1000, 30))
    check("no record of a refresh means one is due", clock.is_stale(None, 30))

    rtc.datetime((2026, 9, 20, 0, 17, 15, 0, 0))
    now = clock.minutes()
    check("minutes() works once set", now is not None)
    check("just refreshed is not stale", not clock.is_stale(now, 30))
    check("29 minutes ago is not stale", not clock.is_stale(now - 29, 30))
    check("30 minutes ago is stale", clock.is_stale(now - 30, 30))
    check("a backwards clock jump forces a refresh",
          clock.is_stale(now + 5000, 30))

    ntp.calls[:] = []
    ntp.fail = False
    check("NTP sync succeeds and sets the clock", clock.sync_ntp())
    check("NTP was asked of a real host",
          ntp.calls and "ntp" in ntp.calls[0], ntp.calls)
    check("the RTC now holds the NTP (UTC) time",
          clock.hhmm() == "16:15", clock.hhmm())

    check("UTC+1 shifts the clock to local", clock.apply_utc_offset(3600))
    check("local time is an hour ahead of UTC", clock.hhmm() == "17:15",
          clock.hhmm())
    check("a zero offset is a no-op", not clock.apply_utc_offset(0))

    ntp.fail = True
    ntp.calls[:] = []
    check("every NTP host is tried before giving up",
          clock.sync_ntp() is False and len(ntp.calls) == len(clock.NTP_HOSTS),
          ntp.calls)
    check("a failed sync leaves the clock alone, not wrong",
          clock.hhmm() == "17:15", clock.hhmm())
    ntp.fail = False
    rtc.datetime((2026, 9, 20, 0, 17, 15, 0, 0))


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
    check("chord constant is A, C and UP",
          sorted(app.SECRET_CHORD) == ["A", "C", "UP"], app.SECRET_CHORD)
    check("views are badge/detail/news/secret",
          (ui.VIEW_BADGE, ui.VIEW_DETAIL, ui.VIEW_NEWS, ui.VIEW_SECRET) == (0, 1, 2, 3))


def test_secret_screen_flow():
    print("\nsecret screen: chord, paging and exit")
    from badgersett import app, scan as scan_mod
    scan_mod.PATH = "/tmp/badgersett_scan_flow.json"
    state_mod.PATH = "/tmp/badgersett_secret.json"
    for path in (scan_mod.PATH, state_mod.PATH):
        if os.path.exists(path):
            os.remove(path)
    CONFIG.SCAN_BLE, CONFIG.SCAN_BLE_MS = True, 50
    sys.modules["network"].WLAN.is_active = False

    badger = sys.modules["badger2040"]
    original_display, original_buttons = badger.Badger2040, app._wake_buttons
    displays = []

    def make_display():
        d = FakeDisplay()
        displays.append(d)
        return d

    badger.Badger2040 = make_display

    class Stop(BaseException):
        pass

    badger.sleep_for = lambda m: (_ for _ in ()).throw(Stop())

    def press(buttons):
        """One wake with these buttons held; returns what was drawn."""
        app._wake_buttons = lambda display: list(buttons)
        del displays[:]
        try:
            app.run()
        except Stop:
            pass
        return " ".join(str(c[1][0]) for c in displays[0].calls if c[0] == "text")

    try:
        drawn = press(["A", "C", "UP"])
        saved = state_mod.State()
        check("the chord opens the scanner", saved.get("view") == ui.VIEW_SECRET,
              saved.get("view"))
        check("it scans on the way in", os.path.exists(scan_mod.PATH))
        check("and draws the scanner, not the badge",
              "SCAN" in drawn and CONFIG.NAME not in drawn, drawn[:60])
        check("the chord does not also fire UP's network refresh or A's badge view",
              saved.get("view") == ui.VIEW_SECRET)

        entries, _ = scan_mod.load()
        pages = max(1, (len(entries) + ui.SCAN_ROWS - 1) // ui.SCAN_ROWS)
        check("enough devices for more than one page", pages > 1, pages)

        press(["DOWN"])
        check("DOWN pages forward instead of muting",
              state_mod.State().get("scan_page") == 1 and not state_mod.State().get("muted"),
              (state_mod.State().get("scan_page"), state_mod.State().get("muted")))
        press(["UP"])
        check("UP pages back instead of forcing a refresh",
              state_mod.State().get("scan_page") == 0)
        press(["UP"])
        check("paging back past the first page wraps to the last",
              state_mod.State().get("scan_page") == pages - 1,
              state_mod.State().get("scan_page"))

        drawn = press(["B"])
        check("B switches to the radar",
              state_mod.State().get("scan_mode") == "radar" and "RADAR" in drawn,
              (state_mod.State().get("scan_mode"), drawn[:40]))
        # The reported bug: on the radar, DOWN redrew an identical screen.
        # Set the mode outright rather than toggling, so this does not
        # depend on which mode the presses above happened to leave.
        setup = state_mod.State()
        setup.set("scan_mode", "radar")
        setup.set("scan_page", 0)
        setup.save()
        radar_first = press([])                     # draw radar page 1
        radar_second = press(["DOWN"])              # page 2
        check("DOWN on the radar changes what is drawn",
              radar_second != radar_first, (radar_first[:50], radar_second[:50]))
        check("...and moves to the next page of devices",
              state_mod.State().get("scan_page") == 1,
              state_mod.State().get("scan_page"))
        check("the radar still draws the radar", "RADAR" in radar_second,
              radar_second[:40])

        drawn = press(["B"])
        check("B switches back to the list",
              state_mod.State().get("scan_mode") == "list" and "SCAN" in drawn)

        os.remove(scan_mod.PATH)
        press(["C"])
        check("C scans again", os.path.exists(scan_mod.PATH))

        drawn = press(["A"])
        check("A leaves the scanner for the badge",
              state_mod.State().get("view") == ui.VIEW_BADGE, drawn[:40])
        check("...and the badge is what gets drawn", CONFIG.NAME in drawn, drawn[:60])

        drawn = press(["B"])
        check("off the scanner, B is the detail view again",
              state_mod.State().get("view") == ui.VIEW_DETAIL)
    finally:
        badger.Badger2040 = original_display
        app._wake_buttons = original_buttons
        # The retry backoff is module state: leave it clean for the next test.
        app._last_attempt = None
        for path in (scan_mod.PATH, state_mod.PATH):
            if os.path.exists(path):
                os.remove(path)


def test_without_breakouts():
    print("\nrunning without the optional boards")
    from badgersett import app
    machine_mod = sys.modules["machine"]
    badger = sys.modules["badger2040"]
    real_i2c, real_display = machine_mod.I2C, badger.Badger2040
    app._last_attempt = None

    class Stop(BaseException):
        pass

    def run_with(devices, want_sensor, want_haptic):
        """One cycle with a given bus population and config."""
        machine_mod.I2C = lambda *a, **kw: FakeI2C(devices=devices)
        CONFIG.BME688_ENABLED, CONFIG.HAPTIC_ENABLED = want_sensor, want_haptic
        CONFIG.POWER_MODE = "sleep"
        state_mod.PATH = "/tmp/badgersett_nohw.json"
        if os.path.exists(state_mod.PATH):
            os.remove(state_mod.PATH)
        seen = []
        badger.Badger2040 = lambda: seen.append(FakeDisplay()) or seen[-1]
        badger.sleep_for = lambda m: (_ for _ in ()).throw(Stop())
        crashed = None
        try:
            app.run()
        except Stop:
            pass
        except Exception as exc:
            crashed = exc
        text = " ".join(str(c[1][0]) for c in seen[0].calls if c[0] == "text") if seen else ""
        return crashed, text, (seen[0] if seen else None)

    try:
        crashed, text, disp = run_with([], False, False)
        check("neither board: completes a cycle", crashed is None, crashed)
        check("neither board: still draws a screen", disp and disp.updates == 1)
        check("neither board: no 'NO BME' warning", "NO BME" not in text, text[-60:])
        check("neither board: does not claim the sensor is offline",
              "Sensor offline" not in text, text[-60:])
        check("neither board: nothing drawn off-panel", not disp.out_of_bounds,
              disp.out_of_bounds[:2])

        crashed, text, disp = run_with([0x5A], False, True)
        check("haptics only: completes a cycle", crashed is None, crashed)
        check("haptics only: no sensor complaint", "NO BME" not in text)

        crashed, text, disp = run_with([0x77], True, False)
        check("sensor only: completes a cycle", crashed is None, crashed)
        check("sensor only: no haptic complaint", crashed is None)

        # Fitted but silent IS a fault, and should say so.
        crashed, text, disp = run_with([], True, True)
        check("sensor expected but absent: completes anyway", crashed is None, crashed)
        check("sensor expected but absent: warns", "NO BME" in text, text[-70:])

        check("auto mode does not stay awake with no sensor to warm",
              app._stay_awake("auto", have_sensor=False) is False)
        check("...but an explicit awake is still honoured",
              app._stay_awake("awake", have_sensor=False) is True)
    finally:
        machine_mod.I2C, badger.Badger2040 = real_i2c, real_display
        CONFIG.BME688_ENABLED = CONFIG.HAPTIC_ENABLED = True
        CONFIG.POWER_MODE = "sleep"
        app._last_attempt = None
        if os.path.exists(state_mod.PATH):
            os.remove(state_mod.PATH)


def test_screens_without_sensor():
    print("\nscreens without a sensor fitted")
    status = {"updated": "Updated 17:15", "online": True, "muted": False,
              "sensor": None, "view": 1, "credit": "Open-Meteo", "location": "Home"}
    st = state_mod.State()

    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, None, st, [], status)
    text = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("no INSIDE heading", "INSIDE" not in text, text[:60])
    check("no 'Sensor not responding'", "responding" not in text, text[:80])
    check("the forecast fills the width instead",
          "Humidity" in text and "Tomorrow" in text, text[:110])
    check("nothing off-panel", not display.out_of_bounds, display.out_of_bounds[:2])
    xs = [c[1][1] for c in display.calls if c[0] == "text"]
    check("it uses the right-hand half", max(xs) > ui.DIVIDER_X, max(xs))

    # Fitted but silent still reports the fault.
    display = FakeDisplay()
    ui.UI(display, CONFIG).detail_view(SAMPLE_WEATHER, None, st,
                                       [], dict(status, sensor=False))
    text = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("a fitted-but-silent sensor still says so", "responding" in text, text[:80])

    display = FakeDisplay()
    ui.UI(display, CONFIG).badge(SAMPLE_WEATHER, None, [], dict(status, view=0))
    text = " ".join(str(c[1][0]) for c in display.calls if c[0] == "text")
    check("badge view omits the indoor line when nothing is fitted",
          "Sensor offline" not in text, text[-60:])


def test_app_cycle():
    print("\nfull wake cycle (app.py)")
    from badgersett import app, nws as nws_mod, weather as weather_mod
    app._last_attempt = None          # no backoff carried in from another test

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

    reserve_during_fetch = []

    def fake_get(url, headers=None):
        calls.append(url)
        # The TLS arena must be released while a fetch is in flight, or
        # HTTPS has nowhere contiguous to work on the real board.
        reserve_during_fetch.append(app._reserve)
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

    handover = [bytearray(app.TLS_RESERVE)]
    app._reserve = None
    try:
        app.run(handover)
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
    # main.py must hand the arena over rather than keep a reference of its
    # own: while anything else still points at it, freeing app's copy frees
    # nothing and every HTTPS fetch fails with ENOMEM.
    check("the TLS arena was taken out of the handover list", handover == [], handover)
    check("the arena was released for every fetch",
          reserve_during_fetch and all(r is None for r in reserve_during_fetch),
          reserve_during_fetch)
    check("the arena was re-claimed once fetching finished",
          app._reserve is not None and len(app._reserve) == app.TLS_RESERVE,
          app._reserve if app._reserve is None else len(app._reserve))
    check("the screen was drawn exactly once", displays and displays[0].updates == 1,
          displays[0].updates if displays else "no display")
    check("it slept for the configured interval", slept == [CONFIG.REFRESH_MINUTES], slept)
    check("the forecast was fetched", any("open-meteo" in u for u in calls), calls)
    check("Met Office warnings were fetched for the network's region",
          any("metoffice.gov.uk" in u and u.endswith("/se") for u in calls), calls)
    check("the forecast used the joined network's coordinates",
          any("latitude=50.8279" in u and "longitude=-0.1687" in u for u in calls),
          [u for u in calls if "open-meteo" in u][:1])
    check("the US NWS was not contacted",
          not any("weather.gov" in u for u in calls), calls)

    saved = state_mod.State()
    check("forecast cached for the next button wake",
          (saved.get("weather") or {}).get("temp") == 19.7, saved.get("weather"))
    check("indoor reading cached",
          (saved.get("indoor") or {}).get("humidity") == 47.0, saved.get("indoor"))
    check("update timestamp is the real local clock, not the forecast's",
          saved.get("updated") == "17:15", saved.get("updated"))
    check("refresh time recorded for staleness checks",
          saved.get("last_refresh") is not None, saved.get("last_refresh"))
    latched = sorted((saved.get("alerts") or {}).keys())
    check("forecast rules and Met Office warnings latch together",
          latched == ["met:rain", "met:wind", "wx:code:96", "wx:gust"], latched)
    # A cold cycle must NOT seed the baseline: the gas heater has only been
    # running for milliseconds, so the reading is mid burn-in and worthless.
    check("a cold cycle does not seed the gas baseline",
          saved.get("gas_baseline") is None, saved.get("gas_baseline"))
    loc = saved.get("location") or {}
    check("the joined network's location was remembered",
          loc.get("label") == "Home" and loc.get("met_region") == "se", loc)
    check("its altitude was remembered for pressure correction",
          loc.get("altitude") == 10, loc)
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
    test_power_detection()
    test_wifi_selection()
    test_photo_blit()
    test_scanner()
    test_scanner_screens()
    test_power_modes()
    test_redraw_flipflop()
    test_battery_cutoff()
    test_gas_gap_reset()
    test_pressure_spacing()
    test_runlog()
    test_clock()
    test_buttons()
    test_layouts()
    test_secret_screen_flow()
    test_without_breakouts()
    test_screens_without_sensor()
    test_app_cycle()
    print("\n%s" % ("-" * 46))
    if FAILURES:
        print("%d check(s) FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
