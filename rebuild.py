#!/usr/bin/env python3
"""
rebuild.py — LUC Volleyball Dashboard Pipeline
================================================
Scans all DVW folders, updates volleyball_data.json,
regenerates dashboard_data.json, and rebuilds dashboard.html.
Optionally auto-pushes dashboard.html to GitHub so coaches
always see the latest version at the GitHub Pages URL.

Usage:
    python3 rebuild.py              # full rebuild + auto-push if git is configured
    python3 rebuild.py --dry-run   # parse only, no writes
    python3 rebuild.py --force     # reparse all files (ignore existing dataset)
    python3 rebuild.py --no-push   # skip the git push step

Auto-push setup (one-time, in Terminal):
    cd "path/to/LUC Volleyball"
    git init -b main
    git remote add origin https://github.com/YOUR_USERNAME/luc-volleyball.git
    git add .
    git commit -m "initial"
    git push -u origin main
    # Then enable GitHub Pages in repo Settings → Pages → Branch: main / root
"""

import os
import sys
import glob
import json
import shutil
import subprocess
import argparse
import time
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Absolute path to the DVW source folders
DVW_ROOT = Path(__file__).parent.parent.parent / "Volleyball DVW Files"

# Folder name → (season label, session type, is_scout)
FOLDER_MAP = {
    "Fall 2025":        ("Fall 2025",   "practice", False),
    "Spring 2025":      ("Spring 2025", "practice", False),
    "Fall 2026":        ("Fall 2026",   "practice", False),
    "Spring 2026":      ("Spring 2026", "practice", False),
    "Spring 2026 Scout":("Spring 2026 Scout", "scout", True),
    # Add new seasons here, e.g.:
    # "Fall 2027":      ("Fall 2027",   "practice", False),
}

# Files in this script's directory
HERE         = Path(__file__).parent
PARSE_SCRIPT = HERE / "parse_dvw.py"
AGG_SCRIPT   = HERE / "aggregate.py"
DATASET      = HERE / "volleyball_data.json"
DASH_DATA    = HERE / "dashboard_data.json"
TEMPLATE     = HERE / "dashboard_template.html"
DASHBOARD    = HERE / "dashboard.html"

# ── HELPERS ──────────────────────────────────────────────────────────────────
def run(cmd, desc):
    print(f"\n{'─'*60}")
    print(f"  {desc}")
    print(f"{'─'*60}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        print(f"\n❌  Command failed (exit {result.returncode})")
        sys.exit(1)

def inject_data(template_path, data_path, output_path):
    """Embed dashboard_data.json into the HTML template."""
    with open(template_path, 'r') as f:
        html = f.read()
    with open(data_path, 'r') as f:
        data = f.read()
    if '__DASHBOARD_DATA__' not in html:
        print("❌  Template is missing __DASHBOARD_DATA__ placeholder")
        sys.exit(1)
    html = html.replace('__DASHBOARD_DATA__', data)
    with open(output_path, 'w') as f:
        f.write(html)
    mb = output_path.stat().st_size / 1024 / 1024
    print(f"  ✅  dashboard.html written ({mb:.1f} MB)")

# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rebuild the LUC volleyball dashboard")
    parser.add_argument('--dry-run',  action='store_true', help='Parse only, no writes')
    parser.add_argument('--force',    action='store_true', help='Reparse from scratch (delete existing dataset first)')
    parser.add_argument('--no-html',  action='store_true', help='Skip HTML rebuild step')
    parser.add_argument('--no-push',  action='store_true', help='Skip git push step')
    args = parser.parse_args()

    print("\n🏐  LUC Volleyball Dashboard — Rebuild")
    print(f"    DVW root : {DVW_ROOT}")
    print(f"    Dataset  : {DATASET}")
    print(f"    Dashboard: {DASHBOARD}\n")

    # Validate paths
    if not DVW_ROOT.exists():
        print(f"❌  DVW root not found: {DVW_ROOT}")
        sys.exit(1)
    if not PARSE_SCRIPT.exists():
        print(f"❌  parse_dvw.py not found at {PARSE_SCRIPT}")
        sys.exit(1)

    if args.force and DATASET.exists():
        print(f"  🗑   Removing existing dataset (--force)")
        DATASET.unlink()

    # ── Step 1: Parse each folder ────────────────────────────────────────────
    t0 = time.time()
    for folder_name, (season_label, session_type, is_scout) in FOLDER_MAP.items():
        folder = DVW_ROOT / folder_name
        if not folder.exists():
            print(f"  ⚠   Folder not found, skipping: {folder}")
            continue

        dvw_files = sorted(folder.glob("*.dvw"))
        if not dvw_files:
            print(f"  ⚠   No .dvw files in {folder_name}, skipping")
            continue

        print(f"\n📂  {folder_name}  ({len(dvw_files)} files)  →  season='{season_label}' type={session_type}")

        cmd = [
            sys.executable, str(PARSE_SCRIPT),
            "--season", season_label,
            "--type", session_type,
            "--dataset", str(DATASET),
        ]
        if is_scout:
            cmd.append("--scout")
        if args.dry_run:
            cmd.append("--no-save")
        cmd += [str(f) for f in dvw_files]

        run(cmd, f"Parsing {folder_name}")

    # ── Step 2: Aggregate ────────────────────────────────────────────────────
    if not args.dry_run:
        print(f"\n📊  Aggregating dataset → dashboard_data.json")
        run(
            [sys.executable, str(AGG_SCRIPT), str(DATASET), str(DASH_DATA)],
            "Aggregating"
        )

        # ── Step 3: Rebuild dashboard HTML ───────────────────────────────────
        if not args.no_html:
            if not TEMPLATE.exists():
                print(f"❌  dashboard_template.html not found — cannot rebuild HTML")
                sys.exit(1)
            print(f"\n🌐  Rebuilding dashboard.html")
            inject_data(TEMPLATE, DASH_DATA, DASHBOARD)

    elapsed = time.time() - t0
    print(f"\n✅  Done in {elapsed:.1f}s\n")
    if not args.dry_run and not args.no_html:
        print(f"    Open {DASHBOARD.name} in your browser to view the dashboard.")

    # ── Step 4: Auto-push to GitHub ──────────────────────────────────────────
    if not args.dry_run and not args.no_html and not args.no_push:
        git_dir = HERE / '.git'
        if not git_dir.exists():
            print("    ℹ   Git not initialized — skipping auto-push. See setup instructions above.\n")
        else:
            print("\n📤  Pushing dashboard to GitHub...")
            try:
                # Stage only the files we want public (not the large data files)
                files_to_commit = [
                    'dashboard.html',
                    'dashboard_template.html',
                    'aggregate.py',
                    'parse_dvw.py',
                    'rebuild.py',
                    'watch.py',
                    '.gitignore',
                ]
                subprocess.run(['git', 'add'] + files_to_commit, cwd=HERE, check=True)
                # Only commit if there are staged changes
                diff = subprocess.run(
                    ['git', 'diff', '--cached', '--quiet'],
                    cwd=HERE
                )
                if diff.returncode != 0:
                    import datetime
                    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                    subprocess.run(
                        ['git', 'commit', '-m', f'auto: dashboard update {ts}'],
                        cwd=HERE, check=True
                    )
                    subprocess.run(['git', 'push'], cwd=HERE, check=True)
                    print("  ✅  Dashboard pushed — coaches will see the update on refresh.\n")
                else:
                    print("  ℹ   No changes to push.\n")
            except subprocess.CalledProcessError as e:
                print(f"  ⚠   Git push failed: {e}")
                print("      Run 'git push' manually, or check your GitHub credentials.\n")
            except FileNotFoundError:
                print("  ⚠   git not found on PATH — skipping push.\n")


if __name__ == '__main__':
    main()
