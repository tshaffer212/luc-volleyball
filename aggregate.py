#!/usr/bin/env python3
"""
Aggregates volleyball_data.json into compact dashboard_data.json.

Key rule for match files:
  - Determine which side is Loyola by checking home_team string
  - If 'Loyola' in home_team → count team_side='home' actions
  - If 'Loyola' in away_team → count team_side='away' actions
  - Practice files: count ALL actions (both sides are Loyola)
  - Scout files: both sides counted for opponent analysis
"""

import json, sys, os
from collections import defaultdict, Counter
from difflib import SequenceMatcher

SKILLS = ['S','R','E','A','B','D','F','O']
EVALS  = ['#','+','/','-','=','!']
LOYOLA_KEYWORDS = ['loyola', 'luc', 'lmv']

# ── Manual name overrides ─────────────────────────────────────────────────────
# Use when fuzzy matching picks the wrong (more frequent but misspelled) variant.
# Format: { jersey_number_string: 'Correct Full Name' }
# The correct name will be used regardless of which spelling appears most in the data.
NAME_OVERRIDES = {
    # '16': 'Alex Smith Van Oyen',   # uncomment if Smith is correct, not Smits
}

def is_loyola_team(team_str):
    if not team_str:
        return False
    t = team_str.lower()
    return any(kw in t for kw in LOYOLA_KEYWORDS)

def loyola_side_for_session(session):
    """Returns 'home', 'away', or 'both' for practice."""
    if session.get('type') != 'match':
        return 'both'
    if is_loyola_team(session.get('home_team', '')):
        return 'home'
    if is_loyola_team(session.get('away_team', '')):
        return 'away'
    # Fallback: assume home
    return 'home'

def empty_skill():
    return {'attempts': 0, 'evals': {e: 0 for e in EVALS}, 'combos': {}}

def empty_zone():
    return {'attempts': 0, 'evals': {e: 0 for e in EVALS}}

def add_action(skill_stats, action, zone_stats=None):
    s = action.get('skill_code','')
    if s not in ('S','R','E','A','B','D','F','O'):
        return
    s_key = 'F' if s == 'O' else s   # merge O→F
    if s_key not in skill_stats:
        skill_stats[s_key] = empty_skill()
    sk = skill_stats[s_key]
    sk['attempts'] += 1
    ev = action.get('evaluation', '~')
    sk['evals'][ev] = sk['evals'].get(ev, 0) + 1
    if s in ('A', 'E') and action.get('combo_code'):
        cc = action['combo_code']
        cn = action.get('combo_name') or cc
        if cc not in sk['combos']:
            sk['combos'][cc] = {'name': cn, 'attempts': 0, 'evals': {e: 0 for e in EVALS}}
        sk['combos'][cc]['attempts'] += 1
        sk['combos'][cc]['evals'][ev] = sk['combos'][cc]['evals'].get(ev, 0) + 1
    # Track receive zones (end_zone field stores serve receive zone)
    if s == 'R' and zone_stats is not None:
        zone = action.get('end_zone')
        if zone:
            zk = str(zone)
            if zk not in zone_stats:
                zone_stats[zk] = empty_zone()
            zone_stats[zk]['attempts'] += 1
            zone_stats[zk]['evals'][ev] = zone_stats[zk]['evals'].get(ev, 0) + 1

def derive_zones(zone_stats):
    """Add rcv AVG, GP%, err% to each zone bucket (in-place)."""
    for zs in zone_stats.values():
        att = zs['attempts']
        ev  = zs['evals']
        if att:
            avg = (3*(ev.get('#',0)) + 2*(ev.get('+',0)) + (ev.get('-',0)) + 0.5*(ev.get('/',0))) / att
            gp  = (ev.get('#',0) + ev.get('+',0) + ev.get('/',0)) / att * 100
            err = ev.get('=',0) / att * 100
        else:
            avg = gp = err = 0
        zs['avg']     = round(avg, 3)
        zs['gp_pct']  = round(gp, 1)
        zs['err_pct'] = round(err, 1)

VALID_K_CODES = {'K1', 'K2', 'K7'}

def compute_call_attacks(actions, loyola_side):
    """
    Per rally: when a setter E action has a K-code (K1/K2/K7),
    find the next A action on the same team side and record the result.
    Returns: { k_code: { atk_combo: {attempts, kills, errors} } }
    """
    rallies, current = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and current:
            rallies.append(current)
            current = []
        current.append(a)
    if current:
        rallies.append(current)

    call_atk = defaultdict(lambda: defaultdict(lambda: {'attempts': 0, 'kills': 0, 'errors': 0}))

    for rally in rallies:
        for i, a in enumerate(rally):
            if a.get('skill_code') != 'E':
                continue
            k_code = a.get('combo_code') or ''
            if k_code not in VALID_K_CODES:
                continue
            e_side = a.get('team_side')
            if loyola_side != 'both' and e_side != loyola_side:
                continue
            # Find the next attack on the same side in this rally
            next_atk = next(
                (b for b in rally[i+1:]
                 if b.get('skill_code') == 'A' and b.get('team_side') == e_side),
                None
            )
            if not next_atk:
                continue
            atk_cc = next_atk.get('combo_code') or ''
            if not atk_cc:
                continue
            ev = next_atk.get('evaluation') or ''
            cv = call_atk[k_code][atk_cc]
            cv['attempts'] += 1
            if ev == '#':
                cv['kills'] += 1
            elif ev == '=':
                cv['errors'] += 1

    return {kc: dict(combos) for kc, combos in call_atk.items()}


def compute_rally_sequences(actions, loyola_side):
    """
    Split actions into rallies (a new rally begins at each Serve action) and compute:

      1. Per-player dig→kill conversion
         A dig "converts" only if the SAME side that dug the ball kills in that rally.
         In practice (loyola_side='both') both sides are Loyola, so we track by side
         to avoid crediting a dig on side A when side B kills.

      2. Team FBSO (First Ball Sideout)
         For each rally where Loyola is receiving (opponent served), check whether
         the receiving side's FIRST attack in that rally is a kill.
         In practice every rally has a receiving side — both count since both = Loyola.

    Returns:
        player_dig : {pnum: {'dig_rallies': int, 'dig_kill_rallies': int}}
        fbso       : {'rcv_rallies': int, 'fbso_kills': int}
    """
    # ── Split into rallies ───────────────────────────────────────────────────────
    rallies, current = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and current:
            rallies.append(current)
            current = []
        current.append(a)
    if current:
        rallies.append(current)

    player_dig = {}   # pnum → {dig_rallies, dig_kill_rallies}
    fbso = {'rcv_rallies': 0, 'fbso_kills': 0}

    for rally in rallies:
        if not rally:
            continue

        # Serving side = team of the first action (the serve itself)
        serve_side = rally[0].get('team_side')   # 'home' | 'away'
        rcv_side   = 'away' if serve_side == 'home' else 'home'

        # Which sides scored a kill in this rally?
        sides_killed = {
            a.get('team_side')
            for a in rally
            if a.get('skill_code') == 'A' and a.get('evaluation') == '#'
        }

        # ── Dig → Kill ──────────────────────────────────────────────────────────
        for a in rally:
            if a.get('skill_code') != 'D':
                continue
            a_side = a.get('team_side')
            # Match mode: only Loyola digs count
            if loyola_side != 'both' and a_side != loyola_side:
                continue
            pnum = a.get('player_num', '')
            if not pnum:
                continue
            if pnum not in player_dig:
                player_dig[pnum] = {'dig_rallies': 0, 'dig_kill_rallies': 0}
            player_dig[pnum]['dig_rallies'] += 1
            # Convert only if the SAME side that dug also kills
            if a_side in sides_killed:
                player_dig[pnum]['dig_kill_rallies'] += 1

        # ── FBSO ────────────────────────────────────────────────────────────────
        # In match mode skip rallies where Loyola is serving (not receiving)
        if loyola_side != 'both' and rcv_side != loyola_side:
            continue

        fbso['rcv_rallies'] += 1

        # First attack by the receiving side in this rally
        first_atk = next(
            (a for a in rally
             if a.get('team_side') == rcv_side and a.get('skill_code') == 'A'),
            None
        )
        if first_atk and first_atk.get('evaluation') == '#':
            fbso['fbso_kills'] += 1

    return player_dig, fbso


def derive(skill_stats):
    """Add kill%, error%, pos%, efficiency to each skill (in-place)."""
    for st in skill_stats.values():
        att = st['attempts']
        ev  = st['evals']
        kills  = ev.get('#', 0)
        errors = ev.get('=', 0)
        pos    = kills + ev.get('+', 0) + ev.get('/', 0)
        st['kill_pct']   = round(kills  / att * 100, 1) if att else 0
        st['error_pct']  = round(errors / att * 100, 1) if att else 0
        st['pos_pct']    = round(pos    / att * 100, 1) if att else 0
        st['efficiency'] = round((kills - errors) / att * 100, 1) if att else 0
        for cs in st.get('combos', {}).values():
            ca = cs['attempts']
            cs['kill_pct']   = round(cs['evals'].get('#',0) / ca * 100, 1) if ca else 0
            cs['error_pct']  = round(cs['evals'].get('=',0) / ca * 100, 1) if ca else 0
            cs['efficiency'] = round((cs['evals'].get('#',0)-cs['evals'].get('=',0))/ca*100,1) if ca else 0

def main(raw_path, out_path):
    print(f"Loading {raw_path} ...", file=sys.stderr)
    with open(raw_path) as f:
        raw = json.load(f)

    sessions_raw = raw.get('sessions', [])
    scouts_raw   = raw.get('scout_sessions', [])

    # ── Per-season player rosters ─────────────────────────────────────────────
    # Build season-specific rosters from practice files.
    # Same jersey number can be a different person in a different season.
    players_by_season = defaultdict(dict)  # season -> num -> player_info
    for s in sessions_raw:
        if s.get('type') != 'practice':
            continue
        season = s['season']
        for num, p in s.get('players', {}).items():
            existing = players_by_season[season].get(num)
            name = p.get('name', f'#{num}')
            if not existing or (not name.startswith('#') and existing.get('name','').startswith('#')):
                players_by_season[season][num] = {
                    'number':        p.get('number', int(num)),
                    'name':          name,
                    'last_name':     p.get('last_name', ''),
                    'first_name':    p.get('first_name', ''),
                    'is_libero':     p.get('is_libero', False),
                    'position_code': p.get('position_code', ''),
                }

    # ── Normalize similar names for the same jersey number across seasons ────
    # Catches typos like "Wil Hatch" / "Will Hatch" or "Smits" / "Smith".
    # Collects all real name variants per number, clusters by similarity,
    # then picks the most-used spelling as canonical.
    num_name_counts = defaultdict(Counter)  # num -> {name: count}
    for season, roster in players_by_season.items():
        for num, p in roster.items():
            name = p['name']
            if not name.startswith('#'):
                num_name_counts[num][name] += 1

    name_canon = {}  # (num, variant) -> canonical
    for num, counts in num_name_counts.items():
        names = list(counts.keys())
        if len(names) <= 1:
            continue
        assigned = set()
        for i, a in enumerate(names):
            if a in assigned:
                continue
            group = [a]
            assigned.add(a)
            for b in names:
                if b not in assigned:
                    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
                    if ratio >= 0.82:
                        group.append(b)
                        assigned.add(b)
            if len(group) > 1:
                canonical = max(group, key=lambda n: counts[n])
                for variant in group:
                    if variant != canonical:
                        name_canon[(num, variant)] = canonical
                        print(f"  Name fix: #{num} '{variant}' → '{canonical}'", file=sys.stderr)

    # Apply manual overrides — force correct spelling regardless of frequency
    for num, correct_name in NAME_OVERRIDES.items():
        for season, roster in players_by_season.items():
            if num in roster:
                old = roster[num]['name']
                if old != correct_name:
                    roster[num]['name'] = correct_name
                    print(f"  Name override: #{num} '{old}' → '{correct_name}'", file=sys.stderr)
                # Also update name_canon so any variant maps to the override
                name_canon[(num, old)] = correct_name

    # Apply normalized names back into players_by_season
    for season, roster in players_by_season.items():
        for num, p in roster.items():
            canon = name_canon.get((num, p['name']))
            if canon:
                p['name'] = canon

    # ── Merged global roster (for backward compat / cross-season lookups) ────
    # When a number appears in multiple seasons with different names, the
    # season-specific lookup (players_by_season) takes priority in the dashboard.
    players = {}
    for season, roster in players_by_season.items():
        for num, p in roster.items():
            existing = players.get(num)
            if not existing or (not p['name'].startswith('#') and existing.get('name','').startswith('#')):
                players[num] = p

    loyola_player_nums = set(players.keys())

    # ── Rally sequences (dig→kill + FBSO + call attacks) ────────────────────
    print("Computing rally sequences ...", file=sys.stderr)
    session_rally = {}   # sid → {'dig': {...}, 'fbso': {...}}
    # call_attacks_by_season: season → k_code → atk_combo → {attempts, kills, errors}
    call_attacks_by_season = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'attempts':0,'kills':0,'errors':0})))

    for s in sessions_raw:
        sid    = f"{s['season']}|{s['file']}"
        season = s['season']
        ls     = loyola_side_for_session(s)
        acts   = s.get('actions', [])
        dig, fbso = compute_rally_sequences(acts, ls)
        session_rally[sid] = {'dig': dig, 'fbso': fbso}
        # Accumulate setter-call → attack outcome per season
        ca = compute_call_attacks(acts, ls)
        for k_code, combos in ca.items():
            for atk_cc, cv in combos.items():
                acc = call_attacks_by_season[season][k_code][atk_cc]
                acc['attempts'] += cv['attempts']
                acc['kills']    += cv['kills']
                acc['errors']   += cv['errors']

    # ── Session index ─────────────────────────────────────────────────────────
    sessions_index = []
    for s in sessions_raw:
        sid  = f"{s['season']}|{s['file']}"
        fbso = session_rally[sid]['fbso']
        sessions_index.append({
            'id':             sid,
            'file':           s['file'],
            'date':           s.get('date'),
            'season':         s.get('season'),
            'type':           s.get('type'),
            'opponent':       s.get('opponent'),
            'home_team':      s.get('home_team'),
            'away_team':      s.get('away_team'),
            'action_count':   s.get('action_count', 0),
            'coding_version': s.get('coding_version', 'basic'),
            'loyola_side':    loyola_side_for_session(s),
            'fbso_rcv':       fbso['rcv_rallies'],
            'fbso_kills':     fbso['fbso_kills'],
        })

    # ── Aggregation accumulators ──────────────────────────────────────────────
    season_stats   = defaultdict(lambda: {
        'sessions':0,'practice_sessions':0,'match_sessions':0,'actions':0,
        'skills':{},'rcv_zones':{},'fbso_rcv':0,'fbso_kills':0,
    })
    player_session = defaultdict(lambda: {'actions':0,'skills':{},'rcv_zones':{}})
    player_season  = defaultdict(lambda: {'sessions':set(),'actions':0,'skills':{},'rcv_zones':{}})

    print("Aggregating Loyola sessions ...", file=sys.stderr)

    # Pre-compute dominant unnamed players per session.
    # If one nameless player accounts for >50% of session actions, keep them
    # (this handles sessions where no jersey numbers were set up — every action
    # goes under one placeholder number by design).
    for s in sessions_raw:
        actions = s.get('actions', [])
        total   = len(actions)
        keep    = set()
        if total > 0:
            from collections import Counter as _C
            counts = _C(a.get('player_num','') for a in actions)
            for pnum_k, cnt in counts.items():
                pname = s.get('players',{}).get(pnum_k, {}).get('name','')
                if (not pname or pname.startswith('#')) and cnt / total >= 0.50:
                    keep.add(pnum_k)
        s['_keep_unnamed'] = keep

    for s in sessions_raw:
        sid    = f"{s['season']}|{s['file']}"
        season = s['season']
        stype  = s['type']

        ss = season_stats[season]
        ss['sessions'] += 1
        if stype == 'practice': ss['practice_sessions'] += 1
        if stype == 'match':    ss['match_sessions'] += 1

        loyola_side   = loyola_side_for_session(s)
        keep_unnamed  = s.get('_keep_unnamed', set())

        for action in s.get('actions', []):
            pnum = action.get('player_num', '')
            if not pnum:
                continue

            # Skip players with no real name (unless they're the dominant placeholder)
            pname = action.get('player_name', '')
            if not pname or pname.startswith('#'):
                if pnum not in keep_unnamed:
                    continue

            # For match files: only count Loyola's side
            if stype == 'match':
                action_side = action.get('team_side')
                if loyola_side != 'both' and action_side != loyola_side:
                    continue
                # Also skip players not on Loyola roster (safety net for jersey collisions)
                if pnum not in loyola_player_nums:
                    continue

            ss['actions'] += 1
            add_action(ss['skills'], action, ss['rcv_zones'])

            psk = player_session[(pnum, sid)]
            psk['actions'] += 1
            add_action(psk['skills'], action, psk['rcv_zones'])

            pse = player_season[(pnum, season)]
            pse['sessions'].add(sid)
            pse['actions'] += 1
            add_action(pse['skills'], action, pse['rcv_zones'])

        # ── Season FBSO ───────────────────────────────────────────────────────
        fbso = session_rally[sid]['fbso']
        ss['fbso_rcv']   += fbso['rcv_rallies']
        ss['fbso_kills'] += fbso['fbso_kills']

        # ── Per-player dig conversion (rally-level) ───────────────────────────
        for pnum, dstats in session_rally[sid]['dig'].items():
            # Apply same name/roster filters as the per-action loop
            pname = s.get('players', {}).get(pnum, {}).get('name', '')
            if not pname or pname.startswith('#'):
                if pnum not in keep_unnamed:
                    continue
            if stype == 'match' and loyola_side != 'both' and pnum not in loyola_player_nums:
                continue

            psk = player_session[(pnum, sid)]
            psk['dig_kill_rallies']  = psk.get('dig_kill_rallies',  0) + dstats['dig_kill_rallies']
            psk['dig_total_rallies'] = psk.get('dig_total_rallies', 0) + dstats['dig_rallies']

            pse = player_season[(pnum, season)]
            pse['dig_kill_rallies']  = pse.get('dig_kill_rallies',  0) + dstats['dig_kill_rallies']
            pse['dig_total_rallies'] = pse.get('dig_total_rallies', 0) + dstats['dig_rallies']

    # Derive metrics
    for ss in season_stats.values():
        derive(ss['skills'])
        derive_zones(ss['rcv_zones'])
    for ps in player_session.values():
        derive(ps['skills'])
        derive_zones(ps['rcv_zones'])
    for pse in player_season.values():
        pse['sessions'] = len(pse['sessions'])
        derive(pse['skills'])
        derive_zones(pse['rcv_zones'])

    # Serialize dicts
    ps_out  = {f"{pn}|{sid}": {'player_num':pn,'session_id':sid,**v}
               for (pn,sid),v in player_session.items()}
    pse_out = {f"{pn}|{se}": {'player_num':pn,'season':se,**v}
               for (pn,se),v in player_season.items()}

    # ── Scout aggregation ─────────────────────────────────────────────────────
    scout_index    = []
    opponent_stats = defaultdict(lambda: {'files':0,'actions':0,'skills':{}})

    print("Aggregating scout sessions ...", file=sys.stderr)
    for s in scouts_raw:
        opp = s.get('opponent') or 'Unknown'
        scout_index.append({
            'file':         s['file'],
            'date':         s.get('date'),
            'season':       s.get('season'),
            'opponent':     opp,
            'home_team':    s.get('home_team'),
            'away_team':    s.get('away_team'),
            'action_count': s.get('action_count', 0),
        })
        os_ = opponent_stats[opp]
        os_['files']   += 1
        os_['actions'] += s.get('action_count', 0)
        for action in s.get('actions', []):
            add_action(os_['skills'], action)

    for os_ in opponent_stats.values():
        derive(os_['skills'])

    # ── Output ────────────────────────────────────────────────────────────────
    total_loyola_actions = sum(v['actions'] for v in season_stats.values())

    output = {
        'meta': {
            'team':           'Loyola University Chicago',
            'abbreviation':   'LUC',
            'colors':         {'primary': '#8D0034', 'secondary': '#FEBC18'},
            'seasons':        sorted(season_stats.keys()),
            'total_sessions': len(sessions_raw),
            'total_actions':  total_loyola_actions,
            'total_players':  len(players),
            'scout_sessions': len(scouts_raw),
        },
        'players':           players,
        'players_by_season': {s: dict(r) for s, r in players_by_season.items()},
        'sessions':          sessions_index,
        'season_stats':   dict(season_stats),
        'player_session': ps_out,
        'player_season':  pse_out,
        'scout_index':    scout_index,
        'opponent_stats': dict(opponent_stats),
        'call_attacks': {
            season: {
                k: {cc: dict(cv) for cc, cv in combos.items()}
                for k, combos in kcodes.items()
            }
            for season, kcodes in call_attacks_by_season.items()
        },
    }

    print(f"Writing {out_path} ...", file=sys.stderr)
    with open(out_path, 'w') as f:
        json.dump(output, f, separators=(',',':'))

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    total_unique_players = len(set(
        f"{num}|{season}" for season, roster in players_by_season.items() for num in roster
    ))
    print(f"Done — {size_mb:.1f} MB | {len(players)} unique numbers, {total_unique_players} player-seasons | {total_loyola_actions:,} actions", file=sys.stderr)

    # Verification printout
    print("\nMatch file side detection:", file=sys.stderr)
    for s in sessions_index:
        if s['type'] == 'match':
            print(f"  {s['file']}: Loyola={s['loyola_side']} | home={s['home_team']} | away={s['away_team']}", file=sys.stderr)

if __name__ == '__main__':
    raw = sys.argv[1] if len(sys.argv) > 1 else 'volleyball_data.json'
    out = sys.argv[2] if len(sys.argv) > 2 else 'dashboard_data.json'
    main(raw, out)
