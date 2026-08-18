#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")"
CATALOG_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["catalog_version"])' "$PROJECT_ROOT/payloads/manifest.json")"
OUTPUT_DIR="$PROJECT_ROOT/dist"
STAGE="$(mktemp -d -t fastumi-tools-resources-deb.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" "$STAGE/usr/share/fastumi-tools/payloads"
install -d "$STAGE/usr/share/doc/fastumi-tools-resources" "$OUTPUT_DIR"
cp -a "$PROJECT_ROOT/payloads/sdk" "$STAGE/usr/share/fastumi-tools/payloads/"
install -d "$STAGE/usr/share/fastumi-tools/payloads/firmware"
find "$PROJECT_ROOT/payloads/firmware" -maxdepth 1 -type f -name '*.zip' \
    -exec install -m 0644 {} "$STAGE/usr/share/fastumi-tools/payloads/firmware/" \;

sed -e "s/@CATALOG_VERSION@/$CATALOG_VERSION/g" \
    -e "s/@MINIMUM_TOOLS_VERSION@/$TOOLS_VERSION/g" \
    "$PROJECT_ROOT/packaging/resources-control.in" > "$STAGE/DEBIAN/control"
install -m 0644 "$PROJECT_ROOT/LICENSE" "$STAGE/usr/share/doc/fastumi-tools-resources/copyright"
install -m 0644 "$PROJECT_ROOT/NOTICE" "$STAGE/usr/share/doc/fastumi-tools-resources/NOTICE"

PACKAGE="$OUTPUT_DIR/fastumi-tools-resources_${CATALOG_VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$PACKAGE" >&2
echo "$PACKAGE"
