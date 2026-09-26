"""BadgerSett configuration.

Copy to `config.py` and edit. `config.py` is gitignored so your WiFi
password and location never land in version control.

    cp config.example.py config.py
"""

# --------------------------------------------------------------------------
# Who you are
# --------------------------------------------------------------------------
NAME = "Your Name"
TITLE = "Job Title"
ORG = "Company / Team"          # shown small under the title; "" to hide

# Packed 1-bit image, 96x128, built by tools/make_photo.py (it writes
# both a .png for previewing and the .fb the badge actually loads).
# Set to None to draw initials instead.
PHOTO = "photo.fb"

# --------------------------------------------------------------------------
# WiFi
# --------------------------------------------------------------------------
# The badge scans for networks in range and joins the first one from
# this list that it can see. ORDER IS PREFERENCE: an earlier entry wins
# even if a later one has a stronger signal, so put home or office first.
#
# Each network carries the location it implies. Joining a different
# network therefore moves the forecast, the Met Office warnings region
# and the altitude used for pressure, all together - so the badge tells
# you about where you actually are.
#
# Per-network keys ("ssid" is the only required one):
#   ssid        the network name
#   password    its password ("" for an open network)
#   label       shown on the detail view; defaults to the ssid
#   lat / lon   coordinates for the forecast
#   met_region  Met Office region code (see MET_REGION below)
#   altitude    metres above sea level, for pressure correction
#
# Anything omitted falls back to the top-level settings further down.
WIFI_NETWORKS = [
    {
        "label": "Home",
        "ssid": "your-home-ssid",
        "password": "your-password",
        "lat": 50.8279, "lon": -0.1687,     # Hove
        "met_region": "se",
        "altitude": 10,
    },
    {
        "label": "Office",
        "ssid": "your-office-ssid",
        "password": "another-password",
        "lat": 51.5074, "lon": -0.1278,     # London
        "met_region": "se",
        "altitude": 11,
    },
]

WIFI_COUNTRY = "GB"             # ISO country code: regulatory domain
WIFI_TIMEOUT = 25               # seconds per attempt before giving up
WIFI_MAX_ATTEMPTS = 3           # how many candidates to try in one wake

# A hidden network broadcasts no SSID and will not appear in the scan.
# It is still tried by name, just after every visible candidate.

# --------------------------------------------------------------------------
# Location & units
# --------------------------------------------------------------------------
# Fallback location, used only for networks that do not set their own
# lat/lon (and by a legacy single-network config).
LATITUDE = 50.8279              # Hove, East Sussex
LONGITUDE = -0.1687
TIMEZONE = "auto"               # "auto" derives it from lat/long

UNITS = "metric"                # "metric" (C, km/h, mm) or "imperial" (F, mph, in)

# Height above sea level in metres. Used to convert the BME688's absolute
# pressure to sea-level pressure so it matches the forecast.
ALTITUDE_M = 10                 # Hove seafront is near enough sea level

# --------------------------------------------------------------------------
# Refresh & power
# --------------------------------------------------------------------------
# HTTPS needs one large *contiguous* block of RAM, and MicroPython's heap
# never compacts, so main.py reserves this much while the heap is still
# whole and hands it back around each fetch. Too small and Met Office
# warnings and BBC headlines fail with ENOMEM even with plenty free; too
# large and the rest of the app runs short. 48 works on a Badger 2040 W.
TLS_RESERVE_KB = 48

REFRESH_MINUTES = 30            # how often to fetch weather, warnings and news
SENSOR_ONLY_ON_BUTTON = True    # a button wake re-reads the BME688 but skips WiFi

# How the badge spends the time between refreshes:
#   "auto"   stay awake on USB, deep-sleep on battery (default)
#   "sleep"  always deep-sleep: longest battery life, but no gas readings
#   "awake"  never sleep: keeps the BME688 gas heater conditioned so air
#            quality works on battery too, at roughly 30-40x the current
#
# The gas channel only means something while the badge is awake, because
# the heater only fires during a measurement - "keeping it warm" means
# sampling it every few seconds, which a sleeping badge cannot do.
POWER_MODE = "auto"

# While awake:
GAS_SAMPLE_S = 5                # sample the BME688 this often, keeping it conditioned
AWAKE_REDRAW_MINUTES = 5        # redraw the e-ink on this timer (plus buttons/alerts)

# Runtime log for power experiments: appends to runtime.log on boot and
# then every RUNTIME_LOG_MINUTES, with uptime and supply voltage. Read the
# last line after the battery dies to see how long it lasted.
RUNTIME_LOG = False
RUNTIME_LOG_MINUTES = 10

# Power off when the supply falls below this on battery, to protect a LiPo.
# The Badger 2040 W has no charger and no cutoff of its own, and will run
# down to ~2.7V - below the ~3.0V at which LiPo cells are damaged. Measured
# at VSYS, a little below the cell itself. None disables it.
LOW_BATTERY_V = 3.2

# --------------------------------------------------------------------------
# Hardware on the Qw/ST chain
# --------------------------------------------------------------------------
# Both breakouts are optional. Set either to False if you do not have
# that board and the badge will not look for it: no "offline" warning, no
# empty column on the detail view, and nothing wasted scanning the bus.
# Everything else - weather, warnings, news, the scanner - works without
# them. With BME688_ENABLED = False there is nothing to keep warm, so
# POWER_MODE "auto" simply sleeps.
BME688_ENABLED = True
HAPTIC_ENABLED = True

I2C_SDA = 4                     # Badger 2040 W Qw/ST connector = GP4/GP5 (I2C0)
I2C_SCL = 5
BME688_ADDRESS = 0x77           # 0x76 if you bridge the address jumper
DRV2605_ADDRESS = 0x5A

# The Pimoroni haptic breakout ships with a linear resonant actuator.
# Set to "ERM" if you soldered on an eccentric-rotating-mass motor instead.
HAPTIC_ACTUATOR = "LRA"
HAPTIC_QUIET_HOURS = (22, 7)    # (start_hour, end_hour) local; None to disable

# --------------------------------------------------------------------------
# Severe weather
# --------------------------------------------------------------------------
# Official government warnings. Options:
#   "metoffice" - UK Met Office National Severe Weather Warning Service
#   "nws"       - US National Weather Service
#   "none"      - threshold rules only (below), which work worldwide
ALERT_SOURCE = "metoffice"

# --- Met Office (UK) ---
# Your region. Hove is in "se" (London & South East England).
#   os Orkney & Shetland     he Highlands & Eilean Siar   gr Grampian
#   st Strathclyde           ta Central, Tayside & Fife   ni N. Ireland
#   dg Dumfries, Galloway, Lothian & Borders              wl Wales
#   nw North West England    ne North East England        yh Yorks & Humber
#   wm West Midlands         em East Midlands             ee East of England
#   sw South West England    se London & South East       uk whole UK
MET_REGION = "se"
# Lowest warning colour that raises an alert: "Yellow", "Amber" or "Red".
# Yellow warnings are common in the UK, so they only get a soft bump
# (level 1); Amber is level 2 and Red is level 3. Set "Amber" if you only
# want to be disturbed by the serious ones.
MET_MIN_COLOUR = "Yellow"

# --- US NWS (only used when ALERT_SOURCE = "nws") ---
NWS_USER_AGENT = "BadgerSett/1.0 (you@example.com)"
NWS_MIN_SEVERITY = "Severe"     # "Extreme", "Severe", "Moderate", "Minor"

# Threshold rules applied to the Open-Meteo forecast. These run whatever
# ALERT_SOURCE is set to, so you still get warned where no official feed
# reaches. Units follow UNITS. Defaults below are tuned for the south
# coast: mild and damp, so heat and cold thresholds sit lower than they
# would inland, and the gust thresholds matter more than they would
# somewhere sheltered.
GUST_WARN = 60                  # km/h (or mph if imperial)
GUST_SEVERE = 90
HEAT_WARN = 28                  # degrees, in your chosen unit
COLD_WARN = -2

# --------------------------------------------------------------------------
# Secret screen: the radio scanner (hold A and C, then press UP)
# --------------------------------------------------------------------------
# Lists WiFi access points and Bluetooth LE devices in range, strongest
# first. On that screen: UP/DOWN page through the list, B switches to the
# radar, C scans again, A leaves.
#
# Both radios share one chip, so the scans run one after the other. WiFi
# takes about half a second; SCAN_BLE_MS is how long to listen for
# Bluetooth. Longer hears more - a phone advertises every second or two -
# but the radio is on the whole time, so keep it modest on battery.
SCAN_BLE = True
SCAN_BLE_MS = 6000

# --------------------------------------------------------------------------
# News (button C)
# --------------------------------------------------------------------------
# Headlines come from the BBC's own published RSS feeds, not scraped pages.
# Set NEWS_ENABLED = False to skip the fetch entirely and save the radio
# time on every refresh.
NEWS_ENABLED = True

# A key from badgersett/news.py FEEDS, or any full BBC feed URL:
#   "top"  "uk"  "world"  "technology"  "science"  "sussex"
# "sussex" is the BBC's Sussex regional feed, local to Hove.
NEWS_FEED = "top"
NEWS_HEADLINES = 4              # how many to show; 4 fits comfortably

# --------------------------------------------------------------------------
# Indoor sensor alerts
# --------------------------------------------------------------------------
# The BME688's gas channel is a relative VOC sensor, NOT a calibrated gas
# detector. BadgerSett learns a rolling baseline and warns when the reading
# drops sharply below it. Read the safety note in the README.
# The gas heater needs an unbroken run of samples before its reading means
# anything: from cold it climbs for minutes. Until GAS_WARMUP_S seconds of
# continuous sampling, the reading is shown but treated as unusable - no
# baseline learning, no alerts, no air-quality verdict. A gap longer than
# GAS_MAX_GAP_S between samples lets the heater cool, and starts the
# warm-up again. See POWER_MODE, and the README, before relying on it.
GAS_WARMUP_S = 300
GAS_MAX_GAP_S = 60              # tolerates the ~20s pause during a WiFi refresh

GAS_ALERTS = True
GAS_DROP_WARN = 0.60            # resistance below 60% of baseline -> warn
GAS_DROP_SEVERE = 0.35          # below 35% of baseline -> severe
GAS_BASELINE_SAMPLES = 20       # samples blended into the rolling baseline

TEMP_LOW_WARN = 15              # indoor comfort band, in your chosen unit
TEMP_HIGH_WARN = 30
HUMIDITY_LOW_WARN = 25          # %RH
HUMIDITY_HIGH_WARN = 70

# Pressure falling faster than this over 3 hours suggests an incoming storm.
PRESSURE_DROP_WARN = 3.0        # hPa / 3h

DEBUG = True                    # print diagnostics to the USB serial console
