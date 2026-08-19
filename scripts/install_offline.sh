#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE="$(find "$BUNDLE_ROOT" -maxdepth 1 -type f -name 'fastumi-tools_*_amd64.deb' -print -quit)"
if [[ -z "$PACKAGE" || ! -d "$BUNDLE_ROOT/payloads" ]]; then
  echo "离线包不完整。" >&2
  exit 1
fi

install -d -m 0755 /var/lib/fastumi-tools/payloads
cp -a "$BUNDLE_ROOT/payloads/." /var/lib/fastumi-tools/payloads/
apt-get install -y --reinstall "$PACKAGE"
systemctl restart fastumi-tools.service

echo "FastUMI Tools 安装完成。"
echo "请从应用菜单打开 FastUMI Tools。"
