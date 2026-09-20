"""Screen layouts for the 296x128 Badger 2040 W panel.

Three views, cycled with the A / B / C buttons:
  0  badge    — photo, name, title, weather at a glance, indoor summary
  1  weather  — the full forecast
  2  indoor   — everything the BME688 has to say

Bitmap fonts throughout: their y coordinate is the top-left corner, which
makes the vertical budget on a 128-pixel-tall screen predictable. Swap in
the Hershey "sans" font for the name if you prefer it — just remember its
y is a baseline, not a top edge.
"""

import badger2040

from . import icons, util, weather as weather_api

WIDTH = badger2040.WIDTH        # 296
HEIGHT = badger2040.HEIGHT      # 128

BLACK = 0
WHITE = 15

PHOTO_W = 96
PANE_X = 104                    # left edge of the text pane on the badge view
PANE_W = WIDTH - PANE_X

VIEW_BADGE, VIEW_WEATHER, VIEW_INDOOR = 0, 1, 2
VIEW_NAMES = ("BADGE", "WEATHER", "INDOOR")


def _degree(d, x, y, scale=1):
    """Bitmap fonts stop at ASCII, so draw the degree ring by hand."""
    r = 2 * scale
    d.circle(x + r, y + r, r)
    d.set_pen(WHITE)
    d.circle(x + r, y + r, max(1, r - scale))
    d.set_pen(BLACK)


def _temp(d, value, x, y, scale=2, unit=True):
    """Draw a temperature with its degree ring; returns the width used."""
    if value is None:
        d.set_font("bitmap8")
        d.text("--", x, y, scale=scale)
        return d.measure_text("--", scale)
    d.set_font("bitmap8")
    text = "%d" % round(value)
    d.text(text, x, y, scale=scale)
    used = d.measure_text(text, scale)
    _degree(d, x + used + scale, y + scale, scale)
    used += 6 * scale
    if unit:
        d.text(util.temp_unit(), x + used, y, scale=scale)
        used += d.measure_text(util.temp_unit(), scale)
    return used


def _hline(d, x, y, w):
    d.rectangle(x, y, w, 1)


def _banner(d, x, y, w, h, text, scale=1):
    """Inverted bar with a warning triangle — the loudest thing a 1-bit
    screen can say. The glyph is knocked out of the black bar in white."""
    d.set_pen(BLACK)
    d.rectangle(x, y, w, h)

    glyph = h - 4
    gx, gy = x + 3, y + 2
    d.set_pen(WHITE)
    d.triangle(gx + glyph // 2, gy, gx, gy + glyph, gx + glyph, gy + glyph)
    d.set_pen(BLACK)                       # the exclamation mark, cut back out
    d.rectangle(gx + glyph // 2, gy + glyph // 3, 2, glyph // 3 - 1)
    d.pixel(gx + glyph // 2, gy + glyph - 3)

    d.set_pen(WHITE)
    d.set_font("bitmap6")
    d.text(util.truncate(d, text, w - glyph - 10, scale),
           gx + glyph + 5, y + (h - 6 * scale) // 2, scale=scale)
    d.set_pen(BLACK)


class UI:
    def __init__(self, display, config):
        self.d = display
        self.config = config
        self._png = None
        try:
            import pngdec
            self._png = pngdec.PNG(display.display)
        except Exception as exc:
            util.log("pngdec unavailable:", exc)

    # -- shared pieces -----------------------------------------------------
    def _clear(self):
        self.d.set_pen(WHITE)
        self.d.clear()
        self.d.set_pen(BLACK)

    def _photo(self, x=0, y=0, w=PHOTO_W, h=HEIGHT):
        path = getattr(self.config, "PHOTO", None)
        if path and self._png:
            try:
                self._png.open_file(path)
                self._png.decode(x, y)
                return True
            except Exception as exc:
                util.log("photo failed:", exc)
        self._initials(x, y, w, h)
        return False

    def _initials(self, x, y, w, h):
        """Fallback portrait: initials reversed out of a black block."""
        d = self.d
        d.set_pen(BLACK)
        d.rectangle(x, y, w, h)
        parts = [p for p in getattr(self.config, "NAME", "").split() if p]
        text = "".join(p[0].upper() for p in parts[:2]) or "?"
        d.set_pen(WHITE)
        d.set_font("bitmap8")
        scale = 5
        d.text(text, x + (w - d.measure_text(text, scale)) // 2,
               y + (h - 8 * scale) // 2, scale=scale)
        d.set_pen(BLACK)

    def _status(self, x, y, w, status):
        """One 6px line: last update, link state, mute, current view."""
        d = self.d
        d.set_pen(BLACK)
        d.set_font("bitmap6")
        left = status.get("updated") or "no data"
        d.text(util.truncate(d, left, w - 78), x, y, scale=1)
        flags = []
        if status.get("online") is False:
            flags.append("OFFLINE")
        if status.get("muted"):
            flags.append("MUTE")
        if status.get("sensor") is False:
            flags.append("NO BME")
        flags.append(VIEW_NAMES[status.get("view", 0)])
        text = " ".join(flags)
        d.text(text, x + w - d.measure_text(text, 1), y, scale=1)

    def _header(self, title, right=""):
        d = self.d
        d.set_pen(BLACK)
        d.rectangle(0, 0, WIDTH, 16)
        d.set_pen(WHITE)
        d.set_font("bitmap8")
        d.text(title, 4, 4, scale=1)
        if right:
            d.set_font("bitmap6")
            d.text(right, WIDTH - d.measure_text(right, 1) - 4, 5, scale=1)
        d.set_pen(BLACK)

    def _row(self, y, label, value, x=6, w=WIDTH - 12):
        """Label on the left, value right-aligned, with a dotted leader."""
        d = self.d
        d.set_font("bitmap8")
        d.text(label, x, y, scale=1)
        value = str(value)
        value_w = d.measure_text(value, 1)
        d.text(value, x + w - value_w, y, scale=1)
        leader_start = x + d.measure_text(label, 1) + 4
        leader_end = x + w - value_w - 4
        for px in range(leader_start, leader_end, 3):
            d.pixel(px, y + 6)

    # -- view 0: the badge -------------------------------------------------
    def badge(self, weather, indoor, alerts, status):
        d = self.d
        self._clear()
        self._photo()

        d.set_pen(BLACK)
        name = getattr(self.config, "NAME", "")
        d.set_font("bitmap8")
        scale = 2 if d.measure_text(name, 2) <= PANE_W - 6 else 1
        d.text(util.truncate(d, name, PANE_W - 6, scale), PANE_X, 2, scale=scale)

        y = 2 + (8 * scale) + 4
        d.text(util.truncate(d, getattr(self.config, "TITLE", ""), PANE_W - 6), PANE_X, y, scale=1)
        y += 10
        org = getattr(self.config, "ORG", "")
        if org:
            d.set_font("bitmap6")
            d.text(util.truncate(d, org, PANE_W - 6), PANE_X, y, scale=1)
        _hline(d, PANE_X, 42, PANE_W - 8)

        # Weather at a glance
        if weather:
            label, icon_key = weather_api.describe(weather.get("code"))
            if icon_key == "sun" and not weather.get("is_day", True):
                icon_key = "moon"
            icons.draw(d, icon_key, PANE_X - 2, 46, 30)
            _temp(d, weather.get("temp"), PANE_X + 32, 48, scale=2)
            d.set_font("bitmap6")
            d.text(util.truncate(d, label, 86), PANE_X + 32, 70, scale=1)

            right_x = PANE_X + 124
            d.set_font("bitmap6")
            hi, lo = weather.get("hi"), weather.get("lo")
            if hi is not None:
                d.text("H %d  L %d" % (round(hi), round(lo)), right_x, 48, scale=1)
            gust = weather.get("gust_max")
            if gust is not None:
                d.text("Gust %d" % round(gust), right_x, 58, scale=1)
            pop = weather.get("pop")
            if pop is not None:
                d.text("Rain %d%%" % pop, right_x, 68, scale=1)
        else:
            d.set_font("bitmap6")
            d.text("No forecast yet", PANE_X, 56, scale=1)

        _hline(d, PANE_X, 80, PANE_W - 8)

        # Indoor one-liner
        d.set_font("bitmap6")
        if indoor:
            parts = ["IN %.1f%s" % (indoor["temp"], util.temp_unit()),
                     "%d%%" % round(indoor["humidity"])]
            if indoor.get("pressure"):
                parts.append("%d hPa" % round(indoor["pressure"]))
            d.text(util.truncate(d, "  ".join(parts), PANE_W - 8), PANE_X, 85, scale=1)
        else:
            d.text("Sensor offline", PANE_X, 85, scale=1)

        # Alert banner, or a quiet spacer
        if alerts:
            top = alerts[0]
            _banner(d, PANE_X - 2, 96, PANE_W - 4, 16, top["text"])
            if len(alerts) > 1:
                d.set_font("bitmap6")
                d.set_pen(BLACK)
                d.text("+%d more" % (len(alerts) - 1), PANE_X, 114, scale=1)
                tight = dict(status)
                clock = (status.get("updated") or "").split()[-1]
                tight["updated"] = clock if ":" in clock else ""
                self._status(PANE_X + 52, 114, PANE_W - 60, tight)
                return
        self._status(PANE_X, 116, PANE_W - 8, status)

    # -- view 1: full forecast --------------------------------------------
    def weather_view(self, weather, alerts, status):
        d = self.d
        self._clear()
        if not weather:
            self._header("WEATHER")
            d.set_font("bitmap8")
            d.text("No forecast available.", 8, 40, scale=1)
            d.set_font("bitmap6")
            d.text("Press UP to retry the network.", 8, 58, scale=1)
            self._status(6, 118, WIDTH - 12, status)
            return

        clock = (weather.get("time") or "")[-5:]
        self._header("WEATHER", clock)

        label, icon_key = weather_api.describe(weather.get("code"))
        if icon_key == "sun" and not weather.get("is_day", True):
            icon_key = "moon"
        icons.draw(d, icon_key, 4, 20, 40)
        _temp(d, weather.get("temp"), 50, 24, scale=3)
        d.set_font("bitmap6")
        d.text(util.truncate(d, label, 120), 50, 52, scale=1)

        feels = weather.get("feels")
        if feels is not None:
            d.text("feels %d" % round(feels), 50, 62, scale=1)

        right = 150
        d.set_font("bitmap6")
        rows = []
        if weather.get("hi") is not None:
            rows.append("Today   H %d / L %d" % (round(weather["hi"]), round(weather["lo"])))
        if weather.get("hi2") is not None:
            rows.append("Tomorrow H %d / L %d" % (round(weather["hi2"]), round(weather["lo2"])))
        if weather.get("wind") is not None:
            rows.append("Wind    %d %s %s" % (round(weather["wind"]), util.speed_unit(),
                                              util.bearing(weather.get("dir") or 0)))
        if weather.get("gust_max") is not None:
            rows.append("Gusts   %d %s" % (round(weather["gust_max"]), util.speed_unit()))
        if weather.get("pop") is not None:
            rows.append("Rain    %d%%" % weather["pop"])
        if weather.get("humidity") is not None:
            rows.append("Humidity %d%%" % round(weather["humidity"]))
        for index, text in enumerate(rows[:6]):
            d.text(util.truncate(d, text, WIDTH - right - 4), right, 22 + index * 10, scale=1)

        # Data credit. The Met Office asks to be attributed wherever their
        # warnings feed is used, so this line is not merely decorative.
        credit = status.get("credit")
        if credit:
            d.set_font("bitmap6")
            d.text(util.truncate(d, credit, WIDTH - 12), 6, 82, scale=1)

        if alerts:
            _banner(d, 0, 92, WIDTH, 16, alerts[0]["text"])
            if len(alerts) > 1:
                d.set_font("bitmap6")
                d.text(util.truncate(d, alerts[1]["text"], WIDTH - 12), 6, 110, scale=1)
                self._status(6, 120, WIDTH - 12, status)
                return
        self._status(6, 118, WIDTH - 12, status)

    # -- view 2: indoor sensor --------------------------------------------
    def indoor_view(self, indoor, state, alerts, status):
        d = self.d
        self._clear()
        self._header("INDOOR", "BME688")

        if not indoor:
            d.set_font("bitmap8")
            d.text("Sensor not responding.", 8, 40, scale=1)
            d.set_font("bitmap6")
            d.text("Check the Qw/ST cable and I2C address.", 8, 58, scale=1)
            self._status(6, 118, WIDTH - 12, status)
            return

        _temp(d, indoor["temp"], 6, 22, scale=3)
        d.set_font("bitmap6")
        dew = indoor.get("dew")
        if dew is not None:
            d.text("dew point %.1f%s" % (dew, util.temp_unit()), 6, 52, scale=1)

        y = 22
        self._row(y, "Humidity", "%.0f %%RH" % indoor["humidity"], x=120, w=WIDTH - 126)
        y += 12
        if indoor.get("pressure"):
            trend = state.pressure_trend()
            arrow = ""
            if trend is not None:
                arrow = " %s%.1f" % ("+" if trend >= 0 else "", trend)
            self._row(y, "Pressure", "%.0f hPa%s" % (indoor["pressure"], arrow),
                      x=120, w=WIDTH - 126)
            y += 12

        gas = indoor.get("gas_raw")
        baseline = state.get("gas_baseline")
        if gas:
            self._row(y, "Gas", "%.1f k" % (gas / 1000.0), x=120, w=WIDTH - 126)
            y += 12
            if baseline:
                ratio = gas / baseline
                verdict = "clean" if ratio >= 0.85 else ("elevated" if ratio >= 0.6 else "POOR")
                if not indoor.get("stable"):
                    verdict = "warming up"
                self._row(y, "Air", "%d%% base  %s" % (round(ratio * 100), verdict),
                          x=120, w=WIDTH - 126)

        # Gas bar chart against baseline
        if gas and baseline:
            bar_w = 100
            fill = int(util.clamp(gas / baseline, 0.0, 1.2) / 1.2 * bar_w)
            d.rectangle(6, 74, bar_w, 10)
            d.set_pen(WHITE)
            d.rectangle(7, 75, bar_w - 2, 8)
            d.set_pen(BLACK)
            d.rectangle(7, 75, max(fill - 1, 0), 8)
            d.set_font("bitmap6")
            d.text("vs baseline", 6, 88, scale=1)

        if alerts:
            _banner(d, 0, 98, WIDTH, 16, alerts[0]["text"])
        self._status(6, 118, WIDTH - 12, status)

    # -- dispatch ----------------------------------------------------------
    def render(self, view, weather, indoor, alerts, state, status):
        if view == VIEW_WEATHER:
            self.weather_view(weather, alerts, status)
        elif view == VIEW_INDOOR:
            self.indoor_view(indoor, state, alerts, status)
        else:
            self.badge(weather, indoor, alerts, status)
        self.d.update()

    def message(self, title, detail=""):
        """Full-screen notice, used for setup errors and first boot."""
        self._clear()
        self._header(title)
        self.d.set_font("bitmap8")
        self.d.text(detail, 8, 40, WIDTH - 16, 1)
        self.d.update()
