"""BBC News headlines from their published RSS feeds.

These are the BBC's own feeds, not scraped pages. Terms of use are at
bbc.co.uk/usingthebbc/terms-of-use (see the feed's own <copyright>
element); they are fine for personal use with attribution, which the
news view renders.

A full feed is 15-30KB, which the badge cannot comfortably hold, but
item titles start about 1KB in and we only want a handful. So this
streams the response and stops reading as soon as it has enough
headlines - typically after ~5KB of a 28KB feed.
"""

import gc

try:
    import urequests as requests
except ImportError:
    import requests

from . import util

FEEDS = {
    "top": "https://feeds.bbci.co.uk/news/rss.xml",
    "uk": "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "sussex": "https://feeds.bbci.co.uk/news/england/sussex/rss.xml",
}

CHUNK = 512
MAX_BYTES = 40000          # hard stop, in case the feed has no items
MAX_TITLE = 110            # longer than two lines can show anyway


def _clean(title):
    """Strip CDATA wrapping and XML entities from a title."""
    title = title.strip()
    if title.startswith("<![CDATA[") and title.endswith("]]>"):
        title = title[9:-3]
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
                         ("&pound;", "£"), ("&nbsp;", " ")):
        title = title.replace(entity, char)
    title = " ".join(title.split())      # collapse newlines and runs of spaces
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE].rsplit(" ", 1)[0] + ".."
    return title.strip()


def fetch(feed="top", count=4):
    """Return up to `count` headline strings, newest first.

    `feed` is a key from FEEDS or a full URL, so any BBC feed works.
    """
    url = FEEDS.get(feed, feed)
    if not url.startswith("http"):
        util.log("bad news feed %r; using top stories" % feed)
        url = FEEDS["top"]

    util.log("GET", url)
    response = None
    headlines = []
    gc.collect()
    try:
        response = requests.get(url, headers={"User-Agent": "BadgerSett/1.0"})
        if response.status_code != 200:
            util.log("news HTTP", response.status_code)
            return []

        stream = response.raw
        window = ""
        read = 0
        in_item = False
        while read < MAX_BYTES and len(headlines) < count:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            read += len(chunk)
            try:
                window += chunk.decode("utf-8")
            except Exception:
                window += "".join(chr(b) for b in chunk if b < 128)

            # Walk whatever is currently visible in the window.
            while len(headlines) < count:
                if not in_item:
                    # The channel and its <image> each carry a decoy <title>
                    # before the first item, so only look inside <item>.
                    start = window.find("<item>")
                    if start < 0:
                        break
                    window = window[start + 6:]
                    in_item = True
                open_tag = window.find("<title>")
                if open_tag < 0:
                    break
                close_tag = window.find("</title>", open_tag)
                if close_tag < 0:
                    break                     # title split across chunks
                headlines.append(_clean(window[open_tag + 7:close_tag]))
                window = window[close_tag + 8:]
                in_item = False

            # Bound the window, but never discard a title we are still
            # assembling: with small chunks the opening <title> can be many
            # reads behind the closing one, and trimming blindly to the tail
            # would throw the headline's first half away.
            if len(window) > CHUNK * 3:
                pending = window.find("<title>")
                if pending >= 0 and window.find("</title>", pending) < 0:
                    window = window[pending:]
                else:
                    window = window[-16:]      # enough to hold a split <item>

        util.log("news: %d headlines in %d bytes" % (len(headlines), read))
    except Exception as exc:
        util.log("news failed:", exc)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        gc.collect()

    return [h for h in headlines if h][:count]
