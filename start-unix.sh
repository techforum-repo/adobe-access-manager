#!/usr/bin/env bash
# Linux/macOS equivalent of start-windows.bat
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_CMD=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_CMD="$candidate"
    break
  fi
done

if [ -z "$PYTHON_CMD" ]; then
  echo
  echo "ERROR: Python was not found."
  echo "Install Python 3.11 or 3.12 and make sure it is on PATH."
  echo
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  if ! "$PYTHON_CMD" -m venv .venv; then
    echo
    echo "ERROR: Failed to create the virtual environment."
    echo "On Debian/Ubuntu you may need: sudo apt install python3-venv"
    echo
    exit 1
  fi
fi

echo "Installing dependencies into the virtual environment..."
if ! ".venv/bin/python" -m pip install -r requirements.txt; then
  echo
  echo "ERROR: Dependency installation failed."
  echo
  exit 1
fi

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
fi

echo "Starting Adobe Access Manager..."
exec ".venv/bin/python" -m streamlit run app.py
