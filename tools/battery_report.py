#!/usr/bin/env python3
"""Summarise a runtime.log pulled off the badge.

    mpremote fs cp :runtime.log .
    python3 tools/battery_report.py runtime.log --capacity 2000

Reports how long each power-on session lasted, the voltage it started
and ended at, and - given the battery's capacity - the average current
that implies, which is the number to compare against the estimate.
"""

import argparse
import re
import sys

LINE = re.compile(r"^(\S+ \S+)\s+(\w+)\s+(.*)$")


def parse(path):
    sessions, current = [], None
    for raw in open(path):
        m = LINE.match(raw.strip())
        if not m:
            continue
        stamp, event, rest = m.groups()
        fields = dict(kv.split("=", 1) for kv in rest.split() if "=" in kv)
        up = int(fields.get("up", "0m").rstrip("m") or 0)
        try:
            volts = float(fields.get("vsys", "?"))
        except ValueError:
            volts = None
        row = {"stamp": stamp, "event": event, "up": up, "vsys": volts,
               "mode": fields.get("mode", "?"), "fields": fields}
        # A boot line, or uptime going backwards, starts a new session.
        if event == "boot" or current is None or up < current[-1]["up"]:
            current = []
            sessions.append(current)
        current.append(row)
    return sessions


def bar(v, lo=3.0, hi=4.3, width=30):
    if v is None:
        return ""
    filled = int(max(0.0, min(1.0, (v - lo) / (hi - lo))) * width)
    return "#" * filled + "." * (width - filled)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--capacity", type=float, default=None,
                    help="battery capacity in mAh, to estimate average current")
    args = ap.parse_args()

    sessions = parse(args.log)
    if not sessions:
        sys.exit("no log lines found in %s" % args.log)

    print("%d power-on session(s)\n" % len(sessions))
    for i, rows in enumerate(sessions, 1):
        first, last = rows[0], rows[-1]
        hours = last["up"] / 60.0
        volts = [r["vsys"] for r in rows if r["vsys"] is not None]
        usb = [r for r in rows if r["vsys"] and r["vsys"] > 4.6]
        print("session %d  mode=%s" % (i, first["mode"]))
        print("  from      %s" % first["stamp"])
        print("  last seen %s" % last["stamp"])
        print("  ran for   %.1f hours (%d min)" % (hours, last["up"]))
        if volts:
            print("  voltage   %.3f V -> %.3f V" % (volts[0], volts[-1]))
        # The experiment is the stretch on battery: VSYS sits near 4.8 V on
        # USB, so the first reading below 4.6 V marks the unplug.
        on_batt = [r for r in rows if r["vsys"] is not None and r["vsys"] < 4.6]
        if on_batt:
            batt_h = (last["up"] - on_batt[0]["up"]) / 60.0
            print("  battery   from %s, %.1f hours (%.3f V -> %.3f V)" %
                  (on_batt[0]["stamp"], batt_h, on_batt[0]["vsys"], on_batt[-1]["vsys"]))
            if args.capacity and batt_h > 0:
                print("  implies   ~%.1f mA average from %.0f mAh" %
                      (args.capacity / batt_h, args.capacity))
        elif usb:
            print("  NOTE: every reading above 4.6 V - this ran on USB, not battery")
        if any(r["event"] == "lowbat" for r in rows):
            print("  ended     at the low-battery cutoff (a clean finish)")
        elif on_batt:
            print("  ended     without a lowbat line - supply collapsed first,"
                  " or the log was pulled early")
        step = max(1, len(rows) // 16)
        print("  curve")
        for r in rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else []):
            if r["vsys"] is not None:
                print("    %s  %5.1fh  %.3fV  %s" % (r["stamp"][-5:], r["up"] / 60.0,
                                                   r["vsys"], bar(r["vsys"])))
        print()


if __name__ == "__main__":
    main()
