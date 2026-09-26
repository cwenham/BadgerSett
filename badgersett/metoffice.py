"""Met Office National Severe Weather Warning Service.

The Met Office publishes a warnings RSS feed per UK region, listed on
their own RSS guide page, so this is a supported public feed rather than
a scraped one. Two properties make it an unusually good fit for a badge:

  * It is already filtered to your region, so there is no area matching
    to do and no coordinate lookup.
  * It is tiny — around 600 bytes when there is nothing to report, which
    is the normal state of affairs.

Item titles follow a fixed shape:

    Yellow warning of rain affecting London & South East England

...which carries the warning colour and the hazard, and the colour maps
straight onto the badge's three alert levels.

Attribution: the Met Office asks that you credit them when using these
feeds. The badge does this on the weather view.
"""

import gc

try:
    import urequests as requests
except ImportError:
    import requests

from . import util

BASE = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/"

# Region codes from the Met Office RSS guide.
REGIONS = {
    "os": "Orkney & Shetland",
    "he": "Highlands & Eilean Siar",
    "gr": "Grampian",
    "st": "Strathclyde",
    "ta": "Central, Tayside & Fife",
    "dg": "Dumfries, Galloway, Lothian & Borders",
    "ni": "Northern Ireland",
    "wl": "Wales",
    "nw": "North West England",
    "ne": "North East England",
    "yh": "Yorkshire & Humber",
    "wm": "West Midlands",
    "em": "East Midlands",
    "ee": "East of England",
    "sw": "South West England",
    "se": "London & South East England",
    "uk": "United Kingdom",
}

# Met Office warning colours -> BadgerSett alert levels.
#   Yellow = be aware, Amber = be prepared, Red = take action.
# Yellow lands at level 1 on purpose: they are common in the UK and a
# soft bump is the right amount of interruption for one.
COLOURS = {"yellow": 1, "amber": 2, "red": 3}

MAX_BYTES = 24000          # a hard cap; the feed is normally tiny
MAX_WARNINGS = 4


def _unescape(text):
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'")):
        text = text.replace(entity, char)
    return text


def parse_title(title):
    """'Yellow warning of rain affecting X' -> ('yellow', 'rain').

    Returns (None, None) for anything that does not match, so an unexpected
    title is ignored rather than shown as a bogus alert.
    """
    title = _unescape(title).strip()
    lower = title.lower()
    if " warning of " not in lower:
        return None, None
    colour = lower.split(" warning of ", 1)[0].strip()
    if colour not in COLOURS:
        return None, None
    hazard = title[len(colour) + len(" warning of "):]
    cut = hazard.lower().find(" affecting ")
    if cut >= 0:
        hazard = hazard[:cut]
    return colour, hazard.strip()


def _items(body):
    """Yield each <item>'s <title> text."""
    position = 0
    while True:
        start = body.find("<item>", position)
        if start < 0:
            return
        end = body.find("</item>", start)
        if end < 0:
            return
        block = body[start:end]
        title_start = block.find("<title>")
        if title_start >= 0:
            title_end = block.find("</title>", title_start)
            if title_end > title_start:
                yield block[title_start + 7:title_end]
        position = end + 7


def fetch(region="se", min_colour="yellow", max_warnings=MAX_WARNINGS):
    """Return a list of {event, severity, rank, ends} dicts, worst first.

    The shape matches the NWS client's so alerts.py can treat them alike.
    """
    region = (region or "se").lower()
    if region not in REGIONS:
        util.log("unknown Met Office region %r; falling back to 'uk'" % region)
        region = "uk"
    floor = COLOURS.get(str(min_colour).lower(), 1)

    url = BASE + region
    util.log("GET", url)
    response = None
    found = []
    failed = False
    gc.collect()
    try:
        response = requests.get(url, headers={"User-Agent": "BadgerSett/1.0"})
        if response.status_code != 200:
            util.log("met office HTTP", response.status_code)
            return None            # a bad response is not an all-clear

        # Read with a cap rather than trusting the feed to stay small.
        body = ""
        stream = response.raw
        while len(body) < MAX_BYTES:
            chunk = stream.read(1024)
            if not chunk:
                break
            try:
                body += chunk.decode("utf-8")
            except Exception:
                body += "".join(chr(b) for b in chunk if b < 128)

        best = {}
        for title in _items(body):
            colour, hazard = parse_title(title)
            if not colour:
                continue
            level = COLOURS[colour]
            if level < floor:
                continue
            # Key on the hazard so a yellow upgraded to amber escalates
            # rather than appearing as a second, separate warning.
            key = hazard.lower()
            if key not in best or level > best[key]["rank"]:
                best[key] = {
                    "key": "met:%s" % key,
                    "event": "%s: %s" % (colour.capitalize(), hazard),
                    "severity": colour.capitalize(),
                    "level": level,
                    "rank": level,
                    "ends": None,
                }
        found = sorted(best.values(), key=lambda a: -a["rank"])[:max_warnings]
        util.log("met office warnings:", [a["event"] for a in found])
    except Exception as exc:
        util.log("met office failed:", exc)
        failed = True
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        gc.collect()

    # None, not [] - "the fetch failed" and "no warnings are in force"
    # must not look the same, or a dead feed reads as an all-clear.
    return None if failed else found
