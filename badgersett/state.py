"""Persisted state that survives deep sleep.

Lives in flash as JSON. We only write when something actually changed,
to be kind to the badge's flash.
"""

import json

from . import util

PATH = "state.json"

DEFAULTS = {
    "alerts": {},          # alert key -> level last notified at
    "gas_baseline": None,  # rolling median-ish baseline, ohms
    "gas_n": 0,            # how many samples are folded into the baseline
    "gas_hold": 0,         # consecutive samples ignored because an event is live
    "pressure": [],        # [(minutes_since_epochish, hPa), ...] most recent last
    "muted": False,
    "view": 0,
    "scan_page": 0,        # which page of the scanner list is showing
    "scan_mode": "list",   # "list" or "radar"
    "weather": None,       # last good forecast, so a button wake can still draw
    "indoor": None,        # last good sensor reading
    "news": [],            # last good headlines, so button C works offline
    "location": None,      # the network we last joined, and where it is
    "updated": None,       # local HH:MM of the last successful network refresh
    "last_refresh": None,  # minutes-since-epoch of that refresh, for staleness
    "cal": None,           # DRV2605L autocalibration results [comp, bemf, fb]
}


class State:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._dirty = False
        self.load()

    def load(self):
        try:
            with open(PATH) as handle:
                stored = json.load(handle)
            for key, value in stored.items():
                if key in DEFAULTS:
                    self._data[key] = value
            util.log("state loaded")
        except Exception as exc:
            util.log("state: starting fresh (%s)" % exc)

    def save(self):
        if not self._dirty:
            return
        try:
            with open(PATH, "w") as handle:
                json.dump(self._data, handle)
            self._dirty = False
            util.log("state saved")
        except Exception as exc:
            util.log("state save failed:", exc)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        if self._data.get(key) != value:
            self._data[key] = value
            self._dirty = True

    # -- gas baseline ------------------------------------------------------
    def update_gas_baseline(self, resistance, max_samples, hold_limit=48):
        """Learn what "normal" air smells like, without learning the smoke.

        A VOC event drops resistance hard. If we folded those samples into
        the baseline the baseline would chase them down and the alert would
        quietly cancel itself while the air was still bad — so during an
        event we stop learning entirely. If the low reading persists for
        `hold_limit` samples (a day at the default refresh) we accept it as
        the new normal, otherwise a genuinely changed environment would
        leave the badge stuck in permanent alarm.
        """
        if resistance is None or resistance <= 0:
            return self.get("gas_baseline")

        baseline = self.get("gas_baseline")
        count = self.get("gas_n", 0)
        if baseline is None:
            self.set("gas_baseline", resistance)
            self.set("gas_n", 1)
            self.set("gas_hold", 0)
            return resistance

        hold = self.get("gas_hold", 0)
        if resistance < baseline * 0.8:
            hold += 1
            self.set("gas_hold", hold)
            if hold < hold_limit:
                return baseline         # event in progress: learn nothing
            alpha = 0.05                # persistent: drift toward it slowly
        else:
            self.set("gas_hold", 0)
            alpha = 1.0 / min(max(count, 1), max_samples)
            if resistance < baseline:
                alpha *= 0.5            # ordinary dips still move it gently

        baseline = baseline + alpha * (resistance - baseline)
        self.set("gas_baseline", baseline)
        self.set("gas_n", count + 1)
        return baseline

    # -- pressure history --------------------------------------------------
    def push_pressure(self, hpa, stamp, keep=12, min_interval=15):
        """Record a pressure sample, at most one per `min_interval` minutes.

        Spacing samples out keeps the history covering the same span no
        matter how often the loop runs. Without it, a badge that stays
        awake and loops every few minutes would fill all 12 slots within
        the hour and never have the 3 hours of history the trend needs.
        """
        if hpa is None or stamp is None:
            return          # no clock yet: a trend needs real timestamps
        history = list(self.get("pressure") or [])
        if history and stamp - history[-1][0] < min_interval:
            return
        history.append([stamp, round(hpa, 1)])
        if len(history) > keep:
            history = history[-keep:]
        self.set("pressure", history)

    def pressure_trend(self, window_minutes=180):
        """hPa change over the window; negative means falling."""
        history = self.get("pressure") or []
        if len(history) < 2:
            return None
        newest_stamp, newest = history[-1]
        for stamp, value in reversed(history[:-1]):
            if newest_stamp - stamp >= window_minutes:
                return newest - value
        oldest_stamp, oldest = history[0]
        if newest_stamp - oldest_stamp >= window_minutes // 2:
            return newest - oldest
        return None
