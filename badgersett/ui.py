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

import gc

import badger2040

from . import icons, util, weather as weather_api

WIDTH = badger2040.WIDTH        # 296
HEIGHT = badger2040.HEIGHT      # 128

BLACK = 0
WHITE = 15

PHOTO_W = 96
PANE_X = 104                    # left edge of the text pane on the badge view
PANE_W = WIDTH - PANE_X

COL2_X = 152                    # left edge of the "inside" column on the detail view
DIVIDER_X = 145

VIEW_BADGE, VIEW_DETAIL, VIEW_NEWS, VIEW_SECRET = 0, 1, 2, 3
VIEW_NAMES = ("BADGE", "DETAIL", "NEWS", "SECRET")


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

    # -- shared pieces -----------------------------------------------------
    def _clear(self):
        self.d.set_pen(WHITE)
        self.d.clear()
        self.d.set_pen(BLACK)

    def _photo(self, x=0, y=0, w=PHOTO_W, h=HEIGHT):
        """Draw the photo, holding the PNG decoder for as short a time as
        possible.

        pngdec.PNG() allocates roughly 48KB up front. Held for the life of
        the UI object that is most of the badge's usable heap, and it
        leaves too little contiguous memory for a TLS handshake later in
        the cycle - which shows up as ENOMEM on the HTTPS feeds rather
        than as anything to do with the photo. So it is built on demand
        and released immediately.
        """
        path = getattr(self.config, "PHOTO", None)
        if path:
            png = None
            try:
                import pngdec
                png = pngdec.PNG(self.d.display)
                png.open_file(path)
                png.decode(x, y)
                return True
            except Exception as exc:
                util.log("photo failed:", exc)
            finally:
                png = None
                gc.collect()
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

    def _status(self, x, y, w, status, left=None):
        """One 6px line: last update (or `left`), link state, mute, view."""
        d = self.d
        d.set_pen(BLACK)
        d.set_font("bitmap6")
        left = left if left is not None else (status.get("updated") or "no data")
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

    # -- view 1 (button B): forecast and sensors, side by side -----------
    def detail_view(self, weather, indoor, state, alerts, status):
        """Everything measured and forecast, in two columns.

        Outside on the left, inside on the right. One screen rather than
        two so a single press of B answers both questions.
        """
        d = self.d
        self._clear()

        clock = (status.get("updated") or "").split()[-1]
        if ":" not in clock:
            clock = ""

        # The header bar doubles as the column captions.
        d.set_pen(BLACK)
        d.rectangle(0, 0, WIDTH, 15)
        d.set_pen(WHITE)
        d.set_font("bitmap8")
        d.text("OUTSIDE", 4, 4, scale=1)
        d.text("INSIDE", COL2_X, 4, scale=1)
        if clock:
            d.set_font("bitmap6")
            d.text(clock, WIDTH - d.measure_text(clock, 1) - 4, 5, scale=1)
        d.set_pen(BLACK)
        d.rectangle(DIVIDER_X, 17, 1, 92)

        # --- outside -----------------------------------------------------
        left_w = DIVIDER_X - 10
        if weather:
            label, icon_key = weather_api.describe(weather.get("code"))
            if icon_key == "sun" and not weather.get("is_day", True):
                icon_key = "moon"
            icons.draw(d, icon_key, 2, 18, 26)
            _temp(d, weather.get("temp"), 32, 20, scale=2)

            rows = [label]
            if weather.get("feels") is not None:
                rows.append("Feels %d" % round(weather["feels"]))
            if weather.get("hi") is not None:
                rows.append("H %d   L %d" % (round(weather["hi"]), round(weather["lo"])))
            if weather.get("wind") is not None:
                rows.append("Wind %d %s %s" % (round(weather["wind"]), util.speed_unit(),
                                               util.bearing(weather.get("dir") or 0)))
            if weather.get("gust_max") is not None:
                rows.append("Gusts %d %s" % (round(weather["gust_max"]), util.speed_unit()))
            if weather.get("pop") is not None:
                rows.append("Rain %d%%" % weather["pop"])
            if weather.get("hi2") is not None:
                rows.append("Tomorrow %d/%d" % (round(weather["hi2"]), round(weather["lo2"])))

            d.set_font("bitmap6")
            y = 48
            for text in rows[:6]:
                d.text(util.truncate(d, text, left_w), 4, y, scale=1)
                y += 10
        else:
            d.set_font("bitmap6")
            d.text("No forecast yet", 4, 48, scale=1)
            d.text("Press UP to retry", 4, 58, scale=1)

        # --- inside ------------------------------------------------------
        right_w = WIDTH - COL2_X - 4
        if indoor:
            _temp(d, indoor.get("temp"), COL2_X, 20, scale=2)
            d.set_font("bitmap6")
            rows = ["Humidity %.0f%%" % indoor["humidity"]]
            if indoor.get("pressure"):
                trend = state.pressure_trend()
                arrow = ""
                if trend is not None:
                    arrow = "  %s%.1f" % ("+" if trend >= 0 else "", trend)
                rows.append("%.0f hPa%s" % (indoor["pressure"], arrow))
            if indoor.get("dew") is not None:
                rows.append("Dew %.1f%s" % (indoor["dew"], util.temp_unit()))

            gas = indoor.get("gas_raw")
            baseline = state.get("gas_baseline")
            ratio = None
            if gas:
                rows.append("Gas %.1f k" % (gas / 1000.0))
                if not indoor.get("gas_trusted"):
                    # The number is real but means nothing yet, so say why
                    # rather than inventing an air-quality verdict.
                    rows.append("Air  %s" % (indoor.get("gas_reason") or "warming"))
                elif baseline:
                    ratio = gas / baseline
                    if ratio >= 0.85:
                        verdict = "clean"
                    elif ratio >= 0.6:
                        verdict = "elevated"
                    else:
                        verdict = "POOR"
                    rows.append("Air %d%%  %s" % (round(ratio * 100), verdict))

            y = 48
            for text in rows[:5]:
                d.text(util.truncate(d, text, right_w), COL2_X, y, scale=1)
                y += 10

            if ratio is not None:
                bar_w = right_w - 6
                fill = int(util.clamp(ratio, 0.0, 1.2) / 1.2 * bar_w)
                d.rectangle(COL2_X, 100, bar_w, 9)
                d.set_pen(WHITE)
                d.rectangle(COL2_X + 1, 101, bar_w - 2, 7)
                d.set_pen(BLACK)
                d.rectangle(COL2_X + 1, 101, max(fill - 1, 0), 7)
        else:
            d.set_font("bitmap6")
            d.text("Sensor not", COL2_X, 48, scale=1)
            d.text("responding", COL2_X, 58, scale=1)

        if alerts:
            _banner(d, 0, 110, WIDTH, 15, alerts[0]["text"])
            return
        self._status(4, 117, WIDTH - 8, status, left=status.get("credit"))

    # -- view 2 (button C): BBC headlines --------------------------------
    def news_view(self, headlines, status):
        d = self.d
        self._clear()
        clock = (status.get("updated") or "").split()[-1]
        self._header("BBC NEWS", clock if ":" in clock else "")

        d.set_font("bitmap6")
        if not headlines:
            d.text("No headlines yet.", 6, 40, scale=1)
            d.text("Press UP to fetch them over WiFi.", 6, 54, scale=1)
        else:
            y = 20
            for item in headlines:
                lines = util.wrap(d, item, WIDTH - 22, 1, 2)
                if y + 9 * len(lines) > 110:
                    break
                d.rectangle(6, y + 2, 3, 3)          # bullet
                for index, line in enumerate(lines):
                    d.text(line, 14, y + index * 9, scale=1)
                y += 9 * len(lines) + 5

        self._status(6, 117, WIDTH - 12, status, left="BBC News")

    # -- dispatch ---------------------------------------------------------
    def render(self, view, weather, indoor, alerts, state, status, headlines=None):
        if view == VIEW_DETAIL:
            self.detail_view(weather, indoor, state, alerts, status)
        elif view == VIEW_NEWS:
            self.news_view(headlines or [], status)
        else:
            # VIEW_SECRET is reserved and deliberately falls back to the
            # badge, so an unknown view can never leave a blank screen.
            self.badge(weather, indoor, alerts, status)
        self.d.update()

    def message(self, title, detail=""):
        """Full-screen notice, used for setup errors and first boot."""
        self._clear()
        self._header(title)
        self.d.set_font("bitmap8")
        self.d.text(detail, 8, 40, WIDTH - 16, 1)
        self.d.update()
