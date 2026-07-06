#!/usr/bin/env python3
"""
DVW Parser for Loyola University Chicago Volleyball Analytics Dashboard
Parses DataVolley 2.0 .dvw files into structured JSON.

Schema is intentionally forward-compatible: fields that aren't yet coded
in older files (setter calls, rotation, zones) will simply be null/empty
and will populate automatically as future files grow richer.

Usage:
    # Parse one folder of Loyola practice/match files:
    python3 parse_dvw.py "Fall 2025/*.dvw" --season "Fall 2025"

    # Parse scout files (opponent data):
    python3 parse_dvw.py "Spring 2026 Scout/*.dvw" --scout

    # All above write to volleyball_data.json by default (cumulative/deduplicated).
"""

import re
import sys
import json
import os
import glob
import argparse
from datetime import datetime
from collections import Counter

# ─── Lookups ───────────────────────────────────────────────────────────────────

SKILLS = {
    'S': 'Serve',
    'R': 'Reception',
    'E': 'Set',
    'A': 'Attack',
    'B': 'Block',
    'D': 'Dig',
    'F': 'Freeball',
    'O': 'Freeball',
}

EVALUATIONS = {
    '#': 'Perfect',
    '+': 'Positive',
    '/': 'Half',
    '-': 'Negative',
    '=': 'Error',
    '!': 'Overpass',
    '~': 'Unknown',
}

EVAL_SCORE = {
    '#': 3, '+': 2, '/': 1, '-': 0, '=': -1, '!': None, '~': None,
}

# Setter call codes → labels (from [3SETTERCALL] section, also hard-coded as fallback)
DEFAULT_SETTER_CALLS = {
    'K1': 'Front Quick',
    'K2': 'Back Quick',
    'K7': 'Shoot/Gap',
    'KC': 'Quick in 3',
    'KM': 'Shifted to 2',
    'KP': 'Shifted to 4',
    'KE': 'No First Tempo',
    'KD': 'Slide',
}

# ─── Filename → date inference ─────────────────────────────────────────────────

def infer_date_from_filename(filename, season_label):
    """
    Try to extract a date from common filename patterns:
      YYYY-MM-DD ...     → ISO date (highest priority; used by Volleymetrics exports)
      M-D.dvw            → use season year for context
      M-D-YY.dvw         → explicit 2-digit year
      M-DD-YY.dvw        → same
      M-DD-YY(AM).dvw    → same, with suffix
      LUC vs TEAM.dvw    → no date in name; rely on file content
      TeamN.dvw          → scout file; rely on file content
    Returns ISO date string or None.
    """
    stem = os.path.splitext(filename)[0]

    # ISO date anywhere in filename (e.g. "&2026-05-03 765748 CSULB-LUC(VM).dvw")
    iso_m = re.search(r'(\d{4})-(\d{2})-(\d{2})', stem)
    if iso_m:
        try:
            return datetime(int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))).strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Strip suffixes like (AM), (1), (2)
    stem_clean = re.sub(r'\s*\(.*\)\s*$', '', stem).strip()

    # Pattern: M-D-YY  or  MM-DD-YY
    m = re.fullmatch(r'(\d{1,2})-(\d{1,2})-(\d{2})', stem_clean)
    if m:
        month, day, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year = 2000 + yy
        try:
            return datetime(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Pattern: M-D  (no year — infer from season_label)
    m = re.fullmatch(r'(\d{1,2})-(\d{1,2})', stem_clean)
    if m and season_label:
        month, day = int(m.group(1)), int(m.group(2))
        year_m = re.search(r'(\d{4})', season_label)
        if year_m:
            year = int(year_m.group(1))
            # Spring = Jan–May; Fall = Aug–Dec. If month doesn't match season,
            # the year might be off by one (e.g. "Spring 2026" with Dec 28 = 2025).
            is_spring = 'spring' in season_label.lower()
            is_fall = 'fall' in season_label.lower()
            if is_spring and month >= 8:
                year -= 1   # Dec/Jan in a "Spring 20XX" season = prior year
            if is_fall and month <= 5:
                year += 1   # shouldn't happen, but safety net
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    return None


def infer_session_type(filename, is_scout=False):
    """Classify a file as practice, match, or scout based on its name."""
    if is_scout:
        return 'scout'
    if re.search(r'\bvs\b', filename, re.IGNORECASE):
        return 'match'
    # Volleymetrics export pattern: "&YYYY-MM-DD 765748 TEAM-TEAM(VM).dvw"
    # Detected by "(VM)" suffix or 6-digit numeric ID between date and team names
    if re.search(r'\(VM\)', filename, re.IGNORECASE):
        return 'match'
    if re.search(r'\d{4}-\d{2}-\d{2}\s+\d{5,7}\s+', filename):
        return 'match'
    # Scout-style short names: "NKU1", "Pepp3", "UCSD2" etc.
    if re.match(r'^[A-Za-z]{2,6}\d?\.dvw$', filename):
        return 'scout'
    return 'practice'


def infer_opponent_from_filename(filename):
    """
    Extract opponent team abbreviation from scout/match filenames.
    'NKU3.dvw' → 'NKU'
    'LUC vs DU.dvw' → 'DU'
    'LMV vs UCLA.dvw' → 'UCLA'
    """
    stem = os.path.splitext(filename)[0].strip()
    # "X vs Y" pattern
    m = re.search(r'\bvs\s+(.+)$', stem, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Scout style: letters + optional digit  (NKU1, UCSD3, Pepp2, Quincy)
    m = re.match(r'^([A-Za-z]+)\d*$', stem)
    if m:
        return m.group(1).strip()
    return None


def infer_coding_version(season_label):
    """
    Return a coding detail level string based on the season.
    Older seasons have sparser coding (fewer zones, no setter calls).
    This lets the dashboard show/hide fields appropriately.
    """
    if not season_label:
        return 'basic'
    label = season_label.lower()
    if 'fall 2025' in label or 'spring 2025' in label:
        return 'basic'       # Early seasons — limited zone/setter coding
    if 'fall 2026' in label:
        return 'standard'    # Improving — more zones added
    if 'spring 2026' in label:
        return 'standard'    # Current season — richer coding
    return 'basic'


# ─── Section parsers ───────────────────────────────────────────────────────────

def parse_date(raw):
    """Parse date string — tries DD/MM/YYYY (DataVolley standard) then MM/DD/YYYY (Volleymetrics export).
    If the first successful parse yields a future date, the alternate format is tried instead."""
    raw = raw.strip()
    today = datetime.today().strftime('%Y-%m-%d')
    candidates = []
    for fmt in ('%d/%m/%Y', '%m/%d/%Y'):
        try:
            candidates.append(datetime.strptime(raw, fmt).strftime('%Y-%m-%d'))
        except ValueError:
            pass
    if not candidates:
        return raw or None
    # Prefer any candidate that is not in the future; fall back to first if all are future
    for c in candidates:
        if c <= today:
            return c
    return candidates[0]


def parse_players(lines):
    """Parse [3PLAYERS-H] or [3PLAYERS-V] block → dict keyed by player number."""
    players = {}
    for line in lines:
        parts = line.split(';')
        if len(parts) < 11:
            continue
        try:
            number = int(parts[1])
        except ValueError:
            continue
        last  = parts[9].strip()
        first = parts[10].strip()
        role     = parts[12].strip() if len(parts) > 12 else ''
        pos_code = parts[13].strip() if len(parts) > 13 else ''
        players[str(number)] = {
            'number':        number,
            'last_name':     last,
            'first_name':    first,
            'name':          f"{first} {last}".strip(),
            'is_libero':     role == 'L',
            'position_code': pos_code,   # 1=libero, 2=OH, 3=MH, 4=OPP, 5=setter
        }
    return players


def parse_attack_combos(lines):
    """Parse [3ATTACKCOMBINATION] block → dict keyed by combo code."""
    combos = {}
    for line in lines:
        parts = line.split(';')
        if len(parts) < 5:
            continue
        code = parts[0].strip()
        name = parts[4].strip()
        if code:
            combos[code] = name
    return combos


def parse_setter_calls(lines):
    """Parse [3SETTERCALL] block → dict keyed by call code."""
    calls = dict(DEFAULT_SETTER_CALLS)
    for line in lines:
        parts = line.split(';')
        if len(parts) < 3:
            continue
        code = parts[0].strip()
        name = parts[2].strip()
        if code and name:
            calls[code] = name
    return calls


def parse_scout_code(code):
    """
    Parse a DataVolley 2.0 scout action code.

    Fixed positions (0-indexed in code string):
      0     : team side (* = home, a = away)
      1-2   : player number (2-digit, zero-padded)
      3     : skill (S R E A B D F O)
      4     : technique
      5     : evaluation (# + / - = ! ~)
      6-7   : combination code (2 chars) or ~~
      8+    : zone / direction info (variable; grows richer with coding detail)

    Returns a dict. Unknown/empty fields are None.
    Future fields (rotation, setter_call_code) are present as None placeholders.
    """
    if len(code) < 4:
        return None

    result = {
        'team_side':    'home' if code[0] == '*' else 'away',
        'player_num':   code[1:3],
        'skill_code':   code[3] if len(code) > 3 else '~',
        'technique':    code[4] if len(code) > 4 else '~',
        'evaluation':   code[5] if len(code) > 5 else '~',
        'combo_code':   None,
        'start_zone':   None,   # zone attack was called from
        'end_zone':     None,   # where ball landed
        'direction':    None,   # attack direction (H=cross, B=line, C=cut, etc.)
        'num_blockers': None,
    }

    # Combination code (chars 6-7)
    if len(code) > 7:
        combo = code[6:8]
        result['combo_code'] = None if '~' in combo else combo

    # Parse zone / direction info from the remainder (char 8+)
    rest = code[8:] if len(code) > 8 else ''
    rest_stripped = rest.replace('~', '')

    # Volleymetrics format for receptions: ~~~ZN... where code[9] IS the zone (1-9)
    # and code[10] is a sub-position digit. e.g. a28RQ+~~~56CW = zone 5, sub-pos 6.
    # Do NOT treat these as x,y grid coordinates — code[9] is the zone directly.
    skill = result.get('skill_code', '')
    if (skill == 'R' and len(code) > 10
            and code[8] == '~' and code[9].isdigit() and code[10].isdigit()):
        result['end_zone'] = code[9]
    else:
        # Direction + end zone: letter followed immediately by digit (e.g. H2, B9)
        dir_match = re.search(r'([A-Z])(\d)', rest_stripped)
        if dir_match:
            result['direction'] = dir_match.group(1)
            result['end_zone']  = dir_match.group(2)

            # Start zone: digit before direction pair, possibly preceded by blocker count
            pre_dir = rest_stripped[:dir_match.start()]
            zones = re.findall(r'\d', pre_dir)
            if zones:
                result['start_zone']   = zones[0]
                result['num_blockers'] = int(zones[1]) if len(zones) > 1 else None
        else:
            # Serves / receptions: trailing digit = end zone
            zone_match = re.search(r'(\d+)$', rest_stripped)
            if zone_match:
                result['end_zone'] = zone_match.group(1)

    return result


# ─── Main file parser ──────────────────────────────────────────────────────────

def parse_dvw(filepath, season=None, session_type=None, is_scout=False):
    """
    Parse a single .dvw file into a session dict.

    The returned dict follows the canonical schema; fields not yet present
    in the file's coding level are null (not absent), keeping the schema stable
    across all seasons.
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    filename    = os.path.basename(filepath)
    parent_dir  = os.path.basename(os.path.dirname(filepath))

    # ── Season label ────────────────────────────────────────────────────────
    season_label = season
    if not season_label:
        if re.search(r'(fall|spring)\s*\d{4}', parent_dir, re.IGNORECASE):
            season_label = parent_dir.strip()
    if not season_label:
        season_label = 'Unknown Season'

    # ── Session type ────────────────────────────────────────────────────────
    # Always run infer_session_type first so VM match files are never
    # mis-classified as practice even when --type practice is passed.
    inferred_type = infer_session_type(filename, is_scout=is_scout)
    if session_type and inferred_type != 'match':
        inferred_type = session_type

    # ── Opponent (for match/scout files) ────────────────────────────────────
    opponent = infer_opponent_from_filename(filename) if inferred_type in ('match', 'scout') else None
    # If filename gave no opponent but we have team names, derive from teams section
    # (Volleymetrics filenames don't follow the "vs" convention)

    # ── Coding detail level ─────────────────────────────────────────────────
    coding_version = infer_coding_version(season_label)

    # ── Split file into named sections ──────────────────────────────────────
    sections = {}
    current_section = None
    current_lines   = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith('[3') and line.endswith(']'):
            if current_section:
                sections[current_section] = current_lines
            current_section = line[1:-1]
            current_lines   = []
        elif current_section and line:
            current_lines.append(line)
    if current_section:
        sections[current_section] = current_lines

    # ── [3MATCH] ────────────────────────────────────────────────────────────
    match_date = None
    match_time = None
    team_name  = 'Loyola University Chicago'

    # ISO date in filename takes highest priority (unambiguous; used by Volleymetrics exports
    # whose section date may use MM/DD vs DD/MM ambiguously).
    filename_iso_date = infer_date_from_filename(filename, season_label) if re.search(r'\d{4}-\d{2}-\d{2}', filename) else None

    match_lines = sections.get('3MATCH', [])
    if match_lines:
        parts = match_lines[0].split(';')
        if parts and parts[0].strip():
            match_date = parse_date(parts[0])
        if len(parts) > 1 and parts[1].strip():
            match_time = parts[1].strip()
        # Season from file content only used as fallback
        if len(parts) > 3 and not season_label:
            season_label = parts[3].strip() or season_label

    # ISO date from filename overrides ambiguous section date
    if filename_iso_date:
        match_date = filename_iso_date
    elif not match_date:
        match_date = infer_date_from_filename(filename, season_label)

    # ── [3TEAMS] ────────────────────────────────────────────────────────────
    team_lines = sections.get('3TEAMS', [])
    if team_lines and not is_scout:
        parts = team_lines[0].split(';')
        if len(parts) > 1 and parts[1].strip():
            team_name = parts[1].strip()

    home_team = None
    away_team = None
    if team_lines and len(team_lines) >= 2:
        hp = team_lines[0].split(';')
        ap = team_lines[1].split(';')
        home_team = hp[1].strip() if len(hp) > 1 else None
        away_team = ap[1].strip() if len(ap) > 1 else None

    # ── Which side is LUC on? ────────────────────────────────────────────
    # Practice files: LUC is always home. Match files from Volleymetrics may
    # have LUC as the visitor. Detect by checking team names.
    LUC_KEYWORDS = ('loyola', 'luc')
    luc_side = 'home'  # default (all practice files)
    if home_team and any(k in home_team.lower() for k in LUC_KEYWORDS):
        luc_side = 'home'
    elif away_team and any(k in away_team.lower() for k in LUC_KEYWORDS):
        luc_side = 'away'

    # ── [3PLAYERS] ──────────────────────────────────────────────────────────
    home_players = parse_players(sections.get('3PLAYERS-H', []))
    away_players = parse_players(sections.get('3PLAYERS-V', []))
    # Canonical roster: LUC's side (for backward compat, fallback to whichever exists)
    players = (home_players if luc_side == 'home' else away_players) or home_players or away_players

    # ── [3ATTACKCOMBINATION] ────────────────────────────────────────────────
    attack_combos = parse_attack_combos(sections.get('3ATTACKCOMBINATION', []))

    # ── [3SETTERCALL] ───────────────────────────────────────────────────────
    setter_calls = parse_setter_calls(sections.get('3SETTERCALL', []))

    # ── [3SCOUT] ────────────────────────────────────────────────────────────
    actions     = []
    scout_lines = sections.get('3SCOUT', [])

    for idx, line in enumerate(scout_lines):
        if not line or line[0] not in ('*', 'a'):
            continue

        fields   = line.split(';')
        code_str = fields[0]

        if len(code_str) < 4:
            continue

        parsed = parse_scout_code(code_str)
        if not parsed:
            continue

        skill_code = parsed['skill_code']
        if skill_code not in SKILLS:
            continue

        # ── Timestamp: try fields[7] then fields[8] ──────────────────────
        timestamp = None
        for ts_idx in (7, 8):
            if len(fields) > ts_idx:
                candidate = fields[ts_idx].strip()
                if candidate and len(candidate) == 8 and candidate[2] == '.' and candidate[5] == '.':
                    timestamp = candidate.replace('.', ':')
                    break

        # ── Side flags (fields 1-2): used for setter_call_code in future ─
        # e.g. 'p' = setter call position, 'r' = ? , 's' = ?
        # Currently stored as raw for future decoding
        side_flag_1 = fields[1].strip() if len(fields) > 1 else None
        side_flag_2 = fields[2].strip() if len(fields) > 2 else None

        # ── Player lookup ────────────────────────────────────────────────
        player_num     = parsed['player_num']
        player_num_int = int(player_num) if player_num.isdigit() else None
        player_num_str = str(player_num_int) if player_num_int is not None else player_num

        # Use each side's own roster (critical when LUC is the visitor)
        action_side  = parsed['team_side']  # 'home' or 'away'
        side_players = home_players if action_side == 'home' else away_players
        player_info  = side_players.get(player_num_str, {
            'number':    player_num_int,
            'name':      f"#{player_num}",
            'last_name': f"#{player_num}",
            'first_name': '',
            'is_libero':  False,
            'position_code': '',
        })

        eval_code  = parsed['evaluation']
        combo_code = parsed['combo_code']

        action = {
            # Identity
            'seq':          idx + 1,
            'team_side':    parsed['team_side'],
            'player_num':   player_num_str,
            'player_name':  player_info.get('name', ''),
            'player_last':  player_info.get('last_name', ''),
            'player_first': player_info.get('first_name', ''),
            'is_libero':    player_info.get('is_libero', False),
            # Skill
            'skill_code':   skill_code,
            'skill_name':   SKILLS.get(skill_code, skill_code),
            'technique':    parsed['technique'],
            # Quality
            'evaluation':      eval_code,
            'evaluation_name': EVALUATIONS.get(eval_code, eval_code),
            'eval_score':      EVAL_SCORE.get(eval_code),
            # Attack detail
            'combo_code':    combo_code,
            'combo_name':    attack_combos.get(combo_code) if combo_code else None,
            'start_zone':    parsed.get('start_zone'),
            'end_zone':      parsed.get('end_zone'),
            'direction':     parsed.get('direction'),
            'num_blockers':  parsed.get('num_blockers'),
            # Setter / rotation (placeholder — populated as coding matures)
            'setter_call_code':  None,   # TODO: extract from code flags when available
            'setter_call_name':  None,
            'rotation':          None,   # TODO: extract from rotation markers
            # Raw flags for future decoding
            '_flag1': side_flag_1 or None,
            '_flag2': side_flag_2 or None,
            # Timing
            'timestamp': timestamp,
            # Rally grouping (fields[12] in DVW — unique per rally within the file)
            'rally_id': fields[12].strip() if len(fields) > 12 else None,
        }

        actions.append(action)

    # ── For match files: keep only LUC's actions ────────────────────────────
    # We don't aggregate opponent stats in the dashboard. Practice files are
    # always LUC-only (home side), so this filter is a no-op for them.
    if inferred_type == 'match' and not is_scout:
        actions = [a for a in actions if a['team_side'] == luc_side]

    # ── Derive opponent from team names if filename didn't supply it ─────────
    if not opponent and inferred_type in ('match', 'scout'):
        if luc_side == 'home' and away_team:
            opponent = away_team
        elif luc_side == 'away' and home_team:
            opponent = home_team

    session = {
        # Identity
        'file':            filename,
        'filepath':        filepath,
        'season':          season_label,
        'coding_version':  coding_version,
        'date':            match_date,
        'time':            match_time,
        'type':            inferred_type,   # practice | match | scout
        'is_scout':        is_scout or inferred_type == 'scout',
        # Team info
        'team':            team_name,
        'home_team':       home_team,
        'away_team':       away_team,
        'opponent':        opponent,
        'luc_side':        luc_side,        # 'home' or 'away'
        # Counts
        'player_count':    len(players),
        'action_count':    len(actions),
        # Lookup tables (embedded so each session is self-contained)
        'players':         players,
        'attack_combos':   attack_combos,
        'setter_calls':    setter_calls,
        # Actions
        'actions': actions,
    }

    return session


# ─── Dataset accumulation ──────────────────────────────────────────────────────

def load_dataset(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'schema_version': '1.1',
        'generated':      datetime.utcnow().isoformat() + 'Z',
        'loyola_team':    'Loyola University Chicago',
        'sessions':       [],
        'scout_sessions': [],
        'players':        {},    # merged roster across all Loyola sessions
    }


def merge_session(dataset, session, is_scout=False):
    """
    Add a parsed session to the dataset.

    Duplicate detection:
      - For Loyola files: deduplicate by (date + session_type) or filename.
      - For scout files: allow same team on different dates; deduplicate by
        (opponent + date) so the same game isn't double-counted if re-exported.
    """
    target_list = dataset['scout_sessions'] if is_scout else dataset['sessions']

    if is_scout:
        # Dedup by opponent + date
        key = (session.get('opponent', ''), session.get('date', ''))
        existing_keys = {
            (s.get('opponent', ''), s.get('date', ''))
            for s in target_list
        }
        if key[0] and key[1] and key in existing_keys:
            print(f"  [skip-dup] {session['file']} ({key})", file=sys.stderr)
            return False
    else:
        # Dedup by (season + filename) — same filename can appear across different seasons
        existing_keys = {(s['season'], s['file']) for s in target_list}
        key = (session['season'], session['file'])
        if key in existing_keys:
            print(f"  [skip] {session['file']} ({session['season']}) already in dataset", file=sys.stderr)
            return False

    # Merge Loyola player roster into top-level players dict
    if not is_scout:
        for num, info in session.get('players', {}).items():
            existing = dataset['players'].get(num, {})
            # Keep most complete version (prefer entries with a real name)
            if not existing.get('name') or info.get('name', '').startswith('#') is False:
                dataset['players'][num] = info

    target_list.append(session)
    return True


def save_dataset(dataset, path):
    dataset['generated'] = datetime.utcnow().isoformat() + 'Z'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Parse .dvw files into structured JSON for the volleyball dashboard.'
    )
    ap.add_argument('files', nargs='+',
                    help='.dvw file(s) or glob patterns (e.g. "Fall 2025/*.dvw")')
    ap.add_argument('--season',  help='Season label override (e.g. "Spring 2026")')
    ap.add_argument('--type', dest='session_type', choices=['practice', 'match', 'scout'],
                    help='Session type override')
    ap.add_argument('--scout', action='store_true',
                    help='Mark all files in this batch as opponent scouting data')
    ap.add_argument('--dataset', default='volleyball_data.json',
                    help='Accumulated dataset JSON file (default: volleyball_data.json)')
    ap.add_argument('--summary', action='store_true',
                    help='Print a human-readable summary for each parsed file')
    ap.add_argument('--no-save', action='store_true',
                    help='Parse without writing to dataset (dry run)')
    args = ap.parse_args()

    # Expand globs
    all_files = []
    for pattern in args.files:
        expanded = sorted(glob.glob(pattern))
        all_files.extend(expanded if expanded else [pattern])

    dataset = load_dataset(args.dataset)
    added = skipped = errors = 0

    for dvw_path in all_files:
        if not os.path.exists(dvw_path):
            print(f"  [missing] {dvw_path}", file=sys.stderr)
            errors += 1
            continue

        fname = os.path.basename(dvw_path)
        print(f"  Parsing {fname} ...", file=sys.stderr, end=' ')

        try:
            session = parse_dvw(
                dvw_path,
                season=args.season,
                session_type=args.session_type,
                is_scout=args.scout,
            )
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += 1
            continue

        if args.summary:
            _print_summary(session)
        else:
            is_scout_flag = args.scout or session.get('is_scout', False)
            label = f"[{session['type']}] {session['date'] or '?'}"
            if session.get('opponent'):
                label += f" vs {session['opponent']}"
            print(f"{label} — {session['action_count']} actions", file=sys.stderr)

        if not args.no_save:
            is_scout_flag = args.scout or session.get('is_scout', False)
            if merge_session(dataset, session, is_scout=is_scout_flag):
                added += 1
            else:
                skipped += 1

    if not args.no_save:
        if added > 0:
            save_dataset(dataset, args.dataset)
        total_s = len(dataset['sessions'])
        total_sc = len(dataset['scout_sessions'])
        print(
            f"\nDone — added {added}, skipped {skipped}, errors {errors}",
            file=sys.stderr,
        )
        print(
            f"Dataset: {total_s} Loyola sessions, {total_sc} scout sessions → {args.dataset}",
            file=sys.stderr,
        )
    else:
        print(f"\n[dry-run] Parsed {len(all_files)} files, {errors} errors", file=sys.stderr)


def _print_summary(session):
    actions = session['actions']
    skill_counts = Counter(a['skill_name'] for a in actions)
    eval_counts  = Counter(a['evaluation_name'] for a in actions)
    attacks = [a for a in actions if a['skill_code'] == 'A']

    print(f"\n{'─'*60}", file=sys.stderr)
    print(f"  {session['file']} | {session['date']} | {session['season']} | {session['type']}", file=sys.stderr)
    print(f"  Players: {session['player_count']}  Actions: {session['action_count']}  Coding: {session['coding_version']}", file=sys.stderr)
    skill_str = ' | '.join(f"{k}:{v}" for k, v in sorted(skill_counts.items(), key=lambda x: -x[1]))
    print(f"  Skills : {skill_str}", file=sys.stderr)
    eval_str = ' | '.join(f"{k}:{v}" for k, v in sorted(eval_counts.items(), key=lambda x: -x[1]))
    print(f"  Evals  : {eval_str}", file=sys.stderr)
    if attacks:
        top = Counter(a['player_name'] for a in attacks).most_common(5)
        atk_str = ' | '.join(f"{n}:{c}" for n, c in top)
        print(f"  Atk top: {atk_str}", file=sys.stderr)


if __name__ == '__main__':
    main()
