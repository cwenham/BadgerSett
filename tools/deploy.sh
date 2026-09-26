#!/usr/bin/env bash
# Copy BadgerSett onto a Badger 2040 W over USB.
#
#   ./tools/deploy.sh            # copy code + config + photo
#   ./tools/deploy.sh --run      # ...then start main.py and watch the console
#
# Needs mpremote:  pip3 install mpremote
# If more than one board is plugged in, name the port:
#   BADGER_PORT=/dev/cu.usbmodem142401 ./tools/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# pip3 installs mpremote as a script, but --user installs often land
# outside PATH, so fall back to the module.
if command -v mpremote >/dev/null; then
  MP="mpremote"
elif python3 -c "import mpremote" 2>/dev/null; then
  MP="python3 -m mpremote"
else
  echo "mpremote not found: pip3 install mpremote"; exit 1
fi
PORT="${BADGER_PORT:-auto}"
mpr() { $MP connect "$PORT" "$@"; }

[ -f config.py ] || { echo "No config.py. Run: cp config.example.py config.py"; exit 1; }

echo "==> Badger found:"
mpr eval "__import__('sys').implementation" || {
  echo "Could not talk to the badge. Plug it in, and close any other serial monitor."; exit 1; }

echo "==> Copying package"
mpr fs mkdir :badgersett 2>/dev/null || true
for f in badgersett/*.py; do
  echo "    $f"
  mpr fs cp "$f" ":badgersett/$(basename "$f")"
done

echo "==> Copying app files"
mpr fs cp main.py :main.py
mpr fs cp config.py :config.py

# Pull the PHOTO filename out of config.py (bash 3.2-safe: no nested heredoc).
PHOTO=$(sed -n 's/^PHOTO[[:space:]]*=[[:space:]]*["'\'']\([^"'\'']*\)["'\''].*/\1/p' config.py | head -1)

if [ -n "$PHOTO" ] && [ -f "$PHOTO" ]; then
  echo "    $PHOTO"
  mpr fs cp "$PHOTO" ":$PHOTO"
elif [ -n "$PHOTO" ]; then
  echo "    (config.py wants $PHOTO but it is not here — run tools/make_photo.py)"
fi

echo "==> Done."
if [ "${1:-}" = "--run" ]; then
  echo "==> Running main.py (ctrl-C to stop, ctrl-D to soft reset)"
  mpr run main.py
else
  echo "    Reset the badge, or: $MP connect $PORT run main.py"
fi
