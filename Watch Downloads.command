#!/bin/bash
# Double-click to start the DVW download watcher: new .dvw files in Downloads are filed into the
# right season / scouting folder and the dashboard is rebuilt (not pushed). Keep this window open; close it to stop.
cd "$(dirname "$0")" || exit 1
exec python3 watch_downloads.py
