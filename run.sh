#!/bin/sh
# Linux / macOS:  sh run.sh   (options are passed on: sh run.sh --demo, sh run.sh --stop ...)
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then exec python3 run.py "$@"; else exec python run.py "$@"; fi
