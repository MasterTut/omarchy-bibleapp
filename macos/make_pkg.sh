#!/usr/bin/env bash
# Build a distributable .pkg installer from the built .app.
#
#   ./macos/make_pkg.sh [path/to/OmaBible.app]
#
# Produces dist/OmaBible-<version>.pkg (installer-style, needs
# Developer Tools' pkgbuild/productbuild on PATH).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/OmaBible.app}"
VERSION="${VERSION:-1.0.0}"

if [ ! -d "$APP" ]; then
    echo "ERROR: .app not found at $APP — run ./macos/build_app.sh first."
    exit 1
fi
command -v pkgbuild >/dev/null 2>&1 || { echo "ERROR: pkgbuild not found (needs Xcode Command Line Tools)."; exit 1; }

mkdir -p "$ROOT/dist"
BOM="$ROOT/dist/.component.bom"
PKGINFO="$ROOT/dist/.pkg-info"

# Build the payload component (.pkg bundles are a signed component + resources).
pkgbuild --analyze --root "$ROOT/dist/OmaBible.app" "$PKGINFO" >/dev/null 2>&1
pkgbuild \
    --root "$ROOT/dist/OmaBible.app" \
    --component-plist "$PKGINFO" \
    --identifier "org.omabible" \
    --version "$VERSION" \
    --install-location "/Applications" \
    "$ROOT/dist/OmaBible-${VERSION}-component.pkg"

# productbuild it into a single installer.
productbuild \
    --package "$ROOT/dist/OmaBible-${VERSION}-component.pkg" \
    "$ROOT/dist/OmaBible-${VERSION}.pkg"

rm -f "$ROOT/dist/.component.bom" "$ROOT/dist/.pkg-info" \
      "$ROOT/dist/OmaBible-${VERSION}-component.pkg"

echo "==> Created: $ROOT/dist/OmaBible-${VERSION}.pkg"
