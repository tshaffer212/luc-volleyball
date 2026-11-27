#!/usr/bin/env python3
"""
watch_downloads.py - file new DVW downloads automatically
==========================================================
Start it:  double-click "Watch Downloads.command" in this folder (or  python3 watch_downloads.py).
Leave the Terminal window open. It checks your Downloads folder every few seconds.

When a .dvw file finishes downloading (from VolleyMetrics or anywhere else) it:
  1. reads the file the same way the upload page does (teams, date, match / practice / scouting) and picks the
     season folder: an opponent-only file goes in "<season> Scout", a Loyola file in its season folder;
  2. files it if nothing looks off: the download is moved into Volleyball DVW Files/<folder> (under its standard
     name, e.g. "LOY vs UCLA (3-23).dvw"), so nothing is left behind in Downloads;
  3. moves duplicates to Volleyball DVW Files/_duplicates/<folder>/: the exact same file already filed, or the same
     match already on the dashboard under another name. (If a duplicate is really a corrected version that should
     replace the old one, drop it from there on the upload page.)
  4. leaves a file in Downloads and tells you why if it needs a decision (a different version of a file that's
     already there, the wrong-looking season, a file it can't read). Drop those on the upload page;
  5. waits until downloads stop for a few seconds, then rebuilds the dashboard ONCE for the whole batch. It does
     NOT push to GitHub (use the upload page's "Rebuild only" with the push box ticked, or Terminal), unless you
     start it with --push.
A Mac notification says what happened; watch_downloads.log keeps the history.

Only files downloaded after the watcher was first started are handled (it remembers that time in
.watch_downloads.json). To also handle older files in Downloads: --since-days 3.

Options
  --folder PATH     folder to watch (default ~/Downloads)
  --since-days N    also handle .dvw files from the last N days (one time)
  --once            handle what's there now and stop (used for testing)
  --push            push to GitHub after each rebuild
  --no-rebuild      file the downloads but skip the rebuild
"""

import os
import sys
import json
import time
import shutil
import hashlib
import argparse
import datetime
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import upload  # noqa: E402  (same folder: file inspection, season folders, save + rebuild)

STATE_FILE = HERE / '.watch_downloads.json'
LOG_FILE = HERE / 'watch_downloads.log'
POLL = 3          # seconds between checks
SETTLE = 8        # wait this long after the last new download before filing + rebuilding a batch
PARTIAL = ('.crdownload', '.download', '.part', '.tmp')


# ---------------------------------------------------------------- small helpers
def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def notify(title, text):
    """Mac notification (silently skipped anywhere else)."""
    if sys.platform != 'darwin':
        return
    esc = lambda s: str(s).replace('\\', '\\\\').replace('"', '\\"')
    try:
        subprocess.run(['osascript', '-e', 'display notification "%s" with title "%s"' % (esc(text)[:230], esc(title))],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def file_sha(p):
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text('utf-8'))
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    tmp.write_text(json.dumps(st, indent=1), 'utf-8')
    os.replace(str(tmp), str(STATE_FILE))


def unique_dest(folder, name):
    d = folder / name
    if not d.exists():
        return d
    stem, ext = os.path.splitext(name)
    i = 2
    while (folder / ('%s (%d)%s' % (stem, i, ext))).exists():
        i += 1
    return folder / ('%s (%d)%s' % (stem, i, ext))


# ---------------------------------------------------------------- one batch
def describe(res):
    kind = {'match': 'match', 'scout': 'scouting file', 'practice': 'practice'}.get(res.get('type'), 'file')
    teams = '%s vs %s' % (res.get('home') or '?', res.get('away') or '?')
    return '%s %s (%s)' % (kind, res.get('date') or '', teams)


def to_duplicates(p, folder, name):
    """Move a duplicate download into Volleyball DVW Files/_duplicates/<folder>/ (never read by the dashboard)."""
    d = upload.DUP_DIR / folder
    d.mkdir(parents=True, exist_ok=True)
    dest = unique_dest(d, name)
    shutil.move(str(p), str(dest))
    return dest


def handle_batch(paths, args, state):
    """Inspect every finished download, file the clean ones, move duplicates, rebuild once. Returns a summary dict."""
    to_save, new_folders, left, skipped = [], {}, [], []
    originals = {}
    for p in paths:
        name = p.name
        try:
            data = p.read_bytes()
            sid = upload.stage_file(name, data)
        except Exception as e:
            left.append((name, 'could not be read (%s)' % e))
            continue
        res = upload.inspect(sid)
        sha = upload.STAGED[sid]['sha']
        if res.get('error'):
            left.append((name, res['error']))
            continue
        other = [w for w in res.get('warnings', []) if w != res.get('dup_msg')]
        dup = res.get('state') == 'identical' or (res.get('dup_of') and not other and res.get('state') != 'replace')
        if dup and res.get('folder'):
            why = ('exact copy of a file already filed' if res.get('state') == 'identical'
                   else 'same match as "%s", already on the dashboard' % res['dup_of'])
            try:
                dest = to_duplicates(p, res['folder'], res.get('name') or name)
                skipped.append((name, '%s - moved to _duplicates/%s/%s' % (why, res['folder'], dest.name)))
                state['done'][sha] = {'file': name, 'when': time.time(), 'outcome': 'duplicate', 'to': str(dest)}
            except OSError as e:
                left.append((name, '%s (could not move it: %s)' % (why, e)))
            continue
        if res.get('warnings'):
            left.append((name, res['warnings'][0]))
            continue
        folder = res.get('folder')
        if not folder:
            left.append((name, 'could not tell which season folder it belongs in'))
            continue
        nf = res.get('suggest_new')
        if nf and nf.get('folder') == folder:
            new_folders[folder] = {'folder': nf['folder'], 'season': nf['season'], 'scout': nf['scout']}
        to_save.append({'id': sid, 'folder': folder, 'action': 'replace'})
        originals[sid] = (p, sha, res, upload.STAGED[sid]['name'], upload.STAGED[sid]['path'].parent)
        saved_as = res.get('name') or name
        log('  ready: %s -> "%s"%s  [%s]' % (name, folder, ('  as "%s"' % saved_as) if saved_as != name else '', describe(res)))

    outcome = {'filed': [], 'left': left, 'skipped': skipped, 'rebuild': None}
    if to_save:
        if args.no_rebuild:
            # save the files only (no rebuild): copy straight into their folders
            for it in to_save:
                st = upload.STAGED[it['id']]
                dest_dir = upload.DVW_ROOT / it['folder']
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(st['path']), str(dest_dir / st['name']))
            if new_folders:
                rows = upload.load_seasons()
                known = {r['folder'] for r in rows}
                for nf in new_folders.values():
                    if nf['folder'] not in known:
                        rows.append({'folder': nf['folder'], 'season': nf['season'], 'type': 'scout' if nf['scout'] else 'practice', 'scout': nf['scout']})
                upload.save_seasons(rows)
            result = {'ok': True, 'files': [{'name': upload.STAGED[it['id']]['name'], 'folder': it['folder'], 'outcome': 'saved'} for it in to_save]}
        else:
            log('Saving %d file(s) and rebuilding the dashboard%s ...' % (len(to_save), ' (and pushing)' if args.push else ''))
            upload.JOB.update(lines=[], steps=[], result=None, running=True)
            upload.run_job(to_save, list(new_folders.values()), args.push)
            result = upload.JOB['result'] or {'ok': False, 'error': 'no result', 'files': []}
            outcome['rebuild'] = result
        by_name = {f['name']: f for f in result.get('files', [])}
        for sid, (p, sha, res, staged_name, tmpdir) in originals.items():
            shutil.rmtree(str(tmpdir), ignore_errors=True)
            f = by_name.get(staged_name) or {}
            ok = f.get('outcome') in ('added', 'already', 'saved')
            if ok:
                # the download now lives in its season folder: remove the Downloads copy once the filed copy is
                # confirmed identical (a move, done in two steps so nothing is lost if the save failed)
                filed = upload.DVW_ROOT / (f.get('folder') or res.get('folder')) / staged_name
                try:
                    if filed.exists() and upload.sha1(filed) == sha and p.exists():
                        os.remove(str(p))
                except OSError as e:
                    log('  (could not remove %s from Downloads: %s)' % (p.name, e))
                outcome['filed'].append((staged_name, f.get('folder') or res.get('folder'), describe(res)))
                state['done'][sha] = {'file': p.name, 'when': time.time(), 'outcome': 'filed', 'folder': f.get('folder')}
            else:
                left.append((p.name, f.get('detail') or result.get('error') or 'was not added'))
        if not result.get('ok'):
            log('  Rebuild problem: %s (last lines below)' % (result.get('error') or ''))
            for line in upload.JOB['lines'][-15:]:
                log('    ' + line)
    for name, why in left:
        log('  LEFT IN DOWNLOADS: %s - %s' % (name, why))
    for name, why in skipped:
        log('  duplicate: %s - %s' % (name, why))
    # every file we looked at is remembered, so a file left in Downloads isn't reported again and again
    for p in paths:
        state['seen'][str(p)] = p.stat().st_mtime if p.exists() else 0
    save_state(state)
    for sid in list(upload.STAGED):
        shutil.rmtree(str(upload.STAGED[sid]['path'].parent), ignore_errors=True)
        upload.STAGED.pop(sid, None)
    return outcome


def tell(outcome, args):
    f, l, s = outcome['filed'], outcome['left'], outcome['skipped']
    if not (f or l or s):
        return
    bits = []
    if f:
        names = ', '.join(sorted({x[1] for x in f}))
        bits.append('%d filed (%s)' % (len(f), names))
        if outcome['rebuild'] is not None:
            bits.append('dashboard rebuilt' + (', pushed' if args.push and outcome['rebuild'].get('push') == 'pushed' else ', not pushed'))
    if s:
        bits.append('%d duplicate%s moved to _duplicates' % (len(s), '' if len(s) == 1 else 's'))
    if l:
        bits.append('%d need a look - see watch_downloads.log' % len(l))
    notify('DVW downloads', '; '.join(bits))
    log('Done: ' + '; '.join(bits))


# ---------------------------------------------------------------- main loop
def candidates(folder, since, state):
    out = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return out
    for p in entries:
        n = p.name.lower()
        if not p.is_file() or not n.endswith('.dvw') or n.endswith(PARTIAL) or p.name.startswith('.'):
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt < since or state['seen'].get(str(p)) == mt:
            continue
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description='File new DVW downloads automatically')
    ap.add_argument('--folder', default=str(Path.home() / 'Downloads'))
    ap.add_argument('--since-days', type=float, default=None)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--no-rebuild', action='store_true')
    args = ap.parse_args()
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print('Folder not found: %s' % folder)
        sys.exit(1)
    if not upload.DVW_ROOT.is_dir():
        print('DVW folder not found: %s' % upload.DVW_ROOT)
        sys.exit(1)

    state = load_state()
    state.setdefault('seen', {})
    state.setdefault('done', {})
    if 'since' not in state:
        state['since'] = time.time()            # first start: only downloads from now on
    since = state['since']
    if args.since_days is not None:
        since = time.time() - args.since_days * 86400
    save_state(state)

    print('\n  LUC Volleyball - DVW download watcher')
    print('  -------------------------------------')
    print('  Watching:   %s' % folder)
    print('  Filing to:  %s' % upload.DVW_ROOT)
    print('  Handles .dvw files downloaded after %s' % datetime.datetime.fromtimestamp(since).strftime('%b %d %Y %H:%M'))
    print('  Rebuilds the dashboard after each batch%s. Close this window (or Ctrl+C) to stop.\n'
          % (' and pushes to GitHub' if args.push else ' (no push)'))

    sizes = {}        # path -> (size, first time seen at that size)
    last_new = 0
    try:
        while True:
            now = time.time()
            ready = []
            for p in candidates(folder, since, state):
                try:
                    sz = p.stat().st_size
                except OSError:
                    continue
                prev = sizes.get(str(p))
                if not prev or prev[0] != sz:
                    sizes[str(p)] = (sz, now)
                    last_new = now
                elif sz > 0 and now - prev[1] >= POLL:      # size unchanged for a full check = download finished
                    ready.append(p)
            # still downloading = a file whose size changed during the last check
            waiting = [k for k, (sz, t) in sizes.items() if now - t < POLL and Path(k).exists()]
            if ready and (args.once or now - last_new >= SETTLE) and not waiting:
                log('%d new download(s): %s' % (len(ready), ', '.join(p.name for p in ready)))
                outcome = handle_batch(ready, args, state)
                tell(outcome, args)
                for p in ready:
                    sizes.pop(str(p), None)
            if args.once and not waiting and not ready and now - last_new >= POLL:
                break
            time.sleep(POLL if not args.once else 1)
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(str(upload.STAGE), ignore_errors=True)
        print('\n  Watcher stopped.\n')


if __name__ == '__main__':
    main()
