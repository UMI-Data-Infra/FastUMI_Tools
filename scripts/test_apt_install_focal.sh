#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY="${1:-$PROJECT_ROOT/dist/apt-repository}"
REPOSITORY="$(realpath "$REPOSITORY")"
if [[ ! -f "$REPOSITORY/dists/focal/InRelease" ]]; then
    echo "Signed APT repository not found: $REPOSITORY" >&2
    exit 2
fi

docker run --rm \
    -v "$REPOSITORY:/fastumi-repository:ro" \
    ubuntu:20.04 /bin/bash -euc '
        export DEBIAN_FRONTEND=noninteractive
        install -m 0644 /fastumi-repository/fastumi-archive-keyring.gpg /usr/share/keyrings/fastumi-archive-keyring.gpg
        printf "%s\n" "deb [arch=amd64 signed-by=/usr/share/keyrings/fastumi-archive-keyring.gpg] file:/fastumi-repository focal main" > /etc/apt/sources.list.d/fastumi-tools.list
        apt-get update
        apt-get install -y fastumi-tools
        test "$(dpkg-query -W -f="\${Status}" fastumi-tools)" = "install ok installed"
        test "$(dpkg-query -W -f="\${Status}" fastumi-tools-resources)" = "install ok installed"
        test -f /usr/share/doc/fastumi-tools/copyright
        test -f /usr/share/fastumi-tools/payloads/sdk/20260522/XVSDK_focal_amd64_0522.deb
        test -f /usr/share/fastumi-tools/payloads/firmware/firmware-20260514.zip
        apt-get remove -y fastumi-tools fastumi-tools-resources
        test ! -e /opt/fastumi-tools
    '
