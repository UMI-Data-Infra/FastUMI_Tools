#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY="${1:-$PROJECT_ROOT/dist/apt-repository}"
REPOSITORY="$(realpath "$REPOSITORY")"
if [[ ! -f "$REPOSITORY/dists/focal/InRelease" ]]; then
    echo "Signed APT repository not found: $REPOSITORY" >&2
    exit 2
fi

PORT="${FASTUMI_APT_TEST_PORT:-18765}"
SERVER_LOG="$(mktemp -t fastumi-apt-http.XXXXXX)"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$REPOSITORY" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
cleanup() {
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
    rm -f "$SERVER_LOG"
}
trap cleanup EXIT
for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$PORT/dists/focal/InRelease" >/dev/null; then
        break
    fi
    sleep 0.1
done
curl -fsS "http://127.0.0.1:$PORT/dists/focal/InRelease" >/dev/null

timeout 900 docker run --rm \
    --network host \
    -v "$REPOSITORY/fastumi-archive-keyring.gpg:/fastumi-archive-keyring.gpg:ro" \
    ubuntu:20.04 /bin/bash -euc '
        export DEBIAN_FRONTEND=noninteractive
        install -m 0644 /fastumi-archive-keyring.gpg /usr/share/keyrings/fastumi-archive-keyring.gpg
        printf "%s\n" "deb [arch=amd64 signed-by=/usr/share/keyrings/fastumi-archive-keyring.gpg] http://127.0.0.1:'"$PORT"' focal main" > /etc/apt/sources.list.d/fastumi-tools.list
        apt-get update
        apt-get install -y --no-install-recommends fastumi-tools fastumi-tools-resources
        test "$(dpkg-query -W -f="\${Status}" fastumi-tools)" = "install ok installed"
        test "$(dpkg-query -W -f="\${Status}" fastumi-tools-resources)" = "install ok installed"
        /usr/bin/python3 -c "import cv2,numpy"
        command -v dfu-util >/dev/null
        test -f /usr/share/doc/fastumi-tools/copyright
        test -f /usr/share/fastumi-tools/payloads/sdk/20260522/XVSDK_focal_amd64_0522.deb
        test -f /usr/share/fastumi-tools/payloads/firmware/firmware-20260514.zip
        apt-get remove -y fastumi-tools fastumi-tools-resources
        test ! -e /opt/fastumi-tools
    '
