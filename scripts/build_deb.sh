#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")"
CATALOG_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["catalog_version"])' "$PROJECT_ROOT/payloads/manifest.json")"
OUTPUT_DIR="$PROJECT_ROOT/dist"
STAGE="$(mktemp -d -t fastumi-tools-deb.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" "$STAGE/opt/fastumi-tools" "$STAGE/usr/lib/systemd/system"
install -d "$STAGE/usr/local/bin" "$STAGE/usr/bin" "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/fastumi-tools/payloads/firmware"
install -d "$STAGE/usr/share/doc/fastumi-tools"
install -d "$STAGE/etc" "$OUTPUT_DIR"

cp -a "$PROJECT_ROOT/app" "$PROJECT_ROOT/assets" "$STAGE/opt/fastumi-tools/"
find "$STAGE/opt/fastumi-tools" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE/opt/fastumi-tools" -type f -name '*.pyc' -delete
install -m 0644 "$PROJECT_ROOT/VERSION" "$STAGE/opt/fastumi-tools/VERSION"
install -m 0644 "$PROJECT_ROOT/payloads/manifest.json" "$STAGE/usr/share/fastumi-tools/payloads/manifest.json"
install -m 0644 "$PROJECT_ROOT/payloads/firmware/99-LumosVisio.rules" "$STAGE/usr/share/fastumi-tools/payloads/firmware/99-LumosVisio.rules"

sed -e "s/@VERSION@/$VERSION/g" -e "s/@CATALOG_VERSION@/$CATALOG_VERSION/g" \
    "$PROJECT_ROOT/packaging/control.in" > "$STAGE/DEBIAN/control"
install -m 0644 "$PROJECT_ROOT/LICENSE" "$STAGE/usr/share/doc/fastumi-tools/copyright"
install -m 0644 "$PROJECT_ROOT/NOTICE" "$STAGE/usr/share/doc/fastumi-tools/NOTICE"
install -m 0755 "$PROJECT_ROOT/packaging/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$PROJECT_ROOT/packaging/prerm" "$STAGE/DEBIAN/prerm"
install -m 0755 "$PROJECT_ROOT/packaging/postrm" "$STAGE/DEBIAN/postrm"
install -m 0644 "$PROJECT_ROOT/packaging/systemd/fastumi-tools.service" "$STAGE/usr/lib/systemd/system/fastumi-tools.service"
install -m 0644 "$PROJECT_ROOT/packaging/desktop/fastumi-tools.desktop" "$STAGE/usr/share/applications/fastumi-tools.desktop"
install -m 0644 "$PROJECT_ROOT/packaging/desktop/fastumi-tools.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/fastumi-tools.svg"
install -m 0755 "$PROJECT_ROOT/packaging/fastumi-tools-open" "$STAGE/usr/local/bin/fastumi-tools-open"
install -m 0755 "$PROJECT_ROOT/packaging/fastumi-tools-cli" "$STAGE/usr/bin/fastumi-tools"
install -m 0644 "$PROJECT_ROOT/packaging/fastumi-tools.conf" "$STAGE/etc/fastumi-tools.conf"
install -d -m 0755 "$STAGE/var/lib/fastumi-tools/payloads"

PACKAGE="$OUTPUT_DIR/fastumi-tools_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$PACKAGE" >&2
echo "$PACKAGE"
