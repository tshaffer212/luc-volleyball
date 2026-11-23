#!/bin/bash
# Double-click to open the LUC Volleyball upload page (http://127.0.0.1:8765) in your browser.
# Keep this Terminal window open while you use the page. Close it (or press Ctrl+C) to stop.
cd "$(dirname "$0")" || exit 1
exec python3 upload.py
