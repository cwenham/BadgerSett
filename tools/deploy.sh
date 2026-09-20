#!/usr/bin/env bash
# Copy BadgerSett onto a Badger 2040 W over USB.
#
#   ./tools/deploy.sh            # copy code + config + photo
#   ./tools/deploy.sh --run      # ...then start main.py and watch the console
#
# Needs mpremote:  pip3 install mpremote
set -euo pipefail

cd "$(dirname "$0")/.."

command -v mpremote >/dev/null || { echo "mpremote not found: pip3 install mpremote"; exit 1; }
[ -f config.py ] || { echo "No config.py. Run: cp config.example.py config.py"; exit 1; }

echo "==> Badger found:"
mpremote connect auto eval "__import__('sys').implementation" || {
  echo "Could not talk to the badge. Plug it in, and close any other serial monitor."; exit 1; }

echo "==> Copying package"
mpremote connect auto fs mkdir :badgersett 2>/dev/null || true
for f in badgersett/*.py; do
  echo "    $f"
  mpremote connect auto fs cp "$f" ":badgersett/$(basename "$f")"
done

echo "==> Copying app files"
mpremote connect auto fs cp main.py :main.py
mpremote connect auto fs cp config.py :config.py

# Pull the PHOTO filename out of config.py (bash 3.2-safe: no nested heredoc).
PHOTO=$(sed -n 's/^PHOTO[[:space:]]*=[[:space:]]*["'\'']\([^"'\'']*\)["'\''].*/\1/p' config.py | head -1)

if [ -n "$PHOTO" ] && [ -f "$PHOTO" ]; then
  echo "    $PHOTO"
  mpremote connect auto fs cp "$PHOTO" ":$PHOTO"
elif [ -n "$PHOTO" ]; then
  echo "    (config.py wants $PHOTO but it is not here — run tools/make_photo.py)"
fi

echo "==> Done."
if [ "${1:-}" = "--run" ]; then
  echo "==> Running main.py (ctrl-C to stop, ctrl-D to soft reset)"
  mpremote connect auto run main.py
else
  echo "    Reset the badge, or: mpremote connect auto run main.py"
fi
