# BadgerSett

A Pimoroni **Badger 2040 W** name badge that also tells you the weather —
outside from a forecast API, inside from a BME688 — and taps you on the
chest with a haptic buzzer when something is about to go wrong.

```
+------------+-------------------------------+
|            | CHRISTOPHER WENHAM            |
|            | Principal Engineer            |
|   photo    | Badger Industries             |
|            | ----------------------------- |
|            | (**) 19°C     H 20  L 15      |
|            |      Overcast Gust 28         |
|            |               Rain 38%        |
|            | ----------------------------- |
|            | IN 22.4°C  47%  1015 hPa      |
+------------+-------------------------------+
|            | /!\ Tornado Warning           |
|            | Updated 17:15          BADGE  |
+------------+-------------------------------+
```

## What it does

- **Badge view (A)** — your photo, name, title, current conditions, and
  an indoor one-liner.
- **Detail view (B)** — outside and inside side by side: feels-like,
  wind and gusts, rain probability, today's high/low, against humidity,
  sea-level-corrected pressure with a 3-hour trend, dew point, and air
  quality versus a learned baseline.
- **News view (C)** — top headlines from the BBC's published RSS feeds.
- **Haptic alerts** — escalating patterns for severe weather and indoor
  hazards, with de-duplication, quiet hours, and a mute button.
- Sleeps between refreshes so a battery lasts, and keeps showing the last
  good data when WiFi is not available.

## Hardware

| Part | Notes |
|---|---|
| Badger 2040 **W** | The W is required — the non-W has no WiFi. |
| Adafruit BME688 | I²C `0x77` (`0x76` if you bridged the jumper) |
| Pimoroni DRV2605L haptic buzzer | I²C `0x5A` |

Both breakouts chain off the Qw/ST connector, which is **GP4 (SDA) /
GP5 (SCL)** — I²C bus 0. Daisy-chain them with a Qw/ST cable; the
addresses do not collide.

## Firmware

Flash Pimoroni's **Badger 2040 W** MicroPython firmware from
[pimoroni/badger2040 releases](https://github.com/pimoroni/badger2040/releases).
It already contains everything BadgerSett needs — `breakout_bme68x` for
the sensor and `pngdec` for the photo — so there are no libraries to
install. The DRV2605L is driven by this project's own register-level
driver.

## Install

```bash
cp config.example.py config.py    # then edit it: WiFi, name, location
pip3 install mpremote Pillow
python3 tools/make_photo.py me.jpg --preview   # -> photo.png
./tools/deploy.sh
```

Reset the badge and it runs. To watch the serial console while it works:

```bash
mpremote connect auto run main.py
```

Check the wiring first if anything looks off:

```bash
mpremote connect auto run tools/selftest.py
```

That scans I²C, prints live sensor readings, plays each haptic pattern,
and offers to auto-calibrate the actuator.

## Buttons

| Button | Action |
|---|---|
| **A** | Badge view |
| **B** | Detail view — forecast and sensors together |
| **C** | BBC news headlines |
| **UP** | Force a network refresh now |
| **DOWN** | Mute / unmute the haptics (survives sleep) |
| **A + C, then UP** | Reserved. Detected, deliberately does nothing yet |

The chord is checked before the individual buttons, since otherwise the
A in it would simply switch to the badge view and the combination could
never be told apart. To give it a purpose, fill in `_secret_action()` in
`badgersett/app.py`.

A button wake re-reads the sensor but skips WiFi, so switching views is
instant and cheap. Set `SENSOR_ONLY_ON_BUTTON = False` to refresh the
forecast on every press instead.

## How the alerting works

Alerts carry a level: **1** notice, **2** warning, **3** severe. Each one
has a stable key, and the buzzer only fires when a key is *new* or has
*escalated*. Re-running every 30 minutes will not buzz you every 30
minutes, and an alert calming down never buzzes.

| Level | Pattern (TI ROM effects) |
|---|---|
| 1 | a single soft bump |
| 2 | two double-clicks |
| 3 | alternating 750 ms alerts and strong buzzes |

Severe (level 3) alerts override `HAPTIC_QUIET_HOURS`; quieter ones wait
until morning. `DOWN` mutes everything.

**Forecast sources.** Two, and they stack:

1. **Open-Meteo** (worldwide, no key, no sign-up) supplies the forecast,
   and threshold rules over it catch thunderstorms, hail, freezing rain,
   damaging gusts, heat and hard freezes. These run regardless of which
   official feed you use, so you are still covered where none reaches.
2. **An official warning service**, used verbatim, chosen with
   `ALERT_SOURCE`:

| `ALERT_SOURCE` | Service | Coverage |
|---|---|---|
| `"metoffice"` | Met Office National Severe Weather Warning Service | UK (default) |
| `"nws"` | US National Weather Service | USA |
| `"none"` | — | threshold rules only |

### Met Office warnings (UK)

The Met Office publishes a warnings RSS feed per region, listed on their
own [RSS guide](https://weather.metoffice.gov.uk/guides/rss), so it is a
supported public feed rather than something scraped off the website. It
suits a battery-powered badge unusually well: it is **already filtered to
your region** — no coordinate lookup, no area matching — and it is about
600 bytes when there is nothing to report, which is most of the time.

Set `MET_REGION` to your region code (`"se"` is London & South East
England, which covers Hove). The full list of the 16 regional codes plus
`"uk"` is in `config.example.py` and in `badgersett/metoffice.py`.

Warning colours map straight onto the badge's alert levels:

| Colour | Met Office meaning | Level | Buzz |
|---|---|---|---|
| Yellow | be aware | 1 | a soft bump |
| Amber | be prepared | 2 | two double-clicks |
| Red | take action | 3 | alternating alerts and strong buzzes |

Yellow warnings are common in the UK, so they get the gentlest possible
nudge. Set `MET_MIN_COLOUR = "Amber"` to ignore them entirely.

Warnings are keyed by hazard, so a yellow wind warning upgraded to amber
re-buzzes as an escalation rather than appearing as a second warning.

**Attribution.** The Met Office asks that you credit them when using
these feeds. The weather view carries a data credit line for this
reason — please leave it in place.

### A note on transport

Open-Meteo is fetched over plain HTTP on purpose: a TLS handshake costs
around 40KB of RAM, which is a lot on an RP2040, and nothing secret is
being sent. The warning feeds use HTTPS. The Met Office feed is small
enough to read with a hard 24KB cap; the NWS feed is not — an active
alert feed there can run to hundreds of kilobytes — so that client
streams the response in 512-byte chunks and scans for the few fields it
needs, never holding the whole body.

## News

Headlines come from the BBC's own RSS feeds — published feeds, not
scraped pages. `NEWS_FEED` takes a key or a full URL:

| Key | Feed |
|---|---|
| `top` | Top stories (default) |
| `uk` / `world` | UK and world news |
| `technology` / `science` | Section feeds |
| `sussex` | BBC Sussex regional news |

A feed is 15–30KB, far more than the badge can hold, but item titles
start about 1KB in. The client streams the response and stops as soon as
it has enough headlines — typically ~3.5KB of a 28KB feed. Headlines are
cached, so pressing C works offline; `NEWS_ENABLED = False` skips the
fetch entirely and saves the radio time.

The BBC's terms for feed reuse are linked from each feed's own
`<copyright>` element. The news view credits them.

## Memory: why the PNG decoder is built on demand

The RP2040 has ~190KB of usable heap, and a TLS handshake needs a large
*contiguous* block — about 50KB. `pngdec.PNG()` allocates roughly 48KB
of its own. Holding a decoder for the life of the UI object leaves the
largest free block at ~56KB, which is right on the edge: adding one more
module was enough to turn both HTTPS feeds into `ENOMEM`.

MicroPython's garbage collector does not compact, so `gc.collect()`
cannot fix that by itself — the fix is to not hold the allocation.
`ui.UI` therefore builds the PNG decoder only while drawing the photo
and releases it immediately. If you add anything else that grabs tens of
kilobytes, do the same, or the symptom will show up somewhere unrelated.

## Timekeeping

Both clocks — the RP2040's own RTC and the battery-backed PCF85063A —
start unset at 2000-01-01, and the RP2040's is wiped by every power cut.

On each successful connection the badge takes the time from NTP (UTC),
then shifts it to local using the `utc_offset_seconds` Open-Meteo returns
for your coordinates. That handles BST, and any other DST rule, without
a timezone database on the badge. The result is written to the PCF so it
survives the next power cut.

`Updated HH:MM` is the time of the fetch, read from that clock — not the
forecast's own timestamp, which Open-Meteo quantises into 15-minute
buckets.

**Refreshes are scheduled by data age, not by wake reason.** An earlier
version asked `badger2040.woken_by_rtc()` whether this was a timed wake.
That reports why the board *powered on*, so on USB — where `sleep_for()`
cannot actually cut power — it stays False indefinitely and nothing ever
refreshes. Worse, the clock was only ever set by a refresh, so the two
deadlocked: a frozen timestamp, an unset RTC, and a badge that still
responded to buttons and so looked perfectly healthy.

Now the badge records when it last fetched and compares that against
`REFRESH_MINUTES`. Unknown state — no clock, no record, or a clock that
has jumped backwards — always counts as "refresh now", so a lost clock
heals itself on the next wake instead of wedging.

## About the gas sensor — please read

The BME688's gas channel measures **electrical resistance that falls in
the presence of volatile organic compounds**. It is not a calibrated gas
meter, and it cannot identify a specific gas. Without Bosch's proprietary
BSEC library there is no absolute IAQ number, so BadgerSett learns a
rolling baseline of "normal" for your environment and warns when the
reading drops sharply below it. It deliberately stops updating that
baseline while a reading is depressed, so an ongoing event cannot quietly
become the new normal.

**This is not a safety device.** It will not reliably detect carbon
monoxide, natural gas, or smoke, and it must never be relied on for any
of them. Fit a proper certified CO alarm and smoke alarm. Treat the badge
as "something changed in here, go look."

### The gas channel does not work on battery

Measured on real hardware: from a cold start the BME688 reports an
implausibly high resistance, collapses, then **climbs for minutes** —
still rising 3–4% per 15 seconds after three full minutes. The heater
has to run continuously before the number means anything.

A badge that wakes, reads and powers down samples the same point of the
same repeatable burn-in curve every time. Early builds of this project
returned a byte-identical 5684.846 ohms on every cycle for hours, which
looked like a working sensor and was nothing of the kind.

### Reading the air figure

The detail view shows air quality as a **signed deviation from the
learned baseline**: `Air +15% clean` means 15% above normal, `Air -50%
POOR` means half of it. The bar carries a tick at the baseline so
"normal" is visible rather than implied.

It is shown this way because the underlying quantity is
resistance-over-baseline, and BME688 resistance *rises* in clean air. As
a raw percentage that produced readings like "115% clean", which reads
like a fault rather than good news.

Expect a positive drift during long continuous runs: the sensor's
resistance climbs as it conditions, and the baseline is deliberately slow
to follow, so you are partly watching burn-in rather than air quality.
What matters is a sharp *fall*.

So `GAS_WARMUP_S` (default 300) gates it: until the heater has run that
long, `gas` is withheld, the baseline does not learn, no gas alert can
fire, and the detail view says `Air warming 12s` instead of inventing a
verdict. In practice **the gas channel only works on USB power**, where
the badge stays awake.

Temperature, humidity and pressure are unaffected — they are valid
immediately and are the readings worth trusting.

The heater also warms the package, so indoor temperature reads a degree
or two high; mount the breakout away from the board if that bothers you.

## Power

After each refresh the badge sets an RTC alarm and cuts its own power,
which is why a battery lasts. `REFRESH_MINUTES` (default 30) is the main
lever: the WiFi radio dominates consumption, so halving the refresh rate
roughly halves the draw.

On USB power the badge cannot truly power down, so `sleep_for()` blocks
until a button or the alarm fires and then returns — the main loop is
written to handle both paths.

`SHOW_BATTERY` is off by default: battery sense is not wired the same way
across Badger revisions, and a confidently wrong battery gauge is worse
than none.

## Configuration

Everything lives in `config.py`, which is gitignored so your WiFi
password and home coordinates stay off GitHub. `config.example.py` is
the documented template. Switch `UNITS` between `"metric"` and
`"imperial"` and every view, threshold and alert follows.

Set `ALTITUDE_M` to your height above sea level, or the pressure reading
will not match the forecast — it is about 1 hPa per 8 metres.

The shipped defaults are set for Hove: coordinates on the seafront,
`MET_REGION = "se"`, and heat/cold thresholds a little lower than inland
values, since the south coast rarely reaches them.

## Development

```bash
python3 tools/test_offline.py
```

Runs the logic on your laptop with the hardware faked: alert rules and
de-duplication, the gas baseline, the chunked NWS parser, the DRV2605L
register sequence, and every screen layout in seven states — including
checks that nothing is drawn off the 296×128 panel and that a very long
name is truncated rather than overflowing.

```
badgersett/
  app.py        wake -> read -> refresh -> alert -> draw -> sleep
  ui.py         the three screen layouts
  icons.py      weather glyphs drawn with primitives, not bitmaps
  weather.py    Open-Meteo client + WMO code table
  metoffice.py  UK Met Office regional warnings (RSS)
  news.py       BBC headlines, streamed and stopped early
  nws.py        streaming parser for US government alerts
  alerts.py     rules, levels, de-duplication, quiet hours
  sensor.py     BME688 wrapper
  haptics.py    DRV2605L driver
  state.py      what survives deep sleep
  net.py        WiFi up/down
```

## Troubleshooting

**Nothing on I²C** — reseat both ends of the Qw/ST cable, then run
`tools/selftest.py`, which prints every address it finds.

**`no BME688 at 0x77`** — try `BME688_ADDRESS = 0x76`; selftest lists
what is actually there.

**Haptics feel weak or rattly** — `HAPTIC_ACTUATOR` is probably wrong.
Pimoroni's breakout ships with an LRA; set `"ERM"` only if you fitted a
rotating-mass motor. Then run the auto-calibration in selftest with the
actuator mounted the way you actually wear it, since the mounting changes
its resonant frequency.

**WiFi will not connect** — set `WIFI_COUNTRY` to your ISO code; the
regulatory domain affects which channels the radio will use. The Pico W
is 2.4GHz only.

**No warnings ever appear** — that is usually correct; the UK is quiet
most of the time. To check the feed itself, open
`https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se`
in a browser. An empty `<channel>` with no `<item>` elements means there
is genuinely nothing in force for your region.

**Screen shows stale data** — that is deliberate. A failed refresh keeps
the last good forecast and flags `OFFLINE` in the status line rather than
blanking the screen.
