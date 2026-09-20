"""On-badge hardware check. Copy to the badge and run it over USB:

    mpremote connect auto run tools/selftest.py

Scans the I2C bus, exercises the BME688 and the haptic driver, and
optionally auto-calibrates the actuator and saves the result so the
main app picks it up.
"""

import time


import config
from badgersett import sensor as sensor_mod
from badgersett.haptics import Haptics, PATTERNS
from badgersett.state import State

KNOWN = {0x5A: "DRV2605L haptic", 0x76: "BME68x (alt address)",
         0x77: "BME68x", 0x51: "PCF85063A RTC (on-board)"}


def main():
    print("BadgerSett self-test\n" + "=" * 40)

    i2c = sensor_mod.make_i2c(getattr(config, "I2C_SDA", 4), getattr(config, "I2C_SCL", 5))
    found = i2c.scan()
    print("\nI2C devices on GP%d/GP%d:" % (config.I2C_SDA, config.I2C_SCL))
    if not found:
        print("  nothing found — check the Qw/ST cable is seated at both ends")
    for address in found:
        print("  0x%02X  %s" % (address, KNOWN.get(address, "unknown")))

    print("\nBME688:")
    bme = sensor_mod.Sensor(i2c, getattr(config, "BME688_ADDRESS", 0x77))
    if not bme.ok:
        print("  unavailable:", bme.error)
    else:
        for index in range(5):
            reading = bme.read(samples=1, settle=0)
            print("  %d: %.2f C  %.1f %%RH  %.1f hPa  gas %.0f ohm  heater %s" % (
                index + 1, reading["temp_c"], reading["humidity"],
                reading["pressure"], reading["gas_raw"] or 0,
                "stable" if reading["stable"] else "warming"))
            time.sleep(1)
        print("  (the gas channel needs a few minutes of running before it means much)")

    print("\nHaptics:")
    state = State()
    haptic = Haptics(i2c, getattr(config, "DRV2605_ADDRESS", 0x5A),
                     getattr(config, "HAPTIC_ACTUATOR", "LRA"), state.get("cal"))
    if not haptic.ready:
        print("  unavailable:", haptic.error)
        return

    for level in (1, 2, 3):
        print("  level %d: %s" % (level, PATTERNS[level]))
        haptic.alert(level)
        time.sleep(1.5)

    print("\n  Auto-calibrate the actuator? It takes about a second.")
    print("  Hold the badge the way you wear it — how the actuator is")
    print("  mounted changes its resonance. Type y then Enter:")
    try:
        answer = input("  > ").strip().lower()
    except Exception:
        answer = ""
    if answer.startswith("y"):
        result = haptic.calibrate()
        if result:
            state.set("cal", result)
            state.save()
            print("  saved calibration:", result)
            haptic.alert(2)
        else:
            print("  calibration failed — the app will fall back to open loop")

    haptic.standby()
    print("\ndone")


main()
