#!/usr/bin/env python3
"""
upload.py - local upload page for new DVW files
================================================
Start it:  double-click "Upload DVW.command" in this folder, or run  python3 upload.py
A page opens in your browser at http://127.0.0.1:8765 (only this computer can open it).
The page itself is upload_page.html (served with a fresh random key each time this starts).
Season folders live in seasons.json (rebuild.py reads the same file).

On the page:
  1. Drop .dvw files on it. Each file is read right away: teams, date, match / practice / scouting,
     and which season folder it belongs in. Possible problems are flagged (same match already on the
     dashboard, wrong season, Loyola file going into a scouting folder, ...).
  2. Check or change the folder for each file (or make a new season folder).
  3. Press "Save and rebuild": the files are copied into "Volleyball DVW Files/<folder>", rebuild.py
     runs (parse -> aggregate -> dashboard.html -> player portal) and, if the box is ticked, the update
     is pushed to GitHub. Progress and the full log show on the page.

Safety:
  - Listens on 127.0.0.1 only, and every request must carry the random key built into the page when it
    starts, so other websites can't send files to it.
  - A file that replaces an older version with the same name: the old file is moved to
    "Volleyball DVW Files/_replaced/<date time>/<folder>/", and its old session is taken out of
    volleyball_data.json first (a copy is saved as volleyball_data.backup.json) so the new one is read.
  - Nothing in private/ is read or served.

Stop it with Ctrl+C in the Terminal window, or the "Stop the upload page" button.
"""

import os
import re
import sys
import json
import time
import uuid
import shutil
import hashlib
import datetime
import tempfile
import threading
import subprocess
import webbrowser
import io
import contextlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = Path(__file__).resolve().parent
DVW_ROOT = Path(os.environ.get('LUC_DVW_ROOT') or (HERE.parent.parent / 'Volleyball DVW Files'))
SEASONS = HERE / 'seasons.json'
DATASET = HERE / 'volleyball_data.json'
DATASET_BACKUP = HERE / 'volleyball_data.backup.json'
DASH_DATA = HERE / 'dashboard_data.json'
REBUILD = HERE / 'rebuild.py'
REPLACED_DIR = DVW_ROOT / '_replaced'
PORT0 = int(os.environ.get('LUC_UPLOAD_PORT') or 8765)
MAX_BYTES = 50 * 1024 * 1024
TOKEN = uuid.uuid4().hex
STAGE = Path(tempfile.mkdtemp(prefix='luc_upload_'))

sys.path.insert(0, str(HERE))
import parse_dvw  # noqa: E402  (same folder)

SRV = None
PORT = None
PAGES_URL = None
STAGED = {}                  # id -> {name, path, size, sha}
PARSE_LOCK = threading.Lock()
INDEX_LOCK = threading.Lock()
INDEX = {'mtime': None, 'loyola': [], 'scout': []}
JOB_LOCK = threading.Lock()
JOB = {'id': 0, 'running': False, 'lines': [], 'step': '', 'steps': [], 'result': None}
LUC_KEYWORDS = ('loyola', 'luc')     # same rule parse_dvw uses to find Loyola's side


# ---------------------------------------------------------------- season folders (seasons.json)
def load_seasons():
    try:
        rows = json.loads(SEASONS.read_text('utf-8')).get('folders', [])
    except FileNotFoundError:
        rows = []
    if not rows:
        try:
            import rebuild
            rows = [{'folder': k, 'season': v[0], 'type': v[1], 'scout': bool(v[2])} for k, v in rebuild.FOLDER_MAP.items()]
        except Exception:
            rows = []
    return [dict(r, scout=bool(r.get('scout')), type=r.get('type') or ('scout' if r.get('scout') else 'practice')) for r in rows if r.get('folder')]


def save_seasons(rows):
    try:
        doc = json.loads(SEASONS.read_text('utf-8'))
    except Exception:
        doc = {}
    doc['folders'] = [{'folder': r['folder'], 'season': r['season'], 'type': r['type'], 'scout': bool(r['scout'])} for r in rows]
    tmp = SEASONS.with_name(SEASONS.name + '.tmp')
    tmp.write_text(json.dumps(doc, indent=2) + '\n', 'utf-8')
    os.replace(str(tmp), str(SEASONS))


def folder_rows():
    rows = load_seasons()
    mapped = {r['folder'] for r in rows}
    out = []
    for r in rows:
        p = DVW_ROOT / r['folder']
        n = len(list(p.glob('*.dvw'))) if p.is_dir() else 0
        out.append(dict(r, exists=p.is_dir(), files=n))
    unmapped = []
    if DVW_ROOT.is_dir():
        unmapped = sorted(p.name for p in DVW_ROOT.iterdir()
                          if p.is_dir() and p.name not in mapped and not p.name.startswith(('.', '_')))
    return out, unmapped


def clean_folder(name):
    name = (name or '').replace('\\', '/').split('/')[-1].strip()
    name = re.sub(r'[\x00-\x1f:]', '', name)
    if not name or name.startswith(('.', '_')):
        raise ValueError('Folder names cannot be empty or start with "." or "_".')
    return name


def season_from_date(d):
    """Season label a date belongs to: Jan-Jul = Spring YYYY; Aug - mid Dec = Fall (YYYY+1)
    (Fall 2027 = fall 2026); from Dec 20 on = next Spring (winter-break practices count toward Spring)."""
    try:
        dt = datetime.date.fromisoformat(str(d)[:10])
    except Exception:
        return None
    if dt.month <= 7:
        return 'Spring %d' % dt.year
    if dt.month == 12 and dt.day >= 20:
        return 'Spring %d' % (dt.year + 1)
    return 'Fall %d' % (dt.year + 1)


def base_season(label):
    return re.sub(r'\s*scout\s*$', '', label or '', flags=re.I).strip()


# ---------------------------------------------------------------- what's already on the dashboard
def load_index():
    with INDEX_LOCK:
        try:
            mt = DASH_DATA.stat().st_mtime
        except FileNotFoundError:
            return INDEX
        if mt != INDEX['mtime']:
            d = json.loads(DASH_DATA.read_text('utf-8'))
            keys = ('file', 'date', 'season', 'type', 'opponent', 'home_team', 'away_team')
            INDEX['loyola'] = [{k: s.get(k) for k in keys} for s in d.get('sessions', [])]
            INDEX['scout'] = [{k: s.get(k) for k in keys} for s in d.get('scout_index', [])]
            INDEX['mtime'] = mt
        return INDEX


def on_dashboard(scout, season, name):
    idx = load_index()
    if scout:   # scout sessions: older files carry the plain season label, so match on the file name
        return [s for s in idx['scout'] if s['file'] == name]
    return [s for s in idx['loyola'] if s['file'] == name and s['season'] == season]


def _norm_team(t):
    t = re.sub(r"\(.*?\)|\b(men'?s|women'?s|university|college|of|the)\b", ' ', (t or '').lower())
    return re.sub(r'[^a-z0-9]', '', t)


def same_team(a, b):
    """Loose team-name match ('Ohio State University (Men's)' = 'Ohio State')."""
    a, b = _norm_team(a), _norm_team(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def same_pair(h1, a1, h2, a2):
    return (same_team(h1, h2) and same_team(a1, a2)) or (same_team(h1, a2) and same_team(a1, h2))


def opp_of(home, away):
    return away if is_luc(home) else home


# ---------------------------------------------------------------- staging + inspecting files
def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def clean_name(n):
    n = (n or '').replace('\\', '/').split('/')[-1].strip()
    n = re.sub(r'[\x00-\x1f:]', '_', n)
    stem, ext = os.path.splitext(n)
    if ext.lower() != '.dvw':
        raise ValueError('Only .dvw files can be added.')
    stem = stem.strip()
    if not stem:
        raise ValueError('The file has no name.')
    if stem.startswith('.'):
        stem = '_' + stem[1:]
    return stem + '.dvw'


def stage_file(name, data):
    name = clean_name(name)
    if not data:
        raise ValueError('The file is empty.')
    sid = uuid.uuid4().hex[:12]
    d = STAGE / sid
    d.mkdir(parents=True)
    p = d / name
    p.write_bytes(data)
    STAGED[sid] = {'name': name, 'path': p, 'size': len(data), 'sha': hashlib.sha1(data).hexdigest()}
    return sid


def is_luc(team):
    return bool(team) and any(k in team.lower() for k in LUC_KEYWORDS)


def parse_quiet(path, cfg):
    with PARSE_LOCK:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            return parse_dvw.parse_dvw(str(path), season=cfg['season'] if cfg else None,
                                       session_type=cfg['type'] if cfg else None,
                                       is_scout=bool(cfg and cfg['scout']))


def suggest_folder(date, luc, rows):
    """Best existing folder for a file, or a proposal for a new one."""
    guess = season_from_date(date)
    scout = not luc
    if not guess:
        return None, None
    want = guess + ' Scout' if scout else guess
    for r in rows:
        if r['scout'] == scout and r['season'] == want:
            return r['folder'], None
    for r in rows:
        if r['scout'] == scout and base_season(r['season']) == guess:
            return r['folder'], None
    # scouting folders are named "<season> Scout" but carry the plain season label
    return None, {'folder': want, 'season': guess, 'type': 'scout' if scout else 'practice', 'scout': scout}


def inspect(sid, cfg=None, rows=None):
    """Read a staged file (as it would be read in folder cfg) and list anything worth a look."""
    st = STAGED[sid]
    name = st['name']
    res = {'id': sid, 'name': name, 'size': st['size'], 'warnings': [], 'notes': [], 'state': 'new'}
    try:
        s = parse_quiet(st['path'], cfg)
    except Exception as e:
        res['error'] = 'Could not read this as a DVW file (%s).' % e
        return res
    home, away = s.get('home_team'), s.get('away_team')
    if not s.get('action_count') and not (home or away):
        res['error'] = 'This does not look like a DVW file (no teams or actions found).'
        return res
    luc = is_luc(home) or is_luc(away)
    res.update(date=s.get('date'), type=s.get('type'), home=home, away=away, luc=luc,
               opponent=s.get('opponent'), actions=s.get('action_count', 0), players=s.get('player_count', 0),
               season_guess=season_from_date(s.get('date')))
    if not res['actions']:
        res['warnings'].append('No actions were found in this file.')
    rows = rows if rows is not None else load_seasons()
    if cfg is None:
        folder, new = suggest_folder(s.get('date'), luc, rows)
        res['suggest'] = folder
        res['suggest_new'] = new
        cfg = next((r for r in rows if r['folder'] == folder), None) or new
        if cfg is None:
            res['notes'].append('Could not tell the season from this file. Pick a folder.')
            return res
        s2 = parse_quiet(st['path'], cfg)     # read again the way this folder will read it
        res.update(date=s2.get('date'), type=s2.get('type'), opponent=s2.get('opponent'),
                   season_guess=season_from_date(s2.get('date')))
    res['folder'] = cfg['folder']
    res['season'] = cfg['season']
    check(res, cfg)
    return res


def check(res, cfg):
    name, W, N = res['name'], res['warnings'], res['notes']
    scout = cfg['scout']
    if scout and res['luc']:
        W.append('Loyola is one of the teams, but "%s" is a scouting folder.' % cfg['folder'])
    if not scout and not res['luc'] and (res.get('home') or res.get('away')):
        W.append('Loyola is not one of the teams (%s vs %s). Opponent files go in a Scout folder.' % (res.get('home'), res.get('away')))
    g = res.get('season_guess')
    if g and base_season(cfg['season']) != g:
        W.append('The date (%s) looks like %s, but this folder is %s.' % (res.get('date'), g, cfg['season']))
    if not scout and res['luc'] and res.get('type') == 'practice' and res.get('home') and res.get('away') \
            and not (is_luc(res['home']) and is_luc(res['away'])):
        W.append('This looks like a match (%s vs %s), but the file name does not say so, so it would count as a '
                 'practice. Rename it like "LUC vs Opponent.dvw" (or keep the VolleyMetrics name) and drop it again.'
                 % (res['home'], res['away']))

    target = DVW_ROOT / cfg['folder'] / name
    dash = on_dashboard(scout, cfg['season'], name)
    st = STAGED[res['id']]
    if target.exists():
        if sha1(target) == st['sha']:
            res['state'] = 'identical' if dash else 'in_folder'
            N.append('This exact file is already in "%s"%s.' % (
                cfg['folder'], ' and on the dashboard. Nothing to add' if dash else '. The rebuild will add it to the dashboard'))
        else:
            res['state'] = 'replace'
            W.append('A different version of this file is already in "%s". Saving replaces it (the old one is kept in '
                     'Volleyball DVW Files/_replaced).' % cfg['folder'])
    elif dash:
        res['state'] = 'replace'
        N.append('Already on the dashboard, but its original file is no longer in the folder. Saving puts this one in '
                 'its place.')
    others = [p.parent.name for p in DVW_ROOT.glob('*/' + glob_escape(name))
              if p.parent.name != cfg['folder'] and not p.parent.name.startswith(('_', '.'))] if DVW_ROOT.is_dir() else []
    if others:
        N.append('A file with the same name is also in: %s.' % ', '.join(sorted(set(others))))

    if res.get('date') and res['state'] != 'identical':     # nothing gets added for an identical file
        idx = load_index()
        h, a = res.get('home'), res.get('away')
        itself = lambda s: s['file'] == name and (scout or s['season'] == cfg['season'])
        if scout:
            same = [s for s in idx['scout'] if s['date'] == res['date'] and not itself(s)
                    and same_pair(h, a, s['home_team'], s['away_team'])]
            if same:
                W.append('Looks like the same match as "%s", which is already in scouting. It would be skipped as a '
                         'duplicate (same team and date).' % same[0]['file'])
        elif res.get('type') == 'match':
            opp = opp_of(h, a)
            same = [s for s in idx['loyola'] if s['type'] == 'match' and s['date'] == res['date'] and not itself(s)
                    and (not opp or same_team(opp, opp_of(s['home_team'], s['away_team'])))]
            if same:
                W.append('Looks like the same match as "%s" (season "%s"), which is already on the dashboard. Adding '
                         'this one would count the match twice.' % (same[0]['file'], same[0]['season']))
        else:
            same = [s for s in idx['loyola'] if s['type'] == 'practice' and s['date'] == res['date']
                    and s['season'] == cfg['season'] and not itself(s)]
            if same:
                N.append('There is already a practice on %s ("%s"). If this is the same practice it would be counted '
                         'twice.' % (res['date'], same[0]['file']))


def glob_escape(s):
    return re.sub(r'([\[\]\*\?])', r'[\1]', s)


# ---------------------------------------------------------------- the save + rebuild job
def jlog(msg):
    JOB['lines'].append(msg)
    try:
        print(msg, flush=True)
    except Exception:       # a Terminal without UTF-8 can't print the emoji in rebuild.py's output
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


STEP_MARKS = [('\U0001F4C2', 'Reading DVW files'), ('\U0001F4CA', 'Crunching the numbers'),
              ('\U0001F310', 'Building dashboard.html'), ('\U0001F512', 'Building player portal files'),
              ('\U0001F4E4', 'Pushing to GitHub')]


def set_step(label):
    JOB['step'] = label
    if not JOB['steps'] or JOB['steps'][-1] != label:
        JOB['steps'].append(label)


def start_job(files, new_folders, push):
    with JOB_LOCK:
        if JOB['running']:
            raise RuntimeError('A rebuild is already running.')
        JOB.update(id=JOB['id'] + 1, running=True, lines=[], step='', steps=[], result=None)
    t = threading.Thread(target=run_job, args=(files, new_folders, push), daemon=True)
    t.start()
    return JOB['id']


def drop_sessions(drop):
    """Take old versions of re-uploaded files out of volleyball_data.json so the new file is read
    (the parser skips files that are already in the dataset)."""
    if not DATASET.exists() or not drop:
        return
    jlog('Taking the old version of %d file(s) out of volleyball_data.json (copy saved as %s) ...'
         % (len(drop), DATASET_BACKUP.name))
    shutil.copy2(str(DATASET), str(DATASET_BACKUP))
    ds = parse_dvw.load_dataset(str(DATASET))
    n0 = len(ds['sessions']) + len(ds['scout_sessions'])
    scout_names = {n for sc, se, n in drop if sc}
    luc_keys = {(se, n) for sc, se, n in drop if not sc}
    ds['sessions'] = [s for s in ds['sessions'] if (s.get('season'), s.get('file')) not in luc_keys]
    ds['scout_sessions'] = [s for s in ds['scout_sessions'] if s.get('file') not in scout_names]
    removed = n0 - len(ds['sessions']) - len(ds['scout_sessions'])
    parse_dvw.save_dataset(ds, str(DATASET))
    jlog('  removed %d old session(s)' % removed)


def clear_git_locks():
    for nm in ('HEAD.lock', 'index.lock'):
        p = HERE / '.git' / nm
        try:
            if p.exists() and time.time() - p.stat().st_mtime > 10:
                p.unlink()
                jlog('Removed a stale git lock file (.git/%s)' % nm)
        except OSError:
            pass


def run_job(files, new_folders, push):
    res = {'ok': False, 'files': [], 'push': None, 'error': None}
    try:
        set_step('Saving files')
        rows = load_seasons()
        known = {r['folder']: r for r in rows}
        added_folder = False
        for nf in new_folders or []:
            f = clean_folder(nf.get('folder'))
            if f in known:
                continue
            season = (nf.get('season') or f).strip()
            row = {'folder': f, 'season': season, 'type': 'scout' if nf.get('scout') else 'practice', 'scout': bool(nf.get('scout'))}
            rows.append(row)
            known[f] = row
            added_folder = True
            jlog('New season folder "%s" (season "%s"%s) added to seasons.json' % (f, season, ', scouting' if row['scout'] else ''))
        if added_folder:
            save_seasons(rows)

        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H%M%S')
        drop = []
        for it in files or []:
            st = STAGED.get(it.get('id'))
            cfg = known.get(it.get('folder'))
            if not st or not cfg:
                res['files'].append({'name': (st or {}).get('name', '?'), 'folder': it.get('folder'), 'outcome': 'error',
                                     'detail': 'File or folder not found - drop the file again.'})
                continue
            entry = {'name': st['name'], 'folder': cfg['folder'], 'season': cfg['season'], 'scout': cfg['scout']}
            dest_dir = DVW_ROOT / cfg['folder']
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / st['name']
            in_dash = bool(on_dashboard(cfg['scout'], cfg['season'], st['name']))
            if dest.exists() and sha1(dest) == st['sha']:
                entry['saved'] = 'already there'
                jlog('%s is already in "%s" (same file)' % (st['name'], cfg['folder']))
            elif dest.exists():
                if it.get('action') == 'skip':
                    entry.update(saved='skipped', outcome='skipped', detail='Kept the old version.')
                    res['files'].append(entry)
                    continue
                bk = REPLACED_DIR / stamp / cfg['folder']
                bk.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(bk / st['name']))
                shutil.copy2(str(st['path']), str(dest))
                entry['saved'] = 'replaced'
                jlog('Replaced %s in "%s" (old version moved to _replaced/%s/%s)' % (st['name'], cfg['folder'], stamp, cfg['folder']))
                if in_dash:
                    drop.append((cfg['scout'], cfg['season'], st['name']))
            else:
                shutil.copy2(str(st['path']), str(dest))
                entry['saved'] = 'saved'
                jlog('Saved %s to "%s"' % (st['name'], cfg['folder']))
                if in_dash:
                    drop.append((cfg['scout'], cfg['season'], st['name']))
            res['files'].append(entry)

        drop_sessions(drop)
        if push:
            clear_git_locks()

        set_step('Rebuilding')
        cmd = [sys.executable, '-u', str(REBUILD)] + ([] if push else ['--no-push'])
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8', GIT_TERMINAL_PROMPT='0',
                   LUC_DVW_ROOT=str(DVW_ROOT))
        jlog('Running rebuild.py%s ...' % ('' if push else ' --no-push'))
        p = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1)
        cur_folder = None
        parsed, skipped, errors = {}, set(), {}
        names = {(e['folder'], e['name']) for e in res['files'] if e.get('saved')}
        for line in p.stdout:
            line = line.rstrip('\n')
            jlog(line)
            for mark, label in STEP_MARKS:
                if mark in line:
                    if mark == '\U0001F4C2':
                        m = re.search('\U0001F4C2\\s+(.+?)\\s+\\(\\d+ files\\)', line)
                        cur_folder = m.group(1) if m else None
                        set_step('Reading DVW files: %s' % (cur_folder or ''))
                    else:
                        set_step(label)
            m = re.match(r'\s*Parsing (.+?\.dvw) \.\.\. ?(.*)$', line)
            if m and (cur_folder, m.group(1)) in names:
                if m.group(2).startswith('ERROR'):
                    errors[(cur_folder, m.group(1))] = m.group(2)
                else:
                    parsed[(cur_folder, m.group(1))] = m.group(2)
            m = re.match(r'\s*\[skip(?:-dup)?\] (.+?\.dvw) \(', line)
            if m and (cur_folder, m.group(1)) in names:
                skipped.add((cur_folder, m.group(1)))
        rc = p.wait()

        for e in res['files']:
            if e.get('outcome'):
                continue
            k = (e['folder'], e['name'])
            if k in errors:
                e.update(outcome='error', detail='Could not be read: ' + errors[k])
            elif k in skipped:
                e.update(outcome='already', detail='Already on the dashboard (not added twice).')
            elif k in parsed:
                e.update(outcome='added', detail=re.sub(r'[^\x20-\x7e]', '-', parsed[k]).strip())
            else:
                e.update(outcome='unknown', detail='Not seen in the rebuild (is the folder in seasons.json?).')

        log = '\n'.join(JOB['lines'])
        if not push:
            res['push'] = 'off'
        elif 'Dashboard pushed' in log:
            res['push'] = 'pushed'
        elif 'No changes to push' in log:
            res['push'] = 'nothing'
        elif 'Git push failed' in log or 'git not found' in log:
            res['push'] = 'failed'
        elif 'Git not initialized' in log:
            res['push'] = 'nogit'
        if rc != 0:
            res['error'] = 'The rebuild stopped with an error (see the log below).'
        else:
            res['ok'] = True
            set_step('Done')
            for e in res['files']:
                if e.get('outcome') in ('added', 'already'):
                    for sid, st in list(STAGED.items()):
                        if st['name'] == e['name']:
                            STAGED.pop(sid, None)
    except Exception as ex:
        res['error'] = '%s: %s' % (type(ex).__name__, ex)
        jlog('ERROR ' + res['error'])
    finally:
        JOB['result'] = res
        JOB['running'] = False


# ---------------------------------------------------------------- http
def pages_url():
    try:
        r = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=str(HERE), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
        m = re.search(r'github\.com[:/]+([^/\s@]+)/([^/\s]+?)(?:\.git)?/?$', r.stdout.strip())
        if m:
            return 'https://%s.github.io/%s/' % (m.group(1).lower(), m.group(2))
    except Exception:
        pass
    return None


def state():
    rows, unmapped = folder_rows()
    return {'folders': rows, 'unmapped': unmapped, 'dvwRoot': str(DVW_ROOT), 'pages': PAGES_URL,
            'busy': JOB['running'], 'hasDashboard': (HERE / 'dashboard.html').exists()}


def job_view(since):
    return {'id': JOB['id'], 'running': JOB['running'], 'step': JOB['step'], 'steps': JOB['steps'],
            'lines': JOB['lines'][since:], 'next': len(JOB['lines']), 'result': JOB['result']}


PREVIEW_OK = {'dashboard.html', 'dashboard_sets.json', 'players.html', 'scout_app.html'}
CTYPES = {'.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'LUCUpload/1.0'

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        return host in ('127.0.0.1', 'localhost')

    def _origin_ok(self):
        o = self.headers.get('Origin')
        return o is None or o in ('http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT)

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {'error': 'forbidden'})
        u = urlparse(self.path)
        if u.path in ('/', '/index.html'):
            csp = ("default-src 'self'; script-src 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                   "font-src https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'none'")
            return self._send(200, PAGE.replace('__TOKEN__', TOKEN), 'text/html; charset=utf-8', {'Content-Security-Policy': csp})
        if u.path.startswith('/preview/'):
            rel = unquote(u.path[len('/preview/'):]).strip('/')
            ok = rel in PREVIEW_OK or re.fullmatch(r'player_data/[A-Za-z0-9_.-]+\.json', rel)
            p = HERE / rel
            if not ok or not p.is_file():
                return self._send(404, 'Not found', 'text/plain; charset=utf-8')
            return self._send(200, p.read_bytes(), CTYPES.get(p.suffix, 'application/octet-stream'))
        if self.headers.get('X-Upload-Token') != TOKEN:
            return self._send(403, {'error': 'Missing key. Reload the page.'})
        if u.path == '/api/state':
            return self._send(200, state())
        if u.path == '/api/job':
            try:
                since = int(parse_qs(u.query).get('since', ['0'])[0])
            except ValueError:
                since = 0
            return self._send(200, job_view(since))
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok() or self.headers.get('X-Upload-Token') != TOKEN:
            return self._send(403, {'error': 'Missing key. Reload the page.'})
        u = urlparse(self.path)
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n > MAX_BYTES:
            return self._send(413, {'error': 'That file is too large for a DVW file.'})
        body = self.rfile.read(n) if n else b''
        try:
            if u.path == '/api/stage':
                sid = stage_file(unquote(self.headers.get('X-File-Name') or ''), body)
                return self._send(200, inspect(sid))
            data = json.loads(body.decode('utf-8') or '{}')
            if u.path == '/api/inspect':
                sid = data.get('id')
                if sid not in STAGED:
                    return self._send(404, {'error': 'File not found - drop it again.'})
                rows = load_seasons()
                cfg = next((r for r in rows if r['folder'] == data.get('folder')), None)
                nf = data.get('newFolder')
                if cfg is None and nf:
                    cfg = {'folder': clean_folder(nf.get('folder')), 'season': (nf.get('season') or nf.get('folder')).strip(),
                           'type': 'scout' if nf.get('scout') else 'practice', 'scout': bool(nf.get('scout'))}
                if cfg is None:
                    return self._send(400, {'error': 'Unknown folder.'})
                return self._send(200, inspect(sid, cfg, rows))
            if u.path == '/api/remove':
                st = STAGED.pop(data.get('id'), None)
                if st:
                    shutil.rmtree(str(st['path'].parent), ignore_errors=True)
                return self._send(200, {'ok': True})
            if u.path == '/api/commit':
                jid = start_job(data.get('files') or [], data.get('newFolders') or [], bool(data.get('push')))
                return self._send(200, {'job': jid})
            if u.path == '/api/quit':
                if JOB['running']:
                    return self._send(409, {'error': 'Wait for the rebuild to finish first.'})
                threading.Thread(target=SRV.shutdown, daemon=True).start()
                return self._send(200, {'ok': True})
        except (ValueError, RuntimeError) as e:
            return self._send(400, {'error': str(e)})
        except Exception as e:
            return self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})
        return self._send(404, {'error': 'not found'})


# ---------------------------------------------------------------- the page
PAGE_FILE = HERE / 'upload_page.html'
PAGE = PAGE_FILE.read_text('utf-8') if PAGE_FILE.exists() else '<h1>upload_page.html is missing</h1>'


def main():
    global SRV, PORT, PAGES_URL
    if not DVW_ROOT.is_dir():
        print('\n  Could not find the DVW folder: %s\n  (it should sit two folders up from this one)\n' % DVW_ROOT)
        sys.exit(1)
    for port in range(PORT0, PORT0 + 20):
        try:
            SRV = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            PORT = port
            break
        except OSError:
            continue
    if SRV is None:
        print('Could not find a free port.')
        sys.exit(1)
    SRV.daemon_threads = True
    PAGES_URL = pages_url()
    url = 'http://127.0.0.1:%d/' % PORT
    print('\n  LUC Volleyball - upload page')
    print('  -----------------------------')
    print('  Open:        %s' % url)
    print('  DVW folder:  %s' % DVW_ROOT)
    print('  Leave this window open while you use the page. Ctrl+C (or the Stop button on the page) stops it.\n')
    if '--no-browser' not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        SRV.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SRV.server_close()
        shutil.rmtree(str(STAGE), ignore_errors=True)
        print('\n  Upload page stopped.\n')


if __name__ == '__main__':
    main()
