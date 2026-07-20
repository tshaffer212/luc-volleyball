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

def rcv_points(ev_code, is_match=False):
    """Return pass-rating points for an evaluation code.
    Match (4-pt):    #=4, +=3, !=2, -=1, /=0.5, ==0
    Practice (3-pt): #=3, +=2, -=1, /=0.5, ==0  (! not used)
    """
    if is_match:
        return {'#': 4, '+': 3, '!': 2, '-': 1, '/': 0.5}.get(ev_code, 0)
    else:
        return {'#': 3, '+': 2, '-': 1, '/': 0.5}.get(ev_code, 0)

def rcv_category(ev_code, is_match=False):
    """Map an eval code to a pass category.
    Match:    #=perf(4pt), +=good(3pt), !=med(2pt), -=oos(1pt), /=over, ==err
    Practice: #=perf(3pt), +=good(2pt), -=oos(1pt), /=over, ==err  (no med)
    """
    if is_match:
        return {'#': 'perf', '+': 'good', '!': 'med', '-': 'oos', '/': 'over', '=': 'err'}.get(ev_code, 'err')
    else:
        return {'#': 'perf', '+': 'good', '-': 'oos', '/': 'over', '=': 'err'}.get(ev_code, 'err')

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
    # Track serve by technique: SQ = topspin, SM = float
    if s == 'S':
        tech = action.get('technique', '')
        if tech in ('Q', 'M'):
            t_key = 'S' + tech  # 'SQ' or 'SM'
            if t_key not in skill_stats:
                skill_stats[t_key] = empty_skill()
            skill_stats[t_key]['attempts'] += 1
            skill_stats[t_key]['evals'][ev] = skill_stats[t_key]['evals'].get(ev, 0) + 1
    # Track receive zones (end_zone field stores serve receive zone)
    if s == 'R' and zone_stats is not None:
        zone = action.get('end_zone')
        if zone:
            zk = str(zone)
            if zk not in zone_stats:
                zone_stats[zk] = empty_zone()
            zone_stats[zk]['attempts'] += 1
            zone_stats[zk]['evals'][ev] = zone_stats[zk]['evals'].get(ev, 0) + 1

def derive_zones(zone_stats, is_match=False):
    """Add rcv AVG, GP%, err% to each zone bucket (in-place)."""
    for zs in zone_stats.values():
        att = zs['attempts']
        ev  = zs['evals']
        if att:
            if is_match:
                avg  = (4*(ev.get('#',0)) + 3*(ev.get('+',0)) + 2*(ev.get('!',0)) + (ev.get('-',0)) + 0.5*(ev.get('/',0))) / att
                good = ev.get('#',0) + ev.get('+',0)
                med  = ev.get('!',0)
            else:
                avg  = (3*(ev.get('#',0)) + 2*(ev.get('+',0)) + (ev.get('-',0)) + 0.5*(ev.get('/',0))) / att
                good = ev.get('#',0)
                med  = ev.get('+',0)
            oos  = ev.get('-',0)
            over = ev.get('/',0)
            err  = ev.get('=',0)
            gp   = good / att * 100
            med_pct  = med  / att * 100
            oos_pct  = oos  / att * 100
            over_pct = over / att * 100
            err_pct  = err  / att * 100
        else:
            avg = gp = med_pct = oos_pct = over_pct = err_pct = 0
        zs['avg']      = round(avg, 3)
        zs['gp_pct']   = round(gp, 1)
        zs['med_pct']  = round(med_pct, 1)
        zs['oos_pct']  = round(oos_pct, 1)
        zs['over_pct'] = round(over_pct, 1)
        zs['err_pct']  = round(err_pct, 1)

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

    call_atk = defaultdict(lambda: defaultdict(lambda: {'attempts': 0, 'kills': 0, 'errors': 0, 'blocks': 0}))

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
            elif ev == '/':
                cv['blocks'] += 1

    return {kc: dict(combos) for kc, combos in call_atk.items()}


def compute_rally_sequences(actions, loyola_side):
    """
    Split actions into rallies and compute:

      1. Per-player dig→kill conversion
         A dig "converts" only if the SAME side that dug the ball kills in that rally.
         In practice (loyola_side='both') both sides are Loyola, so we track by side
         to avoid crediting a dig on side A when side B kills.

      2. Team FBSO (First Ball Sideout)
         For each rally where Loyola is receiving (opponent served), check whether
         the receiving side's FIRST attack in that rally is a kill.

    Rally splitting strategy:
      - If actions have rally_id fields (Volleymetrics match files), group by rally_id.
        This avoids missing opponent serves (which are filtered out for match files).
        In these files, LUC receiving rallies are identified by the presence of a
        Reception action (skill_code=='R') anywhere in the rally.
      - Otherwise (practice files), split on each Serve action as before.

    Returns:
        player_dig : {pnum: {'dig_rallies': int, 'dig_kill_rallies': int}}
        fbso       : {'rcv_rallies': int, 'fbso_kills': int}
    """
    # ── Decide split strategy ────────────────────────────────────────────────────
    # Match files (loyola_side != 'both') only contain LUC's actions — opponent
    # serves are absent, so serve-based splitting fails.  Use linear scan instead.
    match_mode = (loyola_side != 'both')

    if not match_mode:
        # Practice mode: split into rallies on each Serve action
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

    if match_mode:
        # ── Match mode: linear scan over LUC-only actions ────────────────────────
        # rally_id increments per action (not per rally), so we can't group by it.
        # Instead we scan linearly:
        # - Each Reception (R) marks start of a LUC-receiving possession.
        # - Scan forward from that R to find the first Attack (A) before the next
        #   R or S (which would start a new possession).
        # - Dig→kill: a Dig followed (before the next S/R) by a LUC attack kill.
        n = len(actions)
        for i, a in enumerate(actions):
            skill = a.get('skill_code')

            # ── Dig → Kill (match) ───────────────────────────────────────────
            if skill == 'D':
                pnum = a.get('player_num', '')
                if pnum:
                    if pnum not in player_dig:
                        player_dig[pnum] = {'dig_rallies': 0, 'dig_kill_rallies': 0}
                    player_dig[pnum]['dig_rallies'] += 1
                    # Look forward for a LUC kill before next possession boundary
                    for j in range(i+1, n):
                        ns = actions[j].get('skill_code')
                        if ns in ('S', 'R'):
                            break
                        if ns == 'A' and actions[j].get('evaluation') == '#':
                            player_dig[pnum]['dig_kill_rallies'] += 1
                            break

            # ── FBSO (match) ─────────────────────────────────────────────────
            elif skill == 'R':
                fbso['rcv_rallies'] += 1
                # Find first Attack before next R or S
                for j in range(i+1, n):
                    ns = actions[j].get('skill_code')
                    if ns in ('S', 'R'):
                        break
                    if ns == 'A':
                        if actions[j].get('evaluation') == '#':
                            fbso['fbso_kills'] += 1
                        break  # only the FIRST attack counts

    else:
        # ── Practice mode: serve-split rallies ───────────────────────────────────
        for rally in rallies:
            if not rally:
                continue

            serve_side = rally[0].get('team_side')   # 'home' | 'away'
            rcv_side   = 'away' if serve_side == 'home' else 'home'

            sides_killed = {
                a.get('team_side')
                for a in rally
                if a.get('skill_code') == 'A' and a.get('evaluation') == '#'
            }

            # ── Dig → Kill ──────────────────────────────────────────────────
            for a in rally:
                if a.get('skill_code') != 'D':
                    continue
                a_side = a.get('team_side')
                if loyola_side != 'both' and a_side != loyola_side:
                    continue
                pnum = a.get('player_num', '')
                if not pnum:
                    continue
                if pnum not in player_dig:
                    player_dig[pnum] = {'dig_rallies': 0, 'dig_kill_rallies': 0}
                player_dig[pnum]['dig_rallies'] += 1
                if a_side in sides_killed:
                    player_dig[pnum]['dig_kill_rallies'] += 1

            # ── FBSO ────────────────────────────────────────────────────────
            if loyola_side != 'both' and rcv_side != loyola_side:
                continue

            fbso['rcv_rallies'] += 1

            first_atk = next(
                (a for a in rally
                 if a.get('team_side') == rcv_side and a.get('skill_code') == 'A'),
                None
            )
            if first_atk and first_atk.get('evaluation') == '#':
                fbso['fbso_kills'] += 1

    return player_dig, fbso


def derive(skill_stats):
    """Add kill%, error%, pos%, efficiency to each skill (in-place).
    For attacks (skill 'A'), efficiency = (K - E - Blocked) / Att * 100,
    where blocked = evals['/'] (A/ = attack blocked by opponent).
    """
    for sk, st in skill_stats.items():
        att = st['attempts']
        ev  = st['evals']
        kills   = ev.get('#', 0)
        errors  = ev.get('=', 0)
        blocked = ev.get('/', 0) if sk == 'A' else 0   # A/ = blocked attack
        pos     = kills + ev.get('+', 0) + ev.get('/', 0)
        st['kill_pct']   = round(kills  / att * 100, 1) if att else 0
        st['error_pct']  = round(errors / att * 100, 1) if att else 0
        st['pos_pct']    = round(pos    / att * 100, 1) if att else 0
        st['efficiency'] = round((kills - errors - blocked) / att * 100, 1) if att else 0
        is_atk = (sk == 'A')
        for cs in st.get('combos', {}).values():
            ca = cs['attempts']
            ck = cs['evals'].get('#', 0)
            ce = cs['evals'].get('=', 0)
            cb = cs['evals'].get('/', 0) if is_atk else 0
            cs['kill_pct']   = round(ck / ca * 100, 1) if ca else 0
            cs['error_pct']  = round(ce / ca * 100, 1) if ca else 0
            cs['efficiency'] = round((ck - ce - cb) / ca * 100, 1) if ca else 0

CLOSE_K_CODES = {'K1', 'K2', 'KC'}   # front quick / back quick → close-route attacks
GAP_K_CODES   = {'K7', 'KM', 'KP', 'KE', 'KD'}   # gap / shoot sets


def compute_match_rotation_stats(actions, loyola_side='both'):
    """
    Build rotation-based match report tables from LUC-only match actions.

    Returns dict with:
      rotation_atk       — all LUC attacks grouped by LUC rotation × combo code
      rotation_atk_close — same, filtered to rallies where setter called a close K-code
      rotation_atk_gap   — same, filtered to rallies where setter called a gap K-code
      setter_atk         — all LUC attacks grouped by setter × attacker × combo code
      setter_fbso        — attacks that follow any reception (in-system FBSO)
      setter_fbso_perfect— attacks that follow a perfect (#) reception
    """
    by_rally = defaultdict(list)
    for a in actions:
        by_rally[a.get('rally_seq', 0)].append(a)

    def ea():
        return {'k': 0, 'e': 0, 'b': 0, 'bk': 0, 'a': 0}

    def add_atk(bucket, ev):
        bucket['a'] += 1
        if ev == '#':   bucket['k'] += 1
        elif ev == '=': bucket['e'] += 1
        else:           bucket['b'] += 1
        if ev == '/':   bucket['bk'] += 1   # blocked specifically (opponent scored block kill)

    rot_atk       = defaultdict(lambda: defaultdict(ea))
    rot_atk_close = defaultdict(lambda: defaultdict(ea))
    rot_atk_gap   = defaultdict(lambda: defaultdict(ea))

    # per-rotation serve/receive/sideout accumulators
    def es(): return {'att':0,'ace':0,'err':0,'pos':0,'neg':0}
    def er(): return {'att':0,'pts':0,'perf':0,'good':0,'med':0,'oos':0,'err':0,'over':0}
    def eso(): return {'so_opp':0,'so_won':0,'srv_opp':0,'srv_won':0,'blk':0}
    rot_srv = defaultdict(es)
    rot_rcv = defaultdict(er)
    rot_so  = defaultdict(eso)

    # {setter_num: {atk_num: {bucket: {combo_code: {k,e,b,a}}}}}
    # buckets: 'all' | 'fbso' | 'perf'(#) | 'good'(+) | 'med'(!) | 'oos'(-) | 'transition'
    setter_atk = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(ea))))

    for rs, rally in sorted(by_rally.items()):
        rot = None
        for a in rally:
            r = a.get('rotation')
            if r is not None:
                rot = str(r)
                break

        # Reception quality this rally — LUC receptions only
        rcv_ev = next((a.get('evaluation','') for a in rally
                       if a.get('skill_code') == 'R'
                       and (loyola_side == 'both' or a.get('team_side') == loyola_side)), None)
        has_rcv = rcv_ev is not None

        # ── Rally outcome (who won) ───────────────────────────────────────────
        luc_won = None
        for a in reversed(rally):
            ev = a.get('evaluation', '')
            if ev in ('#', '=', '/'):
                side = a.get('team_side')
                if ev == '#':
                    luc_won = (side == loyola_side)
                else:  # '=' error or '/' blocked-attack = opponent of action team wins
                    luc_won = (side != loyola_side)
                break

        # LUC serve this rally
        luc_srv = next((a for a in rally
                        if a.get('skill_code') == 'S'
                        and (loyola_side == 'both' or a.get('team_side') == loyola_side)), None)

        # LUC reception this rally
        luc_rcv = next((a for a in rally
                        if a.get('skill_code') == 'R'
                        and (loyola_side == 'both' or a.get('team_side') == loyola_side)), None)

        # LUC block kills this rally
        luc_blks = sum(1 for a in rally
                       if a.get('skill_code') == 'B'
                       and (loyola_side == 'both' or a.get('team_side') == loyola_side)
                       and a.get('evaluation') == '#')

        if rot:
            # Serve stats
            if luc_srv:
                sev = luc_srv.get('evaluation', '')
                rs_ = rot_srv[rot]
                rs_['att'] += 1
                if sev == '#':   rs_['ace'] += 1
                elif sev == '=': rs_['err'] += 1
                elif sev == '+': rs_['pos'] += 1
                elif sev == '-': rs_['neg'] += 1
                # Point scoring % (LUC serving → did LUC win?)
                if luc_won is not None:
                    rot_so[rot]['srv_opp'] += 1
                    if luc_won: rot_so[rot]['srv_won'] += 1

            # Receive stats + sideout %
            if luc_rcv:
                rev = luc_rcv.get('evaluation', '')
                rr_ = rot_rcv[rot]
                rr_['att'] += 1
                if   rev == '#': rr_['perf'] += 1; rr_['pts'] += 3
                elif rev == '+': rr_['good'] += 1; rr_['pts'] += 2
                elif rev == '!': rr_['med']  += 1; rr_['pts'] += 1
                elif rev == '-': rr_['oos']  += 1
                elif rev == '=': rr_['err']  += 1
                elif rev == '/': rr_['over'] += 1
                if luc_won is not None:
                    rot_so[rot]['so_opp'] += 1
                    if luc_won: rot_so[rot]['so_won'] += 1

            # Blocks
            rot_so[rot]['blk'] += luc_blks

        # Each setter action → find next attack in same rally (LUC only)
        for i, a in enumerate(rally):
            if a.get('skill_code') != 'E':
                continue
            # Skip opponent setter actions
            if loyola_side != 'both' and a.get('team_side') != loyola_side:
                continue
            setter_num = a.get('player_num', '')
            if not setter_num:
                continue

            k_code   = a.get('combo_code') or ''
            is_close = k_code in CLOSE_K_CODES
            is_gap   = k_code in GAP_K_CODES

            next_atk = next((b for b in rally[i+1:]
                             if b.get('skill_code') == 'A'
                             and (loyola_side == 'both' or b.get('team_side') == loyola_side)), None)
            if not next_atk:
                continue

            atk_num = next_atk.get('player_num', '')
            cc      = next_atk.get('combo_code') or 'OVR'
            ev      = next_atk.get('evaluation', '')

            # ── Attack by Rotation ──────────────────────────────────────────────
            if rot:
                add_atk(rot_atk[rot][cc], ev)
                if cc != 'OVR':
                    add_atk(rot_atk[rot]['OVR'], ev)
                if is_close:
                    add_atk(rot_atk_close[rot][cc], ev)
                    if cc != 'OVR':
                        add_atk(rot_atk_close[rot]['OVR'], ev)
                elif is_gap:
                    add_atk(rot_atk_gap[rot][cc], ev)
                    if cc != 'OVR':
                        add_atk(rot_atk_gap[rot]['OVR'], ev)

            if not setter_num or not atk_num:
                continue

            # ── Attack by Setter — bucketed by reception quality ──────────────
            quality_buckets = ['all']
            if has_rcv:
                quality_buckets.append('fbso')
                if   rcv_ev == '#': quality_buckets.append('perf')
                elif rcv_ev == '+': quality_buckets.append('good')
                elif rcv_ev == '!': quality_buckets.append('med')
                elif rcv_ev == '-': quality_buckets.append('oos')
            else:
                quality_buckets.append('transition')

            for bkt in quality_buckets:
                add_atk(setter_atk[setter_num][atk_num][bkt][cc], ev)
                if cc != 'OVR':
                    add_atk(setter_atk[setter_num][atk_num][bkt]['OVR'], ev)
                add_atk(setter_atk[setter_num]['OVR'][bkt][cc], ev)
                if cc != 'OVR':
                    add_atk(setter_atk[setter_num]['OVR'][bkt]['OVR'], ev)

    def ser_rot(d):
        return {r: {cc: dict(v) for cc, v in combos.items()} for r, combos in d.items()}

    def ser_setter_v2(d):
        return {
            s: {a: {bkt: {cc: dict(v) for cc, v in cc_map.items()}
                    for bkt, cc_map in bkt_map.items()}
                for a, bkt_map in atks.items()}
            for s, atks in d.items()
        }

    return {
        'rotation_atk':       ser_rot(rot_atk),
        'rotation_atk_close': ser_rot(rot_atk_close),
        'rotation_atk_gap':   ser_rot(rot_atk_gap),
        'setter_atk':         ser_setter_v2(setter_atk),
        'rotation_srv':       {r: dict(v) for r, v in rot_srv.items()},
        'rotation_rcv':       {r: dict(v) for r, v in rot_rcv.items()},
        'rotation_so':        {r: dict(v) for r, v in rot_so.items()},
    }


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
        ca = compute_call_attacks(acts, ls)
        rot_stats = compute_match_rotation_stats(acts, ls) if s.get('type') == 'match' else None
        session_rally[sid] = {'dig': dig, 'fbso': fbso, 'call_atk': ca, 'rot_stats': rot_stats}
        # Accumulate setter-call → attack outcome per season
        for k_code, combos in ca.items():
            for atk_cc, cv in combos.items():
                acc = call_attacks_by_season[season][k_code][atk_cc]
                acc['attempts'] += cv['attempts']
                acc['kills']    += cv['kills']
                acc['errors']   += cv['errors']

    # ── Session index ─────────────────────────────────────────────────────────
    def compute_opp_stats(session, loyola_side):
        """Aggregate opponent team ATK/SRV/RCV/FBSO totals for a match session."""
        if loyola_side == 'both':
            return None
        opp = {
            'A':    {'att': 0, 'k': 0, 'e': 0, 'blocked': 0},
            'S':    {'att': 0, 'ace': 0, 'err': 0},
            'R':    {'att': 0, 'pts': 0},
            'FBSO': {'rcv': 0, 'kills': 0},
        }
        actions = session.get('actions', [])
        for action in actions:
            side = action.get('team_side')
            if side == loyola_side:
                continue  # skip LUC actions
            sc = action.get('skill_code', '')
            ev = action.get('evaluation', '')
            if sc == 'A':
                opp['A']['att'] += 1
                if ev == '#':   opp['A']['k'] += 1
                elif ev == '=': opp['A']['e'] += 1
                elif ev == '/': opp['A']['blocked'] += 1
            elif sc == 'S':
                opp['S']['att'] += 1
                if ev == '#':   opp['S']['ace'] += 1
                elif ev == '=': opp['S']['err'] += 1
            elif sc == 'R':
                opp['R']['att'] += 1
                opp['R']['pts'] += rcv_points(ev, is_match=True)

        # Opponent FBSO: group by rally_seq, find rallies where opp received,
        # then check if opp's first attack in that rally was a kill.
        from collections import defaultdict
        rally_groups = defaultdict(list)
        for a in actions:
            rally_groups[a.get('rally_seq', 0)].append(a)
        for rs in sorted(rally_groups):
            rally = rally_groups[rs]
            opp_acts = [a for a in rally if a.get('team_side') != loyola_side]
            if not any(a.get('skill_code') == 'R' for a in opp_acts):
                continue
            opp['FBSO']['rcv'] += 1
            first_atk = next((a for a in opp_acts if a.get('skill_code') == 'A'), None)
            if first_atk and first_atk.get('evaluation') == '#':
                opp['FBSO']['kills'] += 1
        return opp

    def compute_zone_charts(session, loyola_side):
        """
        Collect individual attack and serve shot records for court visuals.
        Stores sub-zone precision: 'ss' (digit, attack start sub-zone) and
        'esz' (letter A-D, serve end sub-zone from VM files).
        Returns:
          atk: {combo_code: [{sz, ss, ez, ev}]}
          srv: [{sz, ez, esz, ev}]
        """
        VALID_D = set('123456789')
        VALID_L = set('ABCDEFGHIJ')
        atk = {}
        srv = []
        for action in session.get('actions', []):
            side = action.get('team_side')
            if loyola_side != 'both' and side != loyola_side:
                continue
            sc  = action.get('skill_code')
            ev  = action.get('evaluation', '')
            sz  = str(action.get('start_zone')    or '')
            ss  = str(action.get('start_subzone') or '')
            ez  = str(action.get('end_zone')      or '')
            esz = str(action.get('end_subzone')   or '')
            if not ez or ez not in VALID_D:
                continue
            if sc == 'A':
                cc = action.get('combo_code')
                if not cc:
                    continue
                if cc not in atk:
                    atk[cc] = []
                atk[cc].append({
                    'sz':  sz  if sz  in VALID_D else '',
                    'ss':  ss  if ss  in VALID_D else '',
                    'ez':  ez,
                    'ev':  ev,
                })
            elif sc == 'S':
                srv.append({
                    'sz':  sz  if sz  in VALID_D else '',
                    'ez':  ez,
                    'esz': esz if esz in VALID_L else '',
                    'ev':  ev,
                })
        if not atk and not srv:
            return None
        return {'atk': atk, 'srv': srv}

    sessions_index = []
    for s in sessions_raw:
        sid  = f"{s['season']}|{s['file']}"
        fbso = session_rally[sid]['fbso']
        ls   = loyola_side_for_session(s)
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
            'loyola_side':    ls,
            'fbso_rcv':       fbso['rcv_rallies'],
            'fbso_kills':     fbso['fbso_kills'],
            'call_attacks':   session_rally[sid]['call_atk'],
            'match_rot_stats': session_rally[sid]['rot_stats'],  # None for practice
            # opp_stats: only populated when the file contains both teams' actions
            'opp_stats': compute_opp_stats(s, ls)
                         if s.get('type') == 'match' and
                            any(a.get('team_side') != ls for a in s.get('actions', []))
                         else None,
            'zone_charts': compute_zone_charts(s, ls),
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

            # Accumulate pre-scaled pass points and category counts so the
            # dashboard can compute correct averages and breakdowns across
            # mixed match/practice sessions.
            if action.get('skill_code') == 'R':
                ev_code = action.get('evaluation', '')
                is_m    = (stype == 'match')
                pts     = rcv_points(ev_code, is_m)
                cat     = rcv_category(ev_code, is_m)
                zone    = action.get('end_zone')
                for store in (psk, pse, ss):
                    rsk = store['skills'].setdefault('R', empty_skill())
                    rsk['rcv_pts']  = rsk.get('rcv_pts', 0) + pts
                    rsk['rcv_cat']  = rsk.get('rcv_cat', {'perf':0,'good':0,'med':0,'oos':0,'over':0,'err':0})
                    rsk['rcv_cat'][cat] = rsk['rcv_cat'].get(cat, 0) + 1
                    # Add pre-scaled pts to the main zone record too
                    if zone and str(zone) in store.get('rcv_zones', {}):
                        store['rcv_zones'][str(zone)]['rcv_pts'] = \
                            store['rcv_zones'][str(zone)].get('rcv_pts', 0) + pts
                # Tag reception by serve type — technique field on the reception action
                # encodes the incoming serve type: Q=topspin, M=float, H=jump-float
                tech = action.get('technique', '')
                tech = tech if tech in ('Q', 'M', 'H') else None
                if tech in ('Q', 'M', 'H'):
                    # Normalize H (jump float) → M (float bucket)
                    tech = 'M' if tech == 'H' else tech
                if tech in ('Q', 'M'):
                    t_key = 'R' + tech   # 'RQ' or 'RM'
                    for store in (psk, pse, ss):
                        rsk_t = store['skills'].setdefault(t_key, empty_skill())
                        rsk_t['attempts'] += 1
                        rsk_t['evals'][ev_code] = rsk_t['evals'].get(ev_code, 0) + 1
                        rsk_t['rcv_pts'] = rsk_t.get('rcv_pts', 0) + pts
                        if 'rcv_cat' not in rsk_t:
                            rsk_t['rcv_cat'] = {'perf':0,'good':0,'med':0,'oos':0,'over':0,'err':0}
                        rsk_t['rcv_cat'][cat] = rsk_t['rcv_cat'].get(cat, 0) + 1
                        if zone:
                            zk_typed = 'rcv_zones_q' if tech == 'Q' else 'rcv_zones_m'
                            if zk_typed not in store:
                                store[zk_typed] = {}
                            if str(zone) not in store[zk_typed]:
                                store[zk_typed][str(zone)] = empty_zone()
                            store[zk_typed][str(zone)]['attempts'] += 1
                            store[zk_typed][str(zone)]['evals'][ev_code] = \
                                store[zk_typed][str(zone)]['evals'].get(ev_code, 0) + 1
                            store[zk_typed][str(zone)]['rcv_pts'] = \
                                store[zk_typed][str(zone)].get('rcv_pts', 0) + pts

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

    # Build sid → is_match lookup for zone derivation
    sid_is_match = {f"{s['season']}|{s['file']}": (s.get('type') == 'match') for s in sessions_raw}

    # Derive metrics
    for ss in season_stats.values():
        derive(ss['skills'])
        derive_zones(ss['rcv_zones'])   # season zones mix types; use neutral formula
    for (pn, sid), ps in player_session.items():
        derive(ps['skills'])
        derive_zones(ps['rcv_zones'], is_match=sid_is_match.get(sid, False))
    for pse in player_season.values():
        pse['sessions'] = len(pse['sessions'])
        derive(pse['skills'])
        derive_zones(pse['rcv_zones'])  # season zones mix types; use neutral formula

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
