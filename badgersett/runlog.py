"""Runtime log, for power experiments.

Appends a line to runtime.log on each boot and then every few minutes:

    2026-09-22 14:10 beat  up=125m vsys=3.912 mode=awake gas=ok wakes=redraw:2

`wakes` counts why the awake loop ended since the previous heartbeat. A
healthy awake badge shows about two timed redraws per ten minutes; a
large "alert" or "refresh" count means something is waking it that
should not be, and costing battery while it does.

`up` is time since this boot and `vsys` the supply voltage, so the last
line written before the battery gives out records both how long it ran
and the voltage it fell to. Pull it off with:

    mpremote fs cp :runtime.log .
    python3 tools/battery_report.py runtime.log

The file is capped: past MAX_BYTES it keeps its newer half. Every line
carries its own uptime, so the total runtime survives trimming even when
the original boot line does not.
"""

import time

from . import clock, power, util

PATH = "runtime.log"
MAX_BYTES = 96000          # ~9 days of 10-minute beats; flash has room


class RunLog:
    def __init__(self, enabled=False, every_minutes=10, mode=""):
        self.enabled = enabled
        self.every_ms = max(1, int(every_minutes)) * 60000
        self.mode = mode
        self._up_ms = 0
        self._tick = time.ticks_ms()
        self._last_beat = None

    def _advance(self):
        # Accumulate in small steps: ticks_ms() wraps after ~12 days, and a
        # battery run could plausibly outlast that.
        now = time.ticks_ms()
        self._up_ms += time.ticks_diff(now, self._tick)
        self._tick = now
        return now

    def _stamp(self):
        try:
            import machine
            d = machine.RTC().datetime()
            if clock.is_set():
                return "%04d-%02d-%02d %02d:%02d" % (d[0], d[1], d[2], d[4], d[5])
        except Exception:
            pass
        return "----------  --:--"     # clock not set yet

    def _trim(self):
        try:
            import os
            if os.stat(PATH)[6] <= MAX_BYTES:
                return
            with open(PATH) as handle:
                lines = handle.readlines()
            with open(PATH, "w") as handle:
                for line in lines[len(lines) // 2:]:
                    handle.write(line)
        except OSError:
            pass                       # no file yet

    def write(self, event, **extra):
        if not self.enabled:
            return
        self._advance()
        volts = power.vsys()
        fields = ["up=%dm" % (self._up_ms // 60000),
                  "vsys=%s" % ("%.3f" % volts if volts is not None else "?"),
                  "mode=%s" % self.mode]
        for key in sorted(extra):
            fields.append("%s=%s" % (key, extra[key]))
        line = "%s %-5s %s\n" % (self._stamp(), event, " ".join(fields))
        try:
            self._trim()
            with open(PATH, "a") as handle:
                handle.write(line)
        except Exception as exc:
            util.log("runtime log write failed:", exc)

    def heartbeat_due(self):
        now = self._advance()
        return self._last_beat is None or \
            time.ticks_diff(now, self._last_beat) >= self.every_ms

    def maybe_beat(self, **extra):
        """Write a heartbeat if one is due. Returns True if it wrote."""
        if self.enabled and self.heartbeat_due():
            self._last_beat = time.ticks_ms()
            self.write("beat", **extra)
            return True
        return False
