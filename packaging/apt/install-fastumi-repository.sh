#!/bin/sh
set -eu

REPOSITORY_URL="https://km-data-pipeline.github.io/FastUMI_APT"
KEYRING="/usr/share/keyrings/fastumi-archive-keyring.gpg"
SOURCE_LIST="/etc/apt/sources.list.d/fastumi-tools.list"

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run this installer with sudo." >&2
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "Cannot identify the operating system." >&2
    exit 1
fi
. /etc/os-release
if [ "${VERSION_CODENAME:-}" != "focal" ]; then
    echo "FastUMI Tools currently supports Ubuntu 20.04 Focal only." >&2
    exit 1
fi
if [ "$(dpkg --print-architecture)" != "amd64" ]; then
    echo "FastUMI Tools currently supports amd64 only." >&2
    exit 1
fi

apt-get update
apt-get install -y ca-certificates curl
temporary_key="$(mktemp -t fastumi-archive-key.XXXXXX)"
trap 'rm -f "$temporary_key"' EXIT
curl -fsSL "$REPOSITORY_URL/fastumi-archive-keyring.gpg" -o "$temporary_key"
install -m 0644 "$temporary_key" "$KEYRING"
printf '%s\n' "deb [arch=amd64 signed-by=$KEYRING] $REPOSITORY_URL focal main" > "$SOURCE_LIST"
apt-get update

echo "FastUMI APT repository configured."
if [ "${1:-}" = "--install" ]; then
    apt-get install -y fastumi-tools
else
    echo "Install with: sudo apt install fastumi-tools"
fi
