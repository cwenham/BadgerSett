"""BadgerSett — entry point.

Badger OS runs main.py on boot. Everything else lives in the badgersett
package; this file stays thin so a failure here is easy to spot over USB.
"""

import gc

gc.collect()

try:
    import config           # noqa: F401  (imported for the friendly error below)
except ImportError:
    raise SystemExit(
        "BadgerSett: no config.py on the badge.\n"
        "Copy config.example.py to config.py, fill it in, and upload it."
    )

from badgersett import app

app.run()
