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

# 1-bit PNG, 96x128. Build it with: python3 tools/make_photo.py me.jpg
# Set to None to draw initials instead.
PHOTO = "photo.png"

# --------------------------------------------------------------------------
# WiFi
# --------------------------------------------------------------------------
WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
WIFI_COUNTRY = "GB"             # ISO country code: regulatory domain
WIFI_TIMEOUT = 25               # seconds before giving up

# --------------------------------------------------------------------------
# Location & units
# --------------------------------------------------------------------------
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
REFRESH_MINUTES = 30            # how long to sleep between network refreshes
SENSOR_ONLY_ON_BUTTON = True    # a button wake re-reads the BME688 but skips WiFi

# --------------------------------------------------------------------------
# Hardware on the Qw/ST chain
# --------------------------------------------------------------------------
I2C_SDA = 4                     # Badger 2040 W Qw/ST connector = GP4/GP5 (I2C0)
I2C_SCL = 5
BME688_ADDRESS = 0x77           # 0x76 if you bridge the address jumper
DRV2605_ADDRESS = 0x5A

# The Pimoroni haptic breakout ships with a linear resonant actuator.
# Set to "ERM" if you soldered on an eccentric-rotating-mass motor instead.
HAPTIC_ACTUATOR = "LRA"
HAPTIC_ENABLED = True
HAPTIC_QUIET_HOURS = (22, 7)    # (start_hour, end_hour) local; None to disable
SHOW_BATTERY = False            # see README — not all Badger revisions wire VBAT

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
# Indoor sensor alerts
# --------------------------------------------------------------------------
# The BME688's gas channel is a relative VOC sensor, NOT a calibrated gas
# detector. BadgerSett learns a rolling baseline and warns when the reading
# drops sharply below it. Read the safety note in the README.
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
