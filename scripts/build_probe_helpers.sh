#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAYLOAD_ROOT="${1:-$PROJECT_ROOT/payloads}"
OUTPUT_ROOT="${2:-$PROJECT_ROOT/dist/probes}"
MANIFEST="$PAYLOAD_ROOT/manifest.json"
STAGE="$(mktemp -d -t fastumi-probes.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

if [[ ! -f "$MANIFEST" ]]; then
  echo "Probe manifest not found: $MANIFEST" >&2
  exit 1
fi
if ! command -v g++ >/dev/null 2>&1; then
  echo "g++ is required to build the isolated XVSDK probe helpers." >&2
  exit 1
fi

install -d "$OUTPUT_ROOT"
python3 - "$MANIFEST" <<'PY' | while IFS=$'\t' read -r generation artifact release package_version relative sha256; do
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
for item in manifest.get("sdk", []):
    generation = str(item.get("camera_generation") or "gen1")
    if item.get("recommended") and generation in ("gen1", "gen2"):
        print("\t".join([
            generation,
            str(item["id"]),
            str(item["release"]),
            str(item.get("package_version") or ""),
            str(item["path"]),
            str(item["sha256"]),
        ]))
PY
  package="$PAYLOAD_ROOT/$relative"
  if [[ ! -f "$package" ]]; then
    echo "Probe SDK package not found: $package" >&2
    exit 1
  fi
  actual="$(sha256sum "$package" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    echo "Probe SDK checksum mismatch: $package" >&2
    exit 1
  fi

  extracted="$STAGE/$generation"
  install -d "$extracted"
  dpkg-deb -x "$package" "$extracted"
  output="$OUTPUT_ROOT/$generation"
  install -d "$output/usr/lib"
  find "$extracted/usr/lib" -maxdepth 1 -type f -name '*.so*' \
    ! -name 'libCInterface-general.so' \
    ! -name 'libxvisio-CInterface-wrapper.so' \
    -exec install -m 0644 {} "$output/usr/lib/" \;
  find "$output/usr/lib" -maxdepth 1 -type f -name '*.so*' \
    -exec strip --strip-unneeded {} +
  g++ -std=c++11 -O2 \
    -I"$extracted/usr/include/xvsdk" \
    "$PROJECT_ROOT/app/tools/xvsdk_version.cpp" \
    -o "$output/xvsdk_version" \
    -L"$extracted/usr/lib" \
    -Wl,--allow-shlib-undefined \
    -Wl,--wrap=__libc_start_main \
    -Wl,-rpath,'$ORIGIN/usr/lib' \
    -lxvsdk
  chmod 0755 "$output/xvsdk_version"
  strip --strip-unneeded "$output/xvsdk_version"
  if objdump -T "$output/xvsdk_version" | grep -Eq 'GLIBC_2\.(3[2-9]|[4-9][0-9])'; then
    echo "Probe helper requires a glibc newer than Ubuntu 20.04: $generation" >&2
    exit 1
  fi
  python3 - "$output/probe.json" "$artifact" "$generation" "$release" "$package_version" <<'PY'
import json
import sys

path, artifact, generation, release, runtime_version = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "schema_version": 1,
        "artifact_id": artifact,
        "camera_generation": generation,
        "release": release,
        "runtime_version": runtime_version,
    }, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
  chmod 0644 "$output/probe.json"
done
