"""BME688 temperature / humidity / pressure / gas sensor.

The Badger 2040 W firmware already contains Pimoroni's `breakout_bme68x`
C module, so there is nothing to install — it drives the BME688 as well
as the BME680.

Gas readings need a few heater cycles before they settle, so `read()`
takes several samples and reports whether the heater reached its
target. Treat an unstable reading as advisory only.
"""

import time

import machine

from . import util

try:
    from breakout_bme68x import BreakoutBME68X, STATUS_HEATER_STABLE
    AVAILABLE = True
except ImportError:      # firmware without the module
    AVAILABLE = False
    STATUS_HEATER_STABLE = 0x10


def make_i2c(sda, scl, freq=100000):
    """I2C0 on the Qw/ST connector (GP4 = SDA, GP5 = SCL on Badger 2040 W)."""
    return machine.I2C(0, sda=machine.Pin(sda), scl=machine.Pin(scl), freq=freq)


class Sensor:
    def __init__(self, i2c, address=0x77, altitude=None):
        self.i2c = i2c
        self.address = address
        # Sea-level pressure depends on where you are, so this follows the
        # active WiFi network's location rather than a fixed config value.
        if altitude is None:
            import config
            altitude = getattr(config, "ALTITUDE_M", 0)
        self.altitude = altitude
        self.device = None
        self.error = None
        # Conditioning is measured from the start of the current unbroken
        # run of samples, not from when this object was created. The heater
        # only fires during a measurement, so a long gap between reads lets
        # it go cold even though the object - and the badge - stayed alive.
        self.heater_started = time.ticks_ms()
        self._last_read = None
        if not AVAILABLE:
            self.error = "no breakout_bme68x in firmware"
            return
        try:
            present = i2c.scan()
        except Exception as exc:
            self.error = "I2C bus error: %s" % exc
            return
        if address not in present:
            self.error = "no BME688 at 0x%02X" % address
            util.log("I2C devices found:", [hex(a) for a in present])
            return
        try:
            self.device = BreakoutBME68X(i2c, address)
            # Oversample temperature and humidity a little harder than the
            # defaults; we read infrequently so the extra milliseconds are free.
            self.device.configure(filter=2, standby_time=0,
                                  os_pressure=5, os_temp=2, os_humidity=2)
        except Exception as exc:
            self.device = None
            self.error = "BME688 init failed: %s" % exc

    @property
    def ok(self):
        return self.device is not None

    def warm_seconds(self):
        """How long the heater has been cycling without a break."""
        return time.ticks_diff(time.ticks_ms(), self.heater_started) / 1000.0

    def _note_sample(self, max_gap):
        """Restart the conditioning clock if the heater has been idle too long.

        This is what makes the warm-up gate honest. An earlier version timed
        warm-up from object creation, so on USB - where the loop reuses one
        Sensor across a 30-minute wait with no reads - it reported half an
        hour of warm-up for a heater that had been cold the whole time, and
        trusted readings it should not have.
        """
        now = time.ticks_ms()
        if self._last_read is not None and \
                time.ticks_diff(now, self._last_read) > max_gap * 1000:
            util.log("gas heater idle %.0fs; conditioning restarts" %
                     (time.ticks_diff(now, self._last_read) / 1000.0))
            self.heater_started = now
        self._last_read = now

    def read(self, samples=4, settle=0.35, warmup=None, gas_enabled=True):
        """Return a reading dict, or None if the sensor is unavailable.

        Early samples are discarded: the first conversion after power-up
        reports a warm, dry, low-resistance lie while the heater spins up.

        The gas channel needs far longer than that. From cold the BME688
        reads implausibly high, collapses, then climbs for many minutes -
        still rising 3-4% per 15s after three minutes in testing. So
        `gas` is only populated once the heater has been running for
        `warmup` seconds; before that it is None and nothing downstream
        (baseline, alerts, the air-quality verdict) will use it.
        `gas_raw` is always present for display.
        """
        import config
        if warmup is None:
            warmup = getattr(config, "GAS_WARMUP_S", 300)
        self._note_sample(getattr(config, "GAS_MAX_GAP_S", 30))
        if not self.ok:
            return None

        last = None
        for index in range(max(samples, 1)):
            try:
                last = self.device.read()
            except Exception as exc:
                util.log("BME688 read failed:", exc)
                return None
            if index < samples - 1:
                time.sleep(settle)

        temperature, pressure, humidity, gas, status = last[0], last[1], last[2], last[3], last[4]
        stable = bool(status & STATUS_HEATER_STABLE)
        warm = self.warm_seconds()
        if not gas_enabled:
            # On battery the badge sleeps between refreshes, so the heater
            # never accumulates enough continuous running for the reading
            # to mean anything. Do not pretend otherwise.
            trusted, reason = False, "usb only"
        elif not stable:
            trusted, reason = False, "unstable"
        elif warm < warmup:
            trusted, reason = False, "warming %ds" % round(warm)
        else:
            trusted, reason = True, "ok"

        reading = {
            "temp_c": temperature,
            "temp": util.c_to_display(temperature),
            "humidity": humidity,
            "pressure_pa": pressure,
            "pressure": util.sea_level_pressure(pressure, self.altitude, temperature),
            "gas": gas if trusted else None,
            "gas_raw": gas,
            "stable": stable,
            "gas_warm_s": warm,
            "gas_trusted": trusted,
            "gas_reason": reason,
            "dew_c": util.dew_point(temperature, humidity),
        }
        reading["dew"] = util.c_to_display(reading["dew_c"])
        util.log("BME688:", "%.1fC %.0f%% %.1fhPa gas=%s (%s)" % (
            temperature, humidity, reading["pressure"] or 0, gas, reason))
        return reading

