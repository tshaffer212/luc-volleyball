#!/usr/bin/env python3
"""
watch.py — Auto-rebuild when DVW files change
===============================================
Watches the DVW source folders for new or modified .dvw files
and automatically triggers rebuild.py.

Setup (one-time):
    pip3 install watchdog --break-system-packages

Run:
    python3 watch.py

Leave this running in a Terminal window while you work.
Press Ctrl+C to stop.
"""

import sys
import time
import subprocess
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("\n❌  watchdog is not installed. Run this first:")
    print("     pip3 install watchdog --break-system-packages\n")
    sys.exit(1)

DVW_ROOT  = Path(__file__).resolve().parent.parent.parent / "Volleyball DVW Files"
REBUILD   = Path(__file__).resolve().parent / "rebuild.py"
DEBOUNCE  = 3.0  # seconds to wait after last change before rebuilding

class DVWHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending       = False
        self._force_rebuild = False  # set True on deletion (need full rescan)
        self._last_event    = 0

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.dvw'):
            self._queue(event.src_path, force=False)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.dvw'):
            self._queue(event.src_path, force=False)

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith('.dvw'):
            self._queue(event.src_path, force=True)

    def _queue(self, path, force=False):
        label = '🗑' if force else '📄'
        action = 'Deleted' if force else 'Change detected'
        print(f"\n  {label}  {action}: {Path(path).name}")
        self._pending = True
        self._last_event = time.time()
        if force:
            self._force_rebuild = True  # latch; stays True until rebuild runs

    def check_and_rebuild(self):
        if self._pending and (time.time() - self._last_event) >= DEBOUNCE:
            self._pending = False
            force = self._force_rebuild
            self._force_rebuild = False
            print(f"\n{'='*60}")
            if force:
                print("  🔄  File deleted — forcing full rescan...")
            else:
                print("  🔄  Triggering rebuild...")
            print(f"{'='*60}")
            cmd = [sys.executable, str(REBUILD)]
            if force:
                cmd.append('--force')
            result = subprocess.run(cmd)
            if result.returncode == 0:
                print("\n  ✅  Dashboard updated — refresh your browser.\n")
            else:
                print("\n  ❌  Rebuild failed. Check output above.\n")

def main():
    if not DVW_ROOT.exists():
        print(f"\n❌  DVW folder not found: {DVW_ROOT}\n")
        sys.exit(1)

    handler  = DVWHandler()
    observer = Observer()
    observer.schedule(handler, str(DVW_ROOT), recursive=True)
    observer.start()

    print(f"\n👀  Watching for .dvw changes in:")
    print(f"    {DVW_ROOT}")
    print(f"\n    Add, update, or delete any .dvw file and the dashboard")
    print(f"    will rebuild automatically (after a {DEBOUNCE:.0f}s delay).")
    print(f"    Deletions trigger a full rescan (--force) to remove stale data.")
    print(f"\n    Press Ctrl+C to stop.\n")

    try:
        while True:
            handler.check_and_rebuild()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  Stopping watcher.\n")
        observer.stop()
    observer.join()

if __name__ == '__main__':
    main()
