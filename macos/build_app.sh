#!/usr/bin/env bash
# Build OmaBible.app for macOS.
#
#   ./macos/build_app.sh
#
# What it does:
#   1. Installs Homebrew deps if missing (GTK3, WebKitGTK, GI, Python).
#   2. Creates/updates a build venv and installs Python deps.
#   3. Generates an .icns icon from org.omabible.svg (best effort).
#   4. Runs PyInstaller with macos/Spec to produce OmaBible.app.
#
# The resulting .app can be wrapped into a .dmg or .pkg with the helpers
# in this directory (make_dmg.sh / make_pkg.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "==> OmaBible macOS build (root: $ROOT)"

# ----------------------- 1. Homebrew dependencies -----------------------
if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: Homebrew not found. Install it from https://brew.sh first."
    exit 1
fi

need=""
for pkg in gtk4 gtk3 webkit2gtk gobject-introspection python@3.11; do
    brew list "$pkg" >/dev/null 2>&1 || need="$need $pkg"
done
if [ -n "$need" ]; then
    echo "==> Installing Homebrew deps:$need"
    brew install $need
fi

# Ensure the Homebrew python is used (so GI/WebKit are discoverable).
if [ -x "/opt/homebrew/bin/python3" ]; then
    PY="/opt/homebrew/bin/python3"
elif [ -x "/usr/local/bin/python3" ]; then
    PY="/usr/local/bin/python3"
fi

# ----------------------- 2. Python virtualenv + deps -----------------------
VENV="$ROOT/macos/.venv"
echo "==> Setting up build venv: $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install --quiet pyinstaller ebooklib pygobject

# ----------------------- 3. Icon (.icns) -----------------------
ICNS="$API_DIR/org.omabible.icns"
if [ ! -f "$ICNS" ]; then
    echo "==> Generating $ICNS from the SVG (best effort)"
    ICONSET="$API_DIR/OmaBible.iconset"
    mkdir -p "$ICONSET"
    # Render the SVG to a large PNG via Apple's Quick Look.
    ( cd "$API_DIR" && qlmanage -t -s 1024 -o . org.omabible.svg >/dev/null 2>&1 ) || true
    PNG="$API_DIR/org.omabible.svg.png"
    if [ -f "$PNG" ]; then
        for size in 16 32 64 128 256 512 1024; do
            sips -z "$size" "$size" "$PNG" \
                --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1 || true
        done
        iconutil -c icns "$ICONSET" -o "$ICNS" >/dev/null 2>&1 || true
        rm -rf "$ICONSET" "$PNG"
    fi
    [ -f "$ICNS" ] || echo "    (icon generation skipped; building without a custom icon)"
fi

# ----------------------- 4. PyInstaller build -----------------------
echo "==> Building OmaBible.app (this takes a while)"
cd "$ROOT"
"$VENV/bin/pyinstaller" --noconfirm --clean "macos/Spec"

echo ""
echo "==> Done. Bundle at: $ROOT/dist/OmaBible.app"
echo "    Next: wrap it with ./macos/make_dmg.sh or ./macos/make_pkg.sh"
