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
    def __init__(self, i2c, address=0x77):
        self.i2c = i2c
        self.address = address
        self.device = None
        self.error = None
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

    def read(self, samples=4, settle=0.35):
        """Return a reading dict, or None if the sensor is unavailable.

        Early samples are discarded: the first conversion after power-up
        reports a warm, dry, low-resistance lie while the heater spins up.
        """
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

        reading = {
            "temp_c": temperature,
            "temp": util.c_to_display(temperature),
            "humidity": humidity,
            "pressure_pa": pressure,
            "pressure": util.sea_level_pressure(pressure, self._altitude(), temperature),
            "gas": gas if stable else None,
            "gas_raw": gas,
            "stable": stable,
            "dew_c": util.dew_point(temperature, humidity),
        }
        reading["dew"] = util.c_to_display(reading["dew_c"])
        util.log("BME688:", "%.1fC %.0f%% %.1fhPa gas=%s stable=%s" % (
            temperature, humidity, reading["pressure"] or 0, gas, stable))
        return reading

    @staticmethod
    def _altitude():
        import config
        return getattr(config, "ALTITUDE_M", 0)
