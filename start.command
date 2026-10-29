#!/bin/bash
# macOS Apple silicon / Intel: run entirely locally, public-data paper mode.
set -euo pipefail
cd "$(dirname "$0")"
printf '\n=== StepAhead Lab 1.1.3 — PAPER ONLY ===\n'
PY=''
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
      "$candidate" -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo 'Python 3.11 or later is required. Install it from https://www.python.org/downloads/macos/'
  exit 1
fi
if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
if ! .venv/bin/python -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
  echo 'Existing .venv uses old Python; rename or delete .venv and run again.'
  exit 1
fi
.venv/bin/python -m pip install --disable-pip-version-check -e .
.venv/bin/python -m unittest discover -s tests -q
printf 'Dashboard: http://127.0.0.1:8765/\nPress Ctrl+C to stop.\n'
exec .venv/bin/stepahead start "$@"
