#!/bin/sh
#  FW — a worldbuilding application for fiction writers.
#  Run this file to start it (macOS / Linux). The first run sets everything up;
#  after that it goes straight to the app.
set -e
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "Python 3.11 or newer is needed and was not found."
    echo "Install it (https://www.python.org/downloads/) and run this file again."
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up a private Python environment..."
    "$PY" -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    echo "Installing the application — this needs the internet once..."
    .venv/bin/python -m pip install --quiet -e .
    echo "Done. This part never runs again."
fi

echo "Starting FW... the app will open in your browser."
echo "Leave this window open while you work; press Ctrl+C to stop the app."
exec .venv/bin/python -m fw serve --open
