"""Timekeeping.

The badge has two clocks: the RP2040's own RTC, which loses time whenever
power is cut, and the PCF85063A, which is battery-backed and drives the
wake alarm. Both start life unset, reading 2000-01-01.

Time is taken from NTP on every successful connection, then shifted to
local time using the UTC offset Open-Meteo reports for your coordinates
- which handles BST, and any other DST rule, without a timezone database
on the badge.

Nothing here may assume the clock is valid: `minutes()` returns None when
it has not been set, and callers treat that as "refresh now", so a lost
clock heals itself on the next wake instead of wedging.
"""

import time

import machine

from . import util

# Anything earlier than this means the RTC has never been set. The
# MicroPython epoch on rp2 is 2000-01-01, and the firmware itself dates
# from 2024, so no genuine reading can predate it.
VALID_YEAR = 2024

NTP_HOSTS = ("pool.ntp.org", "time.cloudflare.com")


def is_set():
    try:
        return machine.RTC().datetime()[0] >= VALID_YEAR
    except Exception:
        return False


def sync_ntp(timeout=5):
    """Set the RP2040 RTC to UTC from NTP. Returns True on success."""
    try:
        import ntptime
    except ImportError:
        util.log("ntptime not available")
        return False

    original_host = getattr(ntptime, "host", None)
    for host in NTP_HOSTS:
        try:
            ntptime.host = host
            try:
                ntptime.timeout = timeout
            except Exception:
                pass          # older ports have no timeout attribute
            ntptime.settime()
            util.log("NTP ok from %s: %s UTC" % (host, machine.RTC().datetime()))
            return True
        except Exception as exc:
            util.log("NTP failed from %s: %s" % (host, exc))
        finally:
            if original_host is not None:
                ntptime.host = original_host
    return False


def apply_utc_offset(seconds):
    """Shift the RTC from UTC to local time.

    Open-Meteo returns utc_offset_seconds for the requested coordinates,
    already accounting for daylight saving on the date in question.
    """
    if not seconds:
        return False
    try:
        local = time.localtime(time.time() + int(seconds))
        # localtime gives (y, m, d, h, m, s, weekday, yearday);
        # RTC.datetime wants (y, m, d, weekday, h, m, s, subsecond).
        machine.RTC().datetime((local[0], local[1], local[2], local[6],
                                local[3], local[4], local[5], 0))
        util.log("local time set: %s (UTC%+d)" % (hhmm(), int(seconds) // 3600))
        return True
    except Exception as exc:
        util.log("could not apply UTC offset:", exc)
        return False


def persist():
    """Copy the RP2040 RTC into the battery-backed PCF85063A."""
    try:
        import badger2040
        badger2040.pico_rtc_to_pcf()
        return True
    except Exception as exc:
        util.log("could not write the hardware RTC:", exc)
        return False


def restore():
    """Load the battery-backed clock into the RP2040 RTC after a power cut."""
    try:
        import badger2040
        badger2040.pcf_to_pico_rtc()
    except Exception as exc:
        util.log("could not read the hardware RTC:", exc)
    return is_set()


def minutes():
    """Minutes since the epoch, or None when the clock has never been set."""
    if not is_set():
        return None
    try:
        return int(time.time() // 60)
    except Exception:
        return None


def hhmm():
    try:
        stamp = machine.RTC().datetime()
        return "%02d:%02d" % (stamp[4], stamp[5])
    except Exception:
        return "--:--"


def hour():
    try:
        return machine.RTC().datetime()[4] if is_set() else None
    except Exception:
        return None


def is_stale(last_refresh, refresh_minutes):
    """True when a refresh is due.

    Unknown state always counts as due: no clock, no record of a previous
    refresh, or a clock that has jumped backwards (which happens when the
    RTC resets) all mean "fetch now" rather than "wait indefinitely".
    """
    if last_refresh is None:
        return True
    now = minutes()
    if now is None:
        return True
    elapsed = now - last_refresh
    if elapsed < 0:
        util.log("clock went backwards; refreshing")
        return True
    return elapsed >= refresh_minutes
