#!/usr/bin/env sh
# 1-Click PocketLLM launcher for macOS and Linux.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required."
  echo "  macOS : brew install python3"
  echo "  Ubuntu/Debian: sudo apt update && sudo apt install -y python3"
  exit 1
fi

exec python3 "$SCRIPT_DIR/run.py" "$@"
