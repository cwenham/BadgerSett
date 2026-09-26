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

| Part | Required? | Notes |
|---|---|---|
| Badger 2040 **W** | **yes** | The W is required — the non-W has no WiFi. |
| Adafruit BME688 | optional | I²C `0x77` (`0x76` if you bridged the jumper) |
| Pimoroni DRV2605L haptic buzzer | optional | I²C `0x5A` |

Both breakouts chain off the Qw/ST connector, which is **GP4 (SDA) /
GP5 (SCL)** — I²C bus 0. Daisy-chain them with a Qw/ST cable; the
addresses do not collide.

## Running without the breakouts

Both I²C boards are optional. With neither fitted you still get the badge,
the forecast, Met Office warnings, BBC headlines, the radio scanner and
the clock — everything except indoor readings and the buzz.

```python
BME688_ENABLED = False      # no temperature, humidity, pressure or air quality
HAPTIC_ENABLED = False      # alerts appear on screen but do not buzz
```

Setting these to `False` means the badge does not go looking for that
board, so its absence is never reported as a fault. It matters that this
is distinct from a board that *is* fitted and not answering, which is a
real problem and still says `NO BME` in the status line and `Sensor not
responding` on the detail view.

Without a sensor the detail view drops the `INSIDE` column and lays the
forecast across the full width instead, adding humidity and tomorrow's
rain chance rather than leaving an empty box. The badge view simply omits
its indoor line.

`POWER_MODE = "auto"` also stops staying awake on USB when no sensor is
fitted — staying awake exists to keep the gas heater conditioned, and with
no heater there is nothing to keep warm. An explicit `"awake"` is still
honoured if you want the faster button response.

Leaving an enabled board absent is handled too: the badge logs it, draws
everything else, and carries on.

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
| **A + C, then UP** | The radio scanner (see below) |

The chord is checked before the individual buttons, since otherwise the
A in it would simply switch to the badge view and the combination could
never be told apart.

## The radio scanner

Hold **A** and **C**, then press **UP**. The badge surveys both radios
and lists what it can hear, strongest first.

| Button | On this screen |
|---|---|
| **UP** / **DOWN** | Previous / next page (wraps around) |
| **B** | Switch between the list and the radar |
| **C** | Scan again |
| **A** | Back to the badge |

The buttons mean different things here than they do elsewhere, so DOWN
pages rather than muting and UP pages rather than forcing a refresh.

**The list** gives each device a signal bar, its strength in dBm, `W` or
`B` for which radio heard it, a name, and a detail — the channel and
security for WiFi, the maker and address type for Bluetooth. Eight per
page.

**The radar** plots signal strength as distance: the closer to the
middle, the louder. Rings mark −40 through −100 dBm. **The bearing is
not a real direction** — one antenna cannot tell where a signal came
from. It is a hash of the device's address, so each device keeps the
same spot between redraws instead of jumping about. Bearings come from
the address rather than the name for a reason: a dozen devices all
called "Apple" would otherwise stack up along one line.

Both radios share the CYW43439, so the scans run one after the other:
WiFi takes about half a second, and `SCAN_BLE_MS` (default 6000) is how
long to listen for Bluetooth. A survey took 7.7s and heard 56 devices on
the bench. Results are cached in `scan.json`, so paging and switching
modes are instant; only the chord and **C** trigger a new scan.

Two limits worth knowing:

- **Bluetooth LE only.** MicroPython does not expose Classic Bluetooth,
  so older headsets and car kits will not appear.
- **You cannot count or identify people.** Modern phones rotate their
  BLE address every ~15 minutes and mostly advertise no name — of 58
  devices heard in one test, 21 used rotating addresses and only 13 gave
  a name. The same phone will appear as a new device over time.

A button wake re-reads the sensor but skips WiFi, so switching views is
instant and cheap. Set `SENSOR_ONLY_ON_BUTTON = False` to refresh the
forecast on every press instead.

## How the alerting works

A failed warnings fetch is not an all-clear. `metoffice.fetch()` returns
`None` when it could not reach the feed and `[]` when the feed genuinely
carries no warnings, and the runtime log records the difference. Treating
the two alike is how a month of silently failing warnings could look like
a quiet month.

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

## Memory: the TLS reserve

The RP2040 has ~190KB of usable heap, and a TLS handshake needs a large
*contiguous* block. MicroPython's garbage collector does not compact, so
`gc.collect()` cannot help once the heap is in pieces — **free memory is
not the number that matters, the largest single block is**. The badge
can sit at 107KB free with a largest block of 14KB, and in that state
every HTTPS fetch fails with `ENOMEM` while plain-HTTP weather carries
on working. Both feeds that matter are HTTPS: Met Office warnings and
BBC headlines.

Importing the package and building the display is enough to chop the
heap up that badly, and it does not take much to tip it over — adding
one branch to `app.py` and a handful of lines to `ui.py` once did it.

So `main.py` claims a 48KB block *before* it imports anything else,
while the heap is still whole, and hands it to `app.run()` in a list it
then drops:

```python
_handover = [bytearray(48 * 1024)]      # no reference kept here
from badgersett import app
app.run(_handover)                      # run() pops it out
```

The handover matters. Passing the buffer directly leaves `main.py`'s own
global pointing at it, and then freeing `app._reserve` frees nothing —
the symptom is identical to having no reserve at all. `tools/test_offline.py`
checks for exactly that.

`app.py` releases the block around each network refresh and re-claims it
afterwards. The re-claim is expected to fail from the second refresh on,
because the first HTTPS session keeps some of what was freed; measured
on a Badger 2040 W the largest block then settles at about 23KB, which
is still comfortably enough. That is why the failure is logged once and
not treated as an error.

`TLS_RESERVE_KB` tunes the size. If you add anything that grabs tens of
kilobytes and holds it, expect the symptom to surface somewhere
unrelated — an HTTPS feed quietly returning nothing.

For the same reason the photo is stored pre-packed as a framebuffer
(`.fb`) and blitted, rather than decoded: `pngdec.PNG()` wanted ~48KB of
its own, and holding it was enough to wedge the board.

## Multiple WiFi networks, and where they are

`WIFI_NETWORKS` is an ordered list. On each refresh the badge scans, then
joins the first entry in that list it can see:

```python
WIFI_NETWORKS = [
    {"label": "Home",   "ssid": "...", "password": "...",
     "lat": 50.8279, "lon": -0.1687, "met_region": "se", "altitude": 10},
    {"label": "Office", "ssid": "...", "password": "...",
     "lat": 51.5074, "lon": -0.1278, "met_region": "se", "altitude": 11},
]
```

**Order is preference, not signal strength.** A weaker network earlier in
the list beats a stronger one further down, so put the places you care
about first. Verified on hardware: with two APs in range at −74 dBm and
−71 dBm, the −74 dBm one was chosen because it came first.

**Each network carries its location**, and that is the point of the
feature: joining a different network moves the forecast coordinates, the
Met Office warnings region and the altitude used for pressure
correction, together. Warnings for the region you are not in would be
worse than no warnings.

Everything except `ssid` is optional and falls back to the top-level
settings, so a single `WIFI_SSID` / `WIFI_PASSWORD` config still works
unchanged.

Details:

- A **hidden** network broadcasts no SSID and never appears in a scan, so
  it cannot be preferred by signal. It is still tried by name, after
  every visible candidate.
- One SSID on **several APs** is de-duplicated; the strongest is used for
  reporting.
- If the preferred network is visible but refuses the connection (a
  changed password, say), the badge falls through to the next candidate.
  `WIFI_MAX_ATTEMPTS` caps how many it tries in one wake, so a bad
  network cannot drain the battery retrying.
- The scan costs about half a second.

The place last joined is remembered, so a button press with no network
still labels the cached forecast with the right location. The detail
view shows it beside `OUTSIDE`.

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

### The gas channel only works while the badge stays awake

Measured on real hardware: from a cold start the BME688 reports an
implausibly high resistance, collapses, then **climbs for minutes** —
still rising 3–4% per 15 seconds after three full minutes.

The heater only fires *during a measurement*. So "keeping it warm" does
not mean leaving it switched on; it means **sampling every few seconds,
without a break**, which only a badge that stays awake can do. A sleeping
badge's heater is always cold, on battery or USB alike.

Early builds got this wrong twice:

1. A badge that woke, read and slept sampled the same point of the same
   repeatable burn-in curve every time, returning a byte-identical
   5684.846 ohms for hours. It looked like a working sensor.
2. The first warm-up gate timed conditioning from when the sensor object
   was *created*. On USB the loop reused one object across a 30-minute
   idle wait, so the gate reported half an hour of warm-up for a heater
   that had been cold throughout — and trusted readings it should not
   have. A baseline learned that way was about 3× too low.

The gate now times an **unbroken run of samples**. `GAS_WARMUP_S`
(default 300) of continuous sampling is needed before `gas` is trusted;
a gap longer than `GAS_MAX_GAP_S` (default 60) lets the heater cool and
restarts the clock. Until then the baseline does not learn, no gas alert
can fire, and the detail view says why — `Air warming 12s` or `Air usb
only` — rather than inventing a verdict. On hardware, a continuously
sampled sensor was trusted after 309 seconds.

Temperature, humidity and pressure are unaffected by any of this — they
are valid immediately in every power mode.

### Reading the air figure

The detail view shows air quality as a **signed deviation from the
learned baseline**: `Air +15% clean` means 15% above normal, `Air -50%
POOR` means half of it. The bar carries a tick at the baseline so
"normal" is visible rather than implied.

It is shown this way because the underlying quantity is
resistance-over-baseline, and BME688 resistance *rises* in clean air. As
a raw percentage that produced readings like "115% clean", which reads
like a fault rather than good news.

Expect a positive drift during long continuous runs: resistance climbs as
the sensor conditions and the baseline is deliberately slow to follow. A
sharp *fall* is the signal that matters.

The heater also warms the package, so indoor temperature reads a degree
or two high; mount the breakout away from the board if that bothers you.

## Screen refreshes: the flash, and what it costs

The black/white flash is the `NORMAL` waveform inverting the whole panel
to clear it. It is also most of what a refresh costs in time. Measured on
a Badger 2040 W:

| Waveform | Full `update()` | `partial_update()`, 32px band |
|---|---|---|
| `NORMAL` | 4.70 s | 3.74 s |
| `MEDIUM` | 2.62 s | 2.62 s |
| `FAST` | 1.00 s | 0.99 s |
| `TURBO` | 0.32 s | 0.32 s |

**`partial_update()` is not the lever it looks like.** Restricting the
update to a band saves nothing worth having — the cost is in driving the
waveform, not in the number of rows — so the badge does not track dirty
rectangles. It changes waveform instead.

A redraw that does not change mode — a new clock time, fresh readings, an
alert appearing — uses `FAST_REDRAW_SPEED` (default `TURBO`) and does not
flash. Pressing A, B or C replaces the whole screen anyway, so those take
the clean `NORMAL` waveform. The first draw after boot does too, since
nothing is known about what is already on the panel.

Fast waveforms do less work to settle each pixel and leave ghosting, so
every `GHOST_CLEAR_EVERY`-th redraw (default 12) uses `NORMAL` to wipe it.
The dithered photo is the part most likely to show residue; if it bothers
you, lower that number, move `FAST_REDRAW_SPEED` to `"fast"` or
`"medium"`, or set `FAST_REDRAW = False` to go back to always flashing.

On power the honest answer is: it helps, but it is not where the battery
goes. The panel only draws current while it updates, so awake mode's
12 redraws an hour cost roughly 4.70 s each at `NORMAL` against 0.32 s at
`TURBO` — around a 1% change against the ~24 mA the badge averages, which
the gas heater and the WiFi radio dominate. The reason to do it is that
the screen stops flashing every five minutes.

## Power

`POWER_MODE` decides how the badge spends the time between refreshes:

| Mode | Between refreshes | Gas readings | Battery life (2000 mAh) |
|---|---|---|---|
| `"auto"` (default) | awake on USB, deep sleep on battery | on USB only | months on battery (est.) |
| `"sleep"` | always deep sleep | never | months (est.) |
| `"awake"` | never sleeps; samples every `GAS_SAMPLE_S` | always | **83 hours, measured** |

The awake figure is not an estimate. A 2000 mAh cell ran the badge in
`"awake"` mode — heater conditioned every 5 seconds, WiFi refreshes on
the usual schedule — for **83.3 hours**, from 4.173 V down to the 3.2 V
cutoff, which works out at about **24 mA average**. The discharge was
close to linear at roughly 12 mV per hour until the last few hours,
where it fell away sharply, as lithium cells do. It stopped on its own
at the cutoff and displayed the charge-me screen, so the protection
path is tested rather than assumed.

`tools/battery_report.py runtime.log --capacity 2000` prints that curve
from the log on the badge, splitting it into power-on sessions and
skipping the ones that were really on USB.

Asleep, the badge sets an RTC alarm and cuts its own power — which also
cuts the 3.3V rail to the BME688. `REFRESH_MINUTES` is then the main
lever: the WiFi radio dominates consumption.

Awake, it samples the sensor every `GAS_SAMPLE_S` (default 5) seconds to
keep the heater conditioned, and only redraws the e-ink on a timer
(`AWAKE_REDRAW_MINUTES`, default 5), a button press, a new alert, or a
due refresh. Two things it guards against that a sleeping badge never
meets: a lost WiFi network would otherwise become a retry loop, so
attempts are spaced at least five minutes apart; and returning on the
first button edge would split the A+C+UP chord, so presses are collected
until every button is released.

### Batteries

**The Badger 2040 W has no charging circuit** — Pimoroni leave it out so
the board is safe with alkaline cells too. Plugging in USB powers the
board but does **not** charge an attached LiPo; charge it on an external
charger.

It also has **no low-voltage cutoff**, and will run down to about 2.7V —
below the ~3.0V at which LiPo cells are damaged. `LOW_BATTERY_V`
(default 3.2) powers the badge off below that on battery, leaving a
`BATTERY LOW` message on the e-ink. It is measured at VSYS, a little
below the cell itself. Set it to `None` only for alkaline cells.

VSYS is readable on this board (ADC3, shared with the WiFi chip, so only
while the radio is off): about 4.8V on USB, tracking the cell on battery.

### Running a battery experiment

1. Charge the LiPo to full **on an external charger**.
2. In `config.py`: `POWER_MODE = "awake"`, `RUNTIME_LOG = True`.
3. Deploy, then unplug USB. With a battery attached the board keeps
   running; the log records the switch as VSYS dropping below 4.6V.
4. Leave it. At `LOW_BATTERY_V` it powers itself off with a message.
5. Plug back in and pull the log:

   ```bash
   mpremote fs cp :runtime.log .
   python3 tools/battery_report.py runtime.log --capacity 2000
   ```

   The report separates the time on battery from any time on USB,
   gives the start and end voltage, and the average current that
   implies — the figure to compare with the estimate.

`runtime.log` gets a line on every boot and then every
`RUNTIME_LOG_MINUTES`, each carrying its own uptime and voltage, so the
last line before the cutoff records how long it lasted even if the file
has been trimmed. It is capped at ~96KB, about nine days of beats, and
trimmed by streaming rather than by reading the file in — at that size,
reading it in would exhaust the heap.

Each refresh logs what every feed actually returned, not just the
forecast:

```
2026-09-26 16:31 fetch up=12m vsys=4.88 mode=awake ok=1 news=4 warn=0 where=Home
```

`ok` is the forecast, `news` the headline count, and `warn` the number
of Met Office warnings — or `?` if that fetch failed. The three can fail
independently: the forecast is plain HTTP and the other two are HTTPS,
so a heap too fragmented for TLS takes out warnings and headlines while
the forecast keeps updating. Without these fields the log said `ok=1`
through all of it.

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
  scan.py       WiFi + BLE survey for the scanner screen
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

## License

The code is MIT licensed — see [LICENSE](LICENSE).

**The photographs are not.** `cwenhamBadge.jpg`, `cwenhamBadge.png` and
`cwenhamBadge.fb` are pictures of a person, included so the badge works
out of the box, and they are excluded from that licence: all rights
reserved. Replace them with your own before reusing this — which you
want to do anyway, since it is meant to be your face on your badge.
