#!/bin/bash
# Omarchy-Bible launcher.
# Automatically creates & provisions the project virtualenv on first run,
# then runs the app with THAT venv's Python (so ebooklib is always available).

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    echo "Setting up virtualenv (first run)..."
    python3 -m venv --system-site-packages "$VENV"
fi

# Ensure ebooklib is present (installs only if missing)
if ! "$PY" -c "import ebooklib" 2>/dev/null; then
    echo "Installing ebooklib..."
    "$VENV/bin/pip" install --quiet ebooklib
fi

exec "$PY" "$DIR/main.py" "$@"
