"""DRV2605L haptic driver.

A direct register-level driver for the TI DRV2605L, which is what sits
behind Pimoroni's haptic buzzer breakout. Effect numbers index the
chip's built-in TI ROM waveform library, so patterns play from ROM and
cost us nothing but an I2C write and a GO bit.

Actuator type matters: the Pimoroni board ships with a linear resonant
actuator (LRA). Set HAPTIC_ACTUATOR = "ERM" in config.py if you fitted
an eccentric-rotating-mass motor instead, or it will feel weak and buzzy.
"""

import time

from . import util

_STATUS = 0x00
_MODE = 0x01
_RTP = 0x02
_LIBRARY = 0x03
_WAVESEQ = 0x04          # 0x04..0x0B, eight slots, zero-terminated
_GO = 0x0C
_ODT = 0x0D
_SPT = 0x0E
_SNT = 0x0F
_BRT = 0x10
_RATED_VOLTAGE = 0x16
_OD_CLAMP = 0x17
_CAL_COMP = 0x18
_CAL_BEMF = 0x19
_FEEDBACK = 0x1A
_CONTROL1 = 0x1B
_CONTROL2 = 0x1C
_CONTROL3 = 0x1D

_MODE_INTERNAL = 0x00
_MODE_RTP = 0x05
_MODE_AUTOCAL = 0x07
_MODE_STANDBY = 0x40
_MODE_RESET = 0x80

# A few effects from the TI ROM library, chosen because they read clearly
# through a badge lanyard rather than because they are subtle.
CLICK = 1            # Strong Click 100%
SHARP = 4            # Sharp Click 100%
BUMP = 7             # Soft Bump 100%
DOUBLE = 10          # Double Click 100%
TRIPLE = 12          # Triple Click 100%
BUZZ = 14            # Strong Buzz 100%
ALERT_750 = 15       # 750 ms Alert 100%
ALERT_1000 = 16      # 1000 ms Alert 100%

# Escalating patterns, indexed by alert level.
PATTERNS = {
    1: (BUMP,),                              # notice
    2: (DOUBLE, DOUBLE),                     # warning
    3: (ALERT_750, BUZZ, ALERT_750, BUZZ),   # severe — hard to miss
}
TICK = (SHARP,)


class Haptics:
    def __init__(self, i2c, address=0x5A, actuator="LRA", calibration=None):
        self.i2c = i2c
        self.address = address
        self.actuator = (actuator or "LRA").upper()
        self.error = None
        self.ready = False
        try:
            if address not in i2c.scan():
                self.error = "no DRV2605L at 0x%02X" % address
                return
            self._init(calibration)
            self.ready = True
        except Exception as exc:
            self.error = "DRV2605L init failed: %s" % exc
            util.log(self.error)

    # -- register helpers --------------------------------------------------
    def _write(self, register, value):
        self.i2c.writeto_mem(self.address, register, bytes([value & 0xFF]))

    def _read(self, register):
        return self.i2c.readfrom_mem(self.address, register, 1)[0]

    # -- setup -------------------------------------------------------------
    def _init(self, calibration):
        self._write(_MODE, _MODE_RESET)
        deadline = time.ticks_add(time.ticks_ms(), 500)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(5)
            try:
                if not self._read(_MODE) & _MODE_RESET:
                    break
            except Exception:
                pass          # the chip NAKs while it is resetting

        self._write(_MODE, _MODE_INTERNAL)      # out of standby, internal trigger
        self._write(_RTP, 0x00)
        for offset in range(8):                 # clear the waveform sequencer
            self._write(_WAVESEQ + offset, 0x00)
        for register in (_ODT, _SPT, _SNT, _BRT):
            self._write(register, 0x00)

        feedback = self._read(_FEEDBACK)
        control3 = self._read(_CONTROL3)

        if calibration:
            # Reuse a stored auto-calibration: much better braking, and it
            # saves the ~1s the calibration sweep would cost on every boot.
            comp, bemf, stored_feedback = calibration
            self._write(_CAL_COMP, comp)
            self._write(_CAL_BEMF, bemf)
            self._write(_FEEDBACK, stored_feedback)
            if self.actuator == "LRA":
                self._write(_CONTROL3, control3 & ~0x01)   # closed loop
            else:
                self._write(_CONTROL3, control3 & ~0x20)
        elif self.actuator == "LRA":
            self._write(_FEEDBACK, feedback | 0x80)        # N_ERM_LRA = LRA
            self._write(_CONTROL3, control3 | 0x01)        # LRA open loop
            self._write(_RATED_VOLTAGE, 0x3E)              # ~2.0 V RMS
            self._write(_OD_CLAMP, 0x8C)
        else:
            self._write(_FEEDBACK, feedback & 0x7F)        # ERM
            self._write(_CONTROL3, control3 | 0x20)        # ERM open loop
            self._write(_RATED_VOLTAGE, 0x50)
            self._write(_OD_CLAMP, 0x89)

        self._write(_LIBRARY, 6 if self.actuator == "LRA" else 1)
        util.log("DRV2605L ready (%s%s)" % (
            self.actuator, ", calibrated" if calibration else ""))

    # -- playback ----------------------------------------------------------
    def play(self, effects, wait=True, timeout_ms=4000):
        """Queue up to 8 ROM effects and fire them."""
        if not self.ready:
            return False
        try:
            effects = tuple(effects)[:8]
            for slot in range(8):
                self._write(_WAVESEQ + slot, effects[slot] if slot < len(effects) else 0)
            self._write(_GO, 1)
            if wait:
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                    if not self._read(_GO) & 1:
                        break
                    time.sleep_ms(20)
            return True
        except Exception as exc:
            util.log("haptic play failed:", exc)
            return False

    def buzz(self, level=0x7F, ms=200):
        """Continuous real-time buzz, for when no ROM effect fits."""
        if not self.ready:
            return False
        try:
            self._write(_MODE, _MODE_RTP)
            self._write(_RTP, level)
            time.sleep_ms(ms)
            self._write(_RTP, 0x00)
            self._write(_MODE, _MODE_INTERNAL)
            return True
        except Exception as exc:
            util.log("haptic buzz failed:", exc)
            return False

    def alert(self, level):
        return self.play(PATTERNS.get(level, PATTERNS[1]))

    def tick(self):
        return self.play(TICK, wait=False)

    def standby(self):
        """Park the driver in standby — it idles at ~2uA there."""
        if not self.ready:
            return
        try:
            self._write(_MODE, _MODE_STANDBY)
        except Exception:
            pass

    # -- calibration -------------------------------------------------------
    def calibrate(self):
        """Run the chip's auto-calibration sweep against your actuator.

        Returns [comp, bemf, feedback] to stash in state.json, or None.
        Run it once via tools/selftest.py with the actuator mounted the way
        you will actually wear it — loading changes the resonant frequency.
        """
        if not self.ready:
            return None
        try:
            feedback = self._read(_FEEDBACK)
            if self.actuator == "LRA":
                self._write(_FEEDBACK, feedback | 0x80)
                self._write(_RATED_VOLTAGE, 0x3E)
                self._write(_OD_CLAMP, 0x8C)
                self._write(_CONTROL1, 0x93)
                self._write(_CONTROL2, 0xF5)
                self._write(_CONTROL3, 0x80)
            else:
                self._write(_FEEDBACK, feedback & 0x7F)
                self._write(_RATED_VOLTAGE, 0x50)
                self._write(_OD_CLAMP, 0x89)

            self._write(_MODE, _MODE_AUTOCAL)
            self._write(_GO, 1)
            deadline = time.ticks_add(time.ticks_ms(), 3000)
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                if not self._read(_GO) & 1:
                    break
                time.sleep_ms(50)
            else:
                util.log("autocal timed out")
                return None

            if self._read(_STATUS) & 0x08:      # DIAG_RESULT: 1 means failed
                util.log("autocal failed — check the actuator is connected")
                return None

            result = [self._read(_CAL_COMP), self._read(_CAL_BEMF), self._read(_FEEDBACK)]
            util.log("autocal ok:", result)
            self._write(_MODE, _MODE_INTERNAL)
            return result
        except Exception as exc:
            util.log("autocal error:", exc)
            return None
