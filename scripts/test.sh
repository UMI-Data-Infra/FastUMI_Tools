#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT/app"

python3 -m py_compile "$PROJECT_ROOT"/app/fastumi_tools/*.py "$PROJECT_ROOT"/app/tools/*.py
python3 -m unittest discover -s "$PROJECT_ROOT/tests" -v
node --check "$PROJECT_ROOT/app/static/app.js"
node "$PROJECT_ROOT/tests/frontend_behavior.cjs" "$PROJECT_ROOT/app/static/app.js"
PACKAGE="$($PROJECT_ROOT/scripts/build_deb.sh)"
dpkg-deb --info "$PACKAGE" >/dev/null
dpkg-deb --contents "$PACKAGE" | grep 'fastumi-tools.service' >/dev/null
echo "All FastUMI Tools tests passed."
