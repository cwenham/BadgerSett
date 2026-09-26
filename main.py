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

# Claim the TLS working area before the package imports fragment the heap.
# MicroPython's GC does not compact, so a block this size can only be had
# while the heap is still whole; app.py hands it back around each fetch.
# Without it, HTTPS (Met Office warnings, BBC headlines) fails with ENOMEM
# even with 100KB free, because none of it is contiguous.
# Handed over in a list so this module keeps no reference of its own:
# app.run() takes it out, and freeing it there really frees it.
try:
    _handover = [bytearray(int(getattr(config, "TLS_RESERVE_KB", 48)) * 1024)]
except MemoryError:
    _handover = []

from badgersett import app

app.run(_handover)
