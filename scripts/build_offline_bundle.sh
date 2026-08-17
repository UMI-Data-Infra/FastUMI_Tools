#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")"
PACKAGE="$($PROJECT_ROOT/scripts/build_deb.sh)"
STAGE="$(mktemp -d -t fastumi-tools-offline.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
BUNDLE_NAME="fastumi-tools-offline_${VERSION}_amd64"
BUNDLE_ROOT="$STAGE/$BUNDLE_NAME"

install -d "$BUNDLE_ROOT"
cp "$PACKAGE" "$BUNDLE_ROOT/"
cp -a "$PROJECT_ROOT/payloads" "$BUNDLE_ROOT/payloads"
install -m 0755 "$PROJECT_ROOT/scripts/install_offline.sh" "$BUNDLE_ROOT/install.sh"
(
  cd "$BUNDLE_ROOT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

OUTPUT="$PROJECT_ROOT/dist/${BUNDLE_NAME}.tar.gz"
tar -czf "$OUTPUT" -C "$STAGE" "$BUNDLE_NAME"
echo "$OUTPUT"

