#!/usr/bin/env bash
# Wrap the built Omarchy-Bible.app into a distributable .dmg.
#
#   ./macos/make_dmg.sh [path/to/Omarchy-Bible.app]
#
# Produces dist/Omarchy-Bible-<version>.dmg (drag-to-Applications style).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$(dirname "$0")"
APP="${1:-$ROOT/dist/Omarchy-Bible.app}"
VERSION="${VERSION:-1.0.0}"

if [ ! -d "$APP" ]; then
    echo "ERROR: .app not found at $APP — run ./macos/build_app.sh first."
    exit 1
fi

OUT="$ROOT/dist/Omarchy-Bible-${VERSION}.dmg"
mkdir -p "$ROOT/dist"

if command -v create-dmg >/dev/null 2>&1; then
    echo "==> Using create-dmg"
    create-dmg \
        --volname "Omarchy-Bible" \
        --window-pos 200 120 \
        --window-size 660 400 \
        --icon-size 128 \
        --app-drop-link 480 220 \
        --icon "Omarchy-Bible.app" 140 220 \
        --hide-extension "Omarchy-Bible.app" \
        "$OUT" "$APP"
else
    echo "==> Using hdiutil (Drag-to-Applications layout)"
    STAGE="$(mktemp -d)"
    cp -R "$APP" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    hdiutil create -volname "Omarchy-Bible" -srcfolder "$STAGE" \
        -ov -format UDZO "$OUT" >/dev/null
    rm -rf "$STAGE"
fi

echo "==> Created: $OUT"
