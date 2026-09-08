#!/usr/bin/env bash
# OmaBible installer.
#
# Provisions system + Python dependencies, creates the project virtualenv,
# installs the launcher command and, on Linux, the desktop entry + icon.
#
# Usage:
#   ./install.sh            # install for the current user (~/.local)
#   ./install.sh --system   # install to system locations (needs sudo)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="omabible"
ICON_SRC="$DIR/org.omabible.svg"

echo "==> OmaBible installer"

# ------------------------- system dependencies -------------------------
install_system() {
    # e.g. `./install.sh --system` with the OS package manager needing sudo.
    if command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm \
            python python-gobject python-pip webkit2gtk-4.1 gtk3
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-gi python3-pip gir1.2-webkit2-4.1 \
            libwebkit2gtk-4.1-0 gir1.2-gtk-3.0
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-gobject python3-pip \
            webkit2gtk4.1 gtk3
    elif command -v brew >/dev/null 2>&1 && [[ "$(uname)" == "Darwin" ]]; then
        brew install python@3.11 gtk3 webkit2gtk gobject-introspection
        brew install pygobject3
    else
        echo "    (no known package manager; install python3, GTK3 & WebKit 4.1 manually)"
    fi
}

# Default for the current user (no sudo needed for the Python deps).
install_system

# ------------------------- python virtualenv + deps -------------------------
VENV="$DIR/.venv"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
    echo "==> Creating virtualenv"
    python3 -m venv "$VENV"
fi
# Use system PyGTK/WebKit bindings on Linux if present, but still create a
# standalone venv when unavailable (e.g. macOS) so pip can pull pygobject.
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install --quiet ebooklib
echo "    python deps ready"

# ------------------------- executable + desktop (Linux) -------------------------
if [[ "$(uname)" == "Linux" ]]; then
    if [[ "${1:-}" == "--system" ]]; then
        BIN_DIR="/usr/local/bin"
        APP_DIR="/usr/local/share/applications"
        ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
        SUDO=(sudo)
    else
        BIN_DIR="$HOME/.local/bin"
        APP_DIR="$HOME/.local/share/applications"
        ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
        SUDO=()
    fi

    mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

    # Executable command -> absolute path to this repo's run.sh
    "${SUDO[@]}" tee "$BIN_DIR/$APP_NAME" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$DIR/run.sh" "\$@"
EOF
    "${SUDO[@]}" chmod +x "$BIN_DIR/$APP_NAME"

    # Desktop entry (absolute Exec path, no hardcoded user paths in the repo)
    "${SUDO[@]}" tee "$APP_DIR/org.omabible.desktop" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=OmaBible
GenericName=Bible Reader
Comment=Read EPUB Bibles, styled after your Omarchy theme
Exec=$BIN_DIR/$APP_NAME %F
Icon=org.omabible
Terminal=false
Categories=Office;Viewer;Literature;
Keywords=bible;epub;reader;scripture;
StartupNotify=true
StartupWMClass=$APP_NAME
EOF

    # Icon (SVG)
    "${SUDO[@]}" cp "$ICON_SRC" "$ICON_DIR/org.omabible.svg"

    "${SUDO[@]}" update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "==> Installed launcher + desktop entry ($BIN_DIR/$APP_NAME)"
else
    # Non-Linux (e.g. macOS): just wire up the launcher on the PATH.
    mkdir -p "$HOME/.local/bin"
    tee "$HOME/.local/bin/$APP_NAME" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$DIR/run.sh" "\$@"
EOF
    chmod +x "$HOME/.local/bin/$APP_NAME"
    echo "==> Installed launcher ($HOME/.local/bin/$APP_NAME)"
fi

echo "==> Done. Run: '$APP_NAME'  (or open a book: '$APP_NAME /path/to/book.epub')"
