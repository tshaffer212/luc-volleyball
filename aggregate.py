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
# Value can be a plain string (applies to every season that jersey number appears
# in) or a {season: name} dict when the SAME number was worn by a different real
# person in other seasons (roster turnover) and the fix must not bleed into them.
NAME_OVERRIDES = {
    # '16': 'Alex Smith Van Oyen',   # uncomment if Smith is correct, not Smits
    '3': {'Fall 2027': 'Matt Kelly'},   # #3 was JJ Sowa through Fall 2026; Matt Kelly
                                        # took #3 in Fall 2027 and is sometimes misspelled 'Matt Kally'.
}

# ── Manual position overrides ─────────────────────────────────────────────────
# Use when a DVW file has the wrong position_code for a player (e.g. a scouting
# entry mistake) and you want it corrected everywhere regardless of what the raw
# file says. Format: { jersey_number_string: 'position_code' }
# position_code convention used by this dashboard: 1=Libero, 2=Outside, 3=Opposite, 4=Middle, 5=Setter
POSITION_OVERRIDES = {
    '15': '4',   # Wil/William Hatch — entered as Outside ('2') in the first two Fall 2027
                 # files by mistake; he's a Middle ('4'). Consistent with every prior season.
    '29': '2',   # Tyler Howard — entered as Opposite ('3') in the first Fall 2027 file
                 # (9-8.dvw) by mistake; he's an Outside ('2'), matching every prior season
                 # (Spring 2025 through Fall 2026) and the later Fall 2027 files (9-11, 9-15).
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


def compute_setter_player_attacks(actions, loyola_side):
    """
    Per rally: for each Set (E) action with a player_num, find the next Attack (A)
    action on the same team side, and attribute that attack (by attacking player and
    combo code) to the setter who set it. This gives TRUE per-setter attribution even
    when two setters share the same session (rotating groups / split-squad practices),
    unlike session-level "who was the primary setter" approximations.

    Returns: { setter_pnum: { attacker_pnum: { atk_combo: {attempts, kills, errors, blocks} } } }
    """
    rallies, current = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and current:
            rallies.append(current)
            current = []
        current.append(a)
    if current:
        rallies.append(current)

    result = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'attempts': 0, 'kills': 0, 'errors': 0, 'blocks': 0})))

    for rally in rallies:
        for i, a in enumerate(rally):
            if a.get('skill_code') != 'E':
                continue
            setter_pnum = a.get('player_num') or ''
            if not setter_pnum:
                continue
            e_side = a.get('team_side')
            if loyola_side != 'both' and e_side != loyola_side:
                continue
            next_atk = next(
                (b for b in rally[i+1:]
                 if b.get('skill_code') == 'A' and b.get('team_side') == e_side),
                None
            )
            if not next_atk:
                continue
            atk_cc = next_atk.get('combo_code') or ''
            attacker_pnum = next_atk.get('player_num') or ''
            if not atk_cc or not attacker_pnum:
                continue
            ev = next_atk.get('evaluation') or ''
            cv = result[setter_pnum][attacker_pnum][atk_cc]
            cv['attempts'] += 1
            if ev == '#':
                cv['kills'] += 1
            elif ev == '=':
                cv['errors'] += 1
            elif ev == '/':
                cv['blocks'] += 1

    return {sp: {ap: dict(combos) for ap, combos in attackers.items()}
            for sp, attackers in result.items()}


def compute_setter_tab(actions, loyola_side):
    """
    Setter-tab data for one session, attributed rally by rally:
      { setter_pnum: { situation: {
            'atk':      {atk_combo: {a,k,e,b}},          # attack right after this setter's set
            'calls':    {k_code: n},                       # setter's own call codes (K1/K2/K7...)
            'call_atk': {k_code: {atk_combo: {a,k,e,b}}}   # attack outcome by the call made
      } } }
    situation: 'all' | 'fbso' (first set after own team's reception) | 'perf' | 'good' |
               'med' | 'oos' (pass quality of that reception) | 'transition' (any later set)
    Each attack is credited to at most one set (a doubled set code can't count it twice).
    """
    rallies, cur = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and cur:
            rallies.append(cur); cur = []
        cur.append(a)
    if cur:
        rallies.append(cur)

    def e4(): return {'a': 0, 'k': 0, 'e': 0, 'b': 0}
    out = defaultdict(lambda: defaultdict(lambda: {
        'atk': defaultdict(e4), 'calls': defaultdict(int),
        'call_atk': defaultdict(lambda: defaultdict(e4))}))
    PASS_BKT = {'#': 'perf', '+': 'good', '!': 'med', '-': 'oos'}

    for rally in rallies:
        linked = set()
        first_set_seen = set()          # sides whose first set of the rally we've passed
        for i, a in enumerate(rally):
            if a.get('skill_code') != 'E':
                continue
            side = a.get('team_side')
            if loyola_side != 'both' and side != loyola_side:
                continue
            setter = a.get('player_num') or ''
            if not setter:
                continue
            # Situation: first set after this side's reception = first ball, else transition
            rcv = next((b for b in rally[:i] if b.get('skill_code') == 'R' and b.get('team_side') == side), None)
            sits = ['all']
            if rcv is not None and side not in first_set_seen:
                sits.append('fbso')
                q = PASS_BKT.get(rcv.get('evaluation', ''))
                if q: sits.append(q)
            else:
                sits.append('transition')
            first_set_seen.add(side)

            call = a.get('combo_code') or ''
            nxt = next((j for j in range(i + 1, len(rally))
                        if rally[j].get('skill_code') == 'A' and rally[j].get('team_side') == side), None)
            atk = rally[nxt] if nxt is not None and nxt not in linked else None
            if atk is not None:
                linked.add(nxt)
            for sit in sits:
                bucket = out[setter][sit]
                if call:
                    bucket['calls'][call] += 1
                if atk is None:
                    continue
                cc = atk.get('combo_code') or ''
                if not cc:
                    continue
                ev = atk.get('evaluation', '')
                for rec in ([bucket['atk'][cc], bucket['call_atk'][call][cc]] if call else [bucket['atk'][cc]]):
                    rec['a'] += 1
                    if ev == '#':   rec['k'] += 1
                    elif ev == '=': rec['e'] += 1
                    elif ev == '/': rec['b'] += 1

    return {sp: {sit: {'atk': {cc: dict(v) for cc, v in b['atk'].items()},
                       'calls': dict(b['calls']),
                       'call_atk': {k: {cc: dict(v) for cc, v in m.items()} for k, m in b['call_atk'].items()}}
                 for sit, b in sits_.items()}
            for sp, sits_ in out.items()}


# ── Points Added (Reports) ────────────────────────────────────────────────────
# For every touch we record: skill, result, situation, and whether that player's
# team went on to win the rally. The dashboard turns these counts into "how much
# did this touch change our chance of winning the rally vs an average touch".
#   Situations (used by the situation-adjusted view):
#     Serve / Pass : serve type — Q = topspin, F = float (M/H), u = unknown
#     Attack       : phase/set-type — phase = pass grade before a first-ball attack
#                    (# + ! - o=other) or T = transition; set-type group below
#     Block / Dig  : set-type group of the opponent attack just before it
#     X (setter)   : one per attack he set: the attack's phase/set-type (the setter's
#                    choice of set, valued against an average set in that phase)
PA_ATK_GROUP = {'X5':'pin','X6':'pin','V5':'high','V6':'high','V8':'high','VP':'high',
                'X1':'quick','X2':'quick','X7':'quick','XP':'bic','X8':'dball'}

def practice_rallies(actions):
    """Practice files have no rally markers (the whole session is one 'rally'), so cut the stream into
    rallies: a new one starts at every serve and after every rally-ending action (kill, ace, error, block
    kill, blocked attack). Returns copies of the actions with rally_seq set to that rally."""
    out, k, prev_end = [], 0, False
    for a in actions:
        if out and (prev_end or a.get('skill_code') == 'S'):
            k += 1
        out.append(dict(a, rally_seq=k))
        prev_end = bool(_pa_terminal(a))
    return out


def _pa_terminal(a):
    """+1 = this action's team wins the rally, -1 = loses, 0 = rally continues."""
    sk, ev = a.get('skill_code'), a.get('evaluation', '')
    if sk == 'S': return 1 if ev == '#' else (-1 if ev == '=' else 0)
    if sk == 'A': return 1 if ev == '#' else (-1 if ev in ('=', '/') else 0)
    if sk == 'B': return 1 if ev == '#' else (-1 if ev == '=' else 0)
    if sk in ('R', 'D', 'E'): return -1 if ev == '=' else 0
    return 0

def compute_points_added(actions, loyola_side):
    rallies, cur = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and cur:
            rallies.append(cur); cur = []
        cur.append(a)
    if cur:
        rallies.append(cur)
    other = lambda sd: 'away' if sd == 'home' else 'home'
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))   # set -> pnum -> key -> [n, wins]
    for rally in rallies:
        winner = None
        for a in reversed(rally):
            tm = _pa_terminal(a)
            if tm and a.get('team_side') in ('home', 'away'):
                winner = a['team_side'] if tm > 0 else other(a['team_side'])
                break
        if winner is None:
            continue
        rcv = {}; fb_used = set(); faced = {}; last_set = {}
        for a in rally:
            side, sk, ev = a.get('team_side'), a.get('skill_code'), a.get('evaluation', '')
            pn = a.get('player_num') or ''
            key = sit = None
            setter_key = None
            if sk in ('S', 'R'):
                tq = a.get('technique') or ''
                sit = 'Q' if tq == 'Q' else ('F' if tq in ('M', 'H') else 'u')
                if sk == 'R' and side not in rcv:
                    rcv[side] = ev
            elif sk == 'E':
                last_set[side] = a
                sit = 'u'
            elif sk == 'A':
                if side in rcv and side not in fb_used:
                    g = rcv[side]; phase = g if g in ('#', '+', '!', '-') else 'o'
                else:
                    phase = 'T'
                fb_used.add(side)
                grp = PA_ATK_GROUP.get(a.get('combo_code') or '', 'oth')
                sit = phase + '/' + grp
                faced[other(side)] = grp
                st = last_set.pop(side, None)
                if st is not None and st.get('player_num'):
                    setter_key = (st['player_num'], 'X|' + ev + '|' + sit)   # attack result he set
            elif sk in ('B', 'D'):
                sit = faced.get(side, 'u')
            else:
                continue
            if loyola_side != 'both' and side != loyola_side:
                continue
            if ev not in ('#', '+', '!', '-', '/', '='):
                continue
            won = 1 if winner == side else 0
            sn = str(a.get('set_number') or 0)
            if pn:
                rec = out[sn][pn][f'{sk}|{ev}|{sit}']; rec[0] += 1; rec[1] += won
            if setter_key:
                rec = out[sn][setter_key[0]][setter_key[1]]; rec[0] += 1; rec[1] += won
    return {sn: {pn: {k: v for k, v in keys.items()} for pn, keys in pl.items()} for sn, pl in out.items()}


def compute_pass_hit(actions, loyola_side):
    """
    Pass & Hit: on every serve-receive possession, the first attack after the pass
    that came off a SET. If the passer is also that hitter → 'ph' (passed & hit),
    otherwise the hitter's attack goes in 'np' (first-ball attack without passing).
    Returns { set: { pnum: { 'ph': {ev: [n, won]}, 'np': {ev: [n, won]},
                             'pg': {pass_eval: n} } } }   (pg = grades of the passes in 'ph')
    """
    rallies, cur = [], []
    for a in actions:
        if a.get('skill_code') == 'S' and cur:
            rallies.append(cur); cur = []
        cur.append(a)
    if cur:
        rallies.append(cur)
    other = lambda sd: 'away' if sd == 'home' else 'home'
    out = defaultdict(lambda: defaultdict(lambda: {'ph': defaultdict(lambda: [0, 0]),
                                                   'np': defaultdict(lambda: [0, 0]),
                                                   'pg': defaultdict(int)}))
    for rally in rallies:
        winner = None
        for a in reversed(rally):
            tm = _pa_terminal(a)
            if tm and a.get('team_side') in ('home', 'away'):
                winner = a['team_side'] if tm > 0 else other(a['team_side'])
                break
        if winner is None:
            continue
        sides = [loyola_side] if loyola_side != 'both' else ['home', 'away']
        for side in sides:
            mine = [a for a in rally if a.get('team_side') == side]
            ri = next((i for i, a in enumerate(mine) if a.get('skill_code') == 'R'), None)
            if ri is None:
                continue
            rcv = mine[ri]
            set_seen = False
            for a in mine[ri + 1:]:
                sk = a.get('skill_code')
                if sk == 'E':
                    set_seen = True
                elif sk == 'A':
                    if set_seen:
                        ev = a.get('evaluation', '')
                        hitter, passer = a.get('player_num') or '', rcv.get('player_num') or ''
                        if hitter and ev in ('#', '+', '-', '/', '=', '!'):
                            sn = str(a.get('set_number') or 0)
                            won = 1 if winner == side else 0
                            if hitter == passer:
                                r = out[sn][hitter]['ph'][ev]; r[0] += 1; r[1] += won
                                out[sn][hitter]['pg'][rcv.get('evaluation', '')] += 1
                            else:
                                r = out[sn][hitter]['np'][ev]; r[0] += 1; r[1] += won
                    break        # only the first attack of the possession
                elif sk in ('D', 'B'):
                    break        # possession ended without a first-ball attack
    return {sn: {pn: {'ph': dict(v['ph']), 'np': dict(v['np']), 'pg': dict(v['pg'])} for pn, v in pl.items()}
            for sn, pl in out.items()}


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
    fbso = {'rcv_rallies': 0, 'fbso_kills': 0, 'by_player': {}}   # by_player: receiver → [rcv, kills]
    def _fb(pnum, kill):
        rec = fbso['by_player'].setdefault(pnum or '?', [0, 0])
        rec[0] += 1
        if kill: rec[1] += 1

    # Match files that code BOTH teams (current Volleymetrics/Volleystation exports):
    # group by rally_seq and only count `loyola_side`'s receptions/digs/attacks.
    # (The linear scan below assumes a LUC-only file; on a two-sided file it would
    #  count both teams' receptions and digs.)
    two_sided = match_mode and len({a.get('team_side') for a in actions if a.get('team_side')}) > 1 \
                and any('rally_seq' in a for a in actions)
    if two_sided:
        by_rally = defaultdict(list)
        for a in actions:
            by_rally[a.get('rally_seq', 0)].append(a)
        for rs in sorted(by_rally):
            rally = by_rally[rs]
            mine  = [a for a in rally if a.get('team_side') == loyola_side]
            # FBSO: rally where this side received; first attack by this side a kill?
            rcv_a = next((a for a in mine if a.get('skill_code') == 'R'), None)
            if rcv_a is not None:
                fbso['rcv_rallies'] += 1
                first_atk = next((a for a in mine if a.get('skill_code') == 'A'), None)
                kill = bool(first_atk and first_atk.get('evaluation') == '#')
                if kill:
                    fbso['fbso_kills'] += 1
                _fb(rcv_a.get('player_num'), kill)
            # Dig → kill: this side's dig, followed later in the rally by this side's kill
            for i, a in enumerate(mine):
                if a.get('skill_code') != 'D':
                    continue
                pnum = a.get('player_num', '')
                if not pnum:
                    continue
                pd = player_dig.setdefault(pnum, {'dig_rallies': 0, 'dig_kill_rallies': 0})
                pd['dig_rallies'] += 1
                if any(b.get('skill_code') == 'A' and b.get('evaluation') == '#' for b in mine[i+1:]):
                    pd['dig_kill_rallies'] += 1
        return player_dig, fbso

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
                kill = False
                # Find first Attack before next R or S
                for j in range(i+1, n):
                    ns = actions[j].get('skill_code')
                    if ns in ('S', 'R'):
                        break
                    if ns == 'A':
                        if actions[j].get('evaluation') == '#':
                            fbso['fbso_kills'] += 1
                            kill = True
                        break  # only the FIRST attack counts
                _fb(a.get('player_num'), kill)

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

            # Count every serve as a receiving opportunity (aces = failed side-out).
            fbso['rcv_rallies'] += 1

            first_atk = next(
                (a for a in rally
                 if a.get('team_side') == rcv_side and a.get('skill_code') == 'A'),
                None
            )
            kill = bool(first_atk and first_atk.get('evaluation') == '#')
            if kill:
                fbso['fbso_kills'] += 1
            rcv_a = next((a for a in rally if a.get('team_side') == rcv_side and a.get('skill_code') == 'R'), None)
            _fb(rcv_a.get('player_num') if rcv_a else None, kill)

    return player_dig, fbso


def add_rcv_extras(stores, action, is_match):
    """Reception extras: pre-scaled pass points, pass categories, and float/topspin
    split (RM/RQ skills + typed zone records) for every store in `stores`."""
    if action.get('skill_code') != 'R':
        return
    if True:
        ev_code = action.get('evaluation', '')
        is_m    = is_match
        pts     = rcv_points(ev_code, is_m)
        cat     = rcv_category(ev_code, is_m)
        zone    = action.get('end_zone')
        for store in stores:
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
            for store in stores:
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


# DVW setter zone → rotation number used throughout the Reports tab
SETTER_ZONE_TO_ROT = {1: 1, 6: 2, 5: 3, 4: 4, 3: 5, 2: 6}


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

    # Every rotation table is kept PER PLAYER ([pnum][rot][...]) so the Reports
    # player filter can sum just the selected players; team = sum of all players.
    #   attacks → attacker, serves/PSP → server, receptions/SO → receiver,
    #   block kills → blocker.  '?' = action with no jersey coded.
    def _pr(f): return defaultdict(lambda: defaultdict(f))
    rot_atk       = defaultdict(lambda: defaultdict(lambda: defaultdict(ea)))
    rot_atk_close = defaultdict(lambda: defaultdict(lambda: defaultdict(ea)))
    rot_atk_gap   = defaultdict(lambda: defaultdict(lambda: defaultdict(ea)))

    # per-rotation serve/receive/sideout accumulators
    def es(): return {'att':0,'ace':0,'err':0,'pos':0,'neg':0}
    def er(): return {'att':0,'pts':0,'perf':0,'good':0,'med':0,'oos':0,'err':0,'over':0}
    def eso(): return {'so_opp':0,'so_won':0,'srv_opp':0,'srv_won':0,'blk':0}
    rot_srv = _pr(es)
    rot_rcv = _pr(er)
    rot_so  = _pr(eso)

    # {setter_num: {atk_num: {bucket: {combo_code: {k,e,b,a}}}}}
    # buckets: 'all' | 'fbso' | 'perf'(#) | 'good'(+) | 'med'(!) | 'oos'(-) | 'transition'
    setter_atk = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(ea))))
    # {setter_num: {rot: {'c': {combo: {k,e,b,bk,a}}, 'h': {atk_num: {...}}}}} — team attack by
    # rotation on balls this setter set (player portal: setters' rotation tab)
    setter_rot = defaultdict(lambda: defaultdict(lambda: {'c': defaultdict(ea), 'h': defaultdict(ea)}))

    for rs, rally in sorted(by_rally.items()):
        rot = None
        for a in rally:
            r = a.get('rotation')
            if r is not None:
                # The DVW 'rotation' value is the SETTER'S COURT ZONE (*z1/*z6/… markers,
                # field 9/10). Reports number rotations 1-6 in serve order instead:
                #   Z1→R1  Z6→R2  Z5→R3 (setter back row)   Z4→R4  Z3→R5  Z2→R6 (front row)
                rot = str(SETTER_ZONE_TO_ROT.get(int(r), r)) if str(r).isdigit() else str(r)
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

        # LUC block kills this rally (by blocker)
        luc_blk_players = [a.get('player_num') or '?' for a in rally
                           if a.get('skill_code') == 'B'
                           and (loyola_side == 'both' or a.get('team_side') == loyola_side)
                           and a.get('evaluation') == '#']
        srv_p = (luc_srv.get('player_num') or '?') if luc_srv else None
        rcv_p = (luc_rcv.get('player_num') or '?') if luc_rcv else None

        if rot:
            # Serve stats
            if luc_srv:
                sev = luc_srv.get('evaluation', '')
                rs_ = rot_srv[srv_p][rot]
                rs_['att'] += 1
                if sev == '#':   rs_['ace'] += 1
                elif sev == '=': rs_['err'] += 1
                elif sev == '+': rs_['pos'] += 1
                elif sev == '-': rs_['neg'] += 1
                # Point scoring % (LUC serving → did LUC win?)
                if luc_won is not None:
                    rot_so[srv_p][rot]['srv_opp'] += 1
                    if luc_won: rot_so[srv_p][rot]['srv_won'] += 1

            # Receive stats + sideout %
            if luc_rcv:
                rev = luc_rcv.get('evaluation', '')
                rr_ = rot_rcv[rcv_p][rot]
                rr_['att'] += 1
                if   rev == '#': rr_['perf'] += 1; rr_['pts'] += 3
                elif rev == '+': rr_['good'] += 1; rr_['pts'] += 2
                elif rev == '!': rr_['med']  += 1; rr_['pts'] += 1
                elif rev == '-': rr_['oos']  += 1
                elif rev == '=': rr_['err']  += 1
                elif rev == '/': rr_['over'] += 1
                if luc_won is not None:
                    rot_so[rcv_p][rot]['so_opp'] += 1
                    if luc_won: rot_so[rcv_p][rot]['so_won'] += 1

            # Blocks
            for bp in luc_blk_players:
                rot_so[bp][rot]['blk'] += 1

        # Reception-quality buckets for this rally (shared by setter + MISC attribution)
        quality_buckets = ['all']
        if has_rcv:
            quality_buckets.append('fbso')
            if   rcv_ev == '#': quality_buckets.append('perf')
            elif rcv_ev == '+': quality_buckets.append('good')
            elif rcv_ev == '!': quality_buckets.append('med')
            elif rcv_ev == '-': quality_buckets.append('oos')
        else:
            quality_buckets.append('transition')

        linked_atk_ids = set()   # attacks credited to a setter's set this rally

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

            # A doubled set code (two E's before one swing) must not count the attack twice
            if id(next_atk) in linked_atk_ids:
                continue
            linked_atk_ids.add(id(next_atk))
            atk_num = next_atk.get('player_num', '')
            cc      = next_atk.get('combo_code') or 'OVR'
            ev      = next_atk.get('evaluation', '')

            # ── Attack by Rotation ──────────────────────────────────────────────
            if rot:
                add_atk(rot_atk[atk_num or '?'][rot][cc], ev)
                if cc != 'OVR':
                    add_atk(rot_atk[atk_num or '?'][rot]['OVR'], ev)
                if is_close:
                    add_atk(rot_atk_close[atk_num or '?'][rot][cc], ev)
                    if cc != 'OVR':
                        add_atk(rot_atk_close[atk_num or '?'][rot]['OVR'], ev)
                elif is_gap:
                    add_atk(rot_atk_gap[atk_num or '?'][rot][cc], ev)
                    if cc != 'OVR':
                        add_atk(rot_atk_gap[atk_num or '?'][rot]['OVR'], ev)

            if not setter_num or not atk_num:
                continue

            if rot:
                sr = setter_rot[setter_num][rot]
                add_atk(sr['c'][cc], ev)
                if cc != 'OVR':
                    add_atk(sr['c']['OVR'], ev)
                add_atk(sr['h'][atk_num], ev)

            # ── Attack by Setter — bucketed by reception quality ──────────────
            for bkt in quality_buckets:
                add_atk(setter_atk[setter_num][atk_num][bkt][cc], ev)
                if cc != 'OVR':
                    add_atk(setter_atk[setter_num][atk_num][bkt]['OVR'], ev)
                add_atk(setter_atk[setter_num]['OVR'][bkt][cc], ev)
                if cc != 'OVR':
                    add_atk(setter_atk[setter_num]['OVR'][bkt]['OVR'], ev)

        # ── MISC: LUC attacks with no coded set before them (no setter to credit) ──
        # Stored under pseudo-setter 'MISC' so Setter Breakdown totals match Overview.
        for a in rally:
            if a.get('skill_code') != 'A' or id(a) in linked_atk_ids:
                continue
            if loyola_side != 'both' and a.get('team_side') != loyola_side:
                continue
            atk_num = a.get('player_num', '')
            if not atk_num:
                continue
            cc = a.get('combo_code') or 'OVR'
            ev = a.get('evaluation', '')
            for bkt in quality_buckets:
                add_atk(setter_atk['MISC'][atk_num][bkt][cc], ev)
                if cc != 'OVR':
                    add_atk(setter_atk['MISC'][atk_num][bkt]['OVR'], ev)

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
        'by_player': {
            p: {k: v for k, v in {
                'rotation_atk':       ser_rot(rot_atk.get(p, {})),
                'rotation_atk_close': ser_rot(rot_atk_close.get(p, {})),
                'rotation_atk_gap':   ser_rot(rot_atk_gap.get(p, {})),
                'rotation_srv':       {r: dict(v) for r, v in rot_srv.get(p, {}).items()},
                'rotation_rcv':       {r: dict(v) for r, v in rot_rcv.get(p, {}).items()},
                'rotation_so':        {r: dict(v) for r, v in rot_so.get(p, {}).items()},
            }.items() if v}
            for p in set(rot_atk) | set(rot_atk_close) | set(rot_atk_gap) | set(rot_srv) | set(rot_rcv) | set(rot_so)
        },
        'setter_atk':         ser_setter_v2(setter_atk),
        'setter_rot':         {sp: {r: {'c': {cc: dict(v) for cc, v in x['c'].items()},
                                        'h': {h: dict(v) for h, v in x['h'].items()}}
                                    for r, x in rots.items()}
                               for sp, rots in setter_rot.items()},
    }


# ── Right-side auto-correction: X6 <-> X8 (matches and scout files) ──────────
# In a 5-1 the opposite is front row while the setter is back row (R1-R3) and back
# row while the setter is front row (R4-R6). An X6 (in-system right, front row) is
# therefore impossible in R4-R6 and an X8 (D-ball, back-row right) impossible in
# R1-R3, so every such code is swapped: X6 in R4-R6 -> X8, X8 in R1-R3 -> X6.
# The rotation used is the same one the pages show: match files use the coded
# setter zone (lineup position as fallback); scout files use the setter's lineup
# position. Plays whose rotation can't be determined are left alone.
BACK_ROW_SETTER_ZONES  = {1, 6, 5}   # R1, R2, R3
FRONT_ROW_SETTER_ZONES = {4, 3, 2}   # R4, R5, R6
_SWAP_START_ZONE = {'X6': '2', 'X8': '9', 'V6': '2', 'V8': '9'}   # keep the start zone consistent with the new code

def side_setters(session, side):
    """A side's setter(s) in one file: players who made a big share of that side's sets."""
    cnt = Counter(str(a.get('player_num')) for a in session.get('actions', [])
                  if a.get('team_side') == side and a.get('skill_code') == 'E' and a.get('player_num'))
    if not cnt:
        return []
    top = cnt.most_common(1)[0][1]
    return [p for p, n in cnt.most_common(2) if n >= max(10, 0.3 * top)]

def setter_zone_on_play(action, side, parse_side, setters):
    """Court zone (1-6) of `side`'s setter on this action, from that side's lineup.
    parse_side = the side the parser stored in 'luc_lineup' (the other is 'opp_lineup')."""
    lineup = action.get('luc_lineup') if side == parse_side else action.get('opp_lineup')
    if not lineup or not setters:
        return None
    pos = [i + 1 for i, p in enumerate(lineup) if str(p) in setters]
    if len(pos) == 1:
        return pos[0]
    if len(pos) > 1:                      # 6-2: the back-row setter is the one setting
        back = [z for z in pos if z in BACK_ROW_SETTER_ZONES]
        return back[0] if back else pos[0]
    return None

def swap_right_side(session, sides, coded_zone=None, prefer_coded=False):
    """Swap impossible right-side codes in place (X6/X8 and the high versions V6/V8) for the given sides.
    Returns [(side, from, to, n)].
    prefer_coded=True uses the coded setter zone first (match files: that's the rotation the
    report tables show), lineup position as fallback; otherwise the other way round."""
    parse_side = session.get('luc_side') or 'home'
    acts = session.get('actions', [])
    pair = {'X6': 'X8', 'X8': 'X6', 'V6': 'V8', 'V8': 'V6'}
    names = {a.get('combo_code'): a.get('combo_name') for a in acts if a.get('combo_code') in pair and a.get('combo_name')}
    report = []
    for side in sides:
        setters = side_setters(session, side)
        swaps = Counter()
        for a in acts:
            if a.get('team_side') != side or a.get('skill_code') != 'A' or a.get('combo_code') not in pair:
                continue
            zc = coded_zone(side, a) if coded_zone else None
            zl = setter_zone_on_play(a, side, parse_side, setters)
            z = (zc if isinstance(zc, int) else zl) if prefer_coded else (zl if zl is not None else zc)
            if not isinstance(z, int):
                continue
            src = a['combo_code']
            if (src in ('X6', 'V6') and z in FRONT_ROW_SETTER_ZONES) or (src in ('X8', 'V8') and z in BACK_ROW_SETTER_ZONES):
                dst = pair[src]
                a['combo_fixed_from'] = src
                a['combo_code'] = dst
                if names.get(dst):
                    a['combo_name'] = names[dst]
                a['start_zone'] = _SWAP_START_ZONE[dst]
                swaps[(src, dst)] += 1
        for (src, dst), n in swaps.items():
            report.append((side, src, dst, n))
    return report

def fix_right_side_typos(session):
    """Match files: swap impossible X6/X8 codes for both Loyola and the opponent."""
    if session.get('type') != 'match':
        return []
    ls = loyola_side_for_session(session)
    if ls not in ('home', 'away'):
        return []
    opp = 'away' if ls == 'home' else 'home'
    def coded_zone(side, a):
        if side == ls:
            return a.get('rotation')                      # LUC setter zone as coded
        return a.get('rot_h') if opp == 'home' else (a.get('az_zone') or None)
    return swap_right_side(session, [ls, opp], coded_zone, prefer_coded=True)


# ── Points Added: position weights (share of each player's score, rows = 100%) ─
# Score = Σ weight × that category's points. Categories: Competition (practice
# drill wins, from competition.csv), Attack, Pass (serve receive), Block, Serve,
# Dig (defense), Set (value of the attack result off his sets + set errors).
PA_WEIGHTS = {
    'OUTSIDES':  {'Competition': .10, 'Attack': .25, 'Pass': .25, 'Block': .10, 'Serve': .15, 'Dig': .10, 'Set': .05},
    'MIDDLES':   {'Competition': .10, 'Attack': .39, 'Pass': .00, 'Block': .35, 'Serve': .10, 'Dig': .05, 'Set': .01},
    'OPPOSITES': {'Competition': .10, 'Attack': .35, 'Pass': .00, 'Block': .20, 'Serve': .25, 'Dig': .08, 'Set': .02},
    'SETTERS':   {'Competition': .20, 'Attack': .05, 'Pass': .00, 'Block': .15, 'Serve': .15, 'Dig': .10, 'Set': .35},
    'LIBEROS':   {'Competition': .10, 'Attack': .00, 'Pass': .55, 'Block': .00, 'Serve': .00, 'Dig': .25, 'Set': .10},
}
COMPETITION_CSV = os.path.join('private', 'competition.csv')   # Date, Number, Player, Wins, Losses (git-ignored: players can't read others' results)


def load_competition(path, sessions_index, players_by_season):
    """competition.csv -> {season: {pnum: {date: [wins, losses]}}}. Missing file -> {}."""
    import csv, datetime
    if not os.path.exists(path):
        return {}
    date_season = {}
    for e in sessions_index:
        if e.get('date'):
            date_season.setdefault(e['date'], e['season'])
    seasons_sorted = sorted({e['season'] for e in sessions_index if e.get('date')},
                            key=lambda se: max(x['date'] for x in sessions_index if x['season'] == se and x.get('date')))
    out, skipped = {}, 0
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            r = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
            ds = r.get('date', '')
            d = None
            for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
                try:
                    d = datetime.datetime.strptime(ds, fmt).strftime('%Y-%m-%d'); break
                except ValueError:
                    pass
            try:
                w, l = int(float(r.get('wins') or 0)), int(float(r.get('losses') or 0))
            except ValueError:
                d = None
            if not d:
                skipped += 1; continue
            se = date_season.get(d) or (seasons_sorted[-1] if seasons_sorted else None)
            roster = players_by_season.get(se, {})
            name, num = r.get('player', '').lower(), r.get('number', '').lstrip('#')
            pn = next((n for n, p in roster.items() if not p.get('virtual') and p.get('name', '').lower() == name), None) if name else None
            pn = pn or (num if num in roster else None)
            if not pn:
                skipped += 1; continue
            c = out.setdefault(se, {}).setdefault(pn, {}).setdefault(d, [0, 0])
            c[0] += w; c[1] += l
    if skipped:
        print(f"  ⚠  competition.csv: skipped {skipped} row(s) (bad date/number or unknown player)", file=sys.stderr)
    return out

# ── Competition from the coaches' Wins Leaderboard (6 v 6 tab) ─────────────────
# private/wins_leaderboard.xlsx (git-ignored), tab "6 v 6": season points per player by position
# (POINTS = # of 6v6 wins + bonus). Competition (Points Added) = (his points - his position's average) / 2.
# Exceptions (missed a lot of practice): points scaled to a full practice schedule (points x practices held /
# practices he was at) and left out of the position averages. Practices attended = practice files he is in.
COMP_6V6 = {
    'season': 'Fall 2027',
    'xlsx': os.path.join('private', 'wins_leaderboard.xlsx'),
    'sheet': '6 v 6',
    'scale': ['2', '13'],            # Ben Winokur, Thomas Vesty: missed practices this semester
    # names / nicknames used on the sheet -> roster name
    'aliases': {'matt': 'Matt Kelly', 'owen': 'Owen Smith', 'jake': 'Jake Read', 'nicola': 'Nicola Konstantinov',
                'ayden': 'Ayden DesLauriers', 'tyler': 'Tyler Howard', 'chacinksi': 'Stanislaw Chacinski',
                'chacinski': 'Stanislaw Chacinski', 'van buren': 'Caden Van Buren', 'sosa': 'Aleksandar Sosa',
                'lukas': 'Lukas Anderson', 'luka': 'Luka Lojic', 'sergej': 'Serguei Jokanovic',
                'smits': 'Alex Smits Van Oyen', 'vesty': 'Thomas Vesty', 'ak': 'Aidan Klein', 'hatch': 'Will Hatch',
                'gob': "Gavin O'Brien", 'winokur': 'Ben Winokur'},
}


def read_xlsx_sheet(path, sheet_name):
    """Rows (lists of cell values) of one sheet, stdlib only (zipfile + XML). Sheet name match ignores case/spaces."""
    import zipfile, xml.etree.ElementTree as ET
    M = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rid = next((sh.get(R + 'id') for sh in wb.iter(M + 'sheet')
                if (sh.get('name') or '').strip().lower() == sheet_name.strip().lower()), None)
    if not rid:
        return None
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target = next(r.get('Target') for r in rels if r.get('Id') == rid)
    target = target.lstrip('/')
    target = target if target.startswith('xl/') else 'xl/' + target
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(M + 'si'):
            shared.append(''.join(t.text or '' for t in si.iter(M + 't')))
    rows = []
    for row in ET.fromstring(z.read(target)).iter(M + 'row'):
        vals = {}
        for c in row.iter(M + 'c'):
            ref = c.get('r') or ''
            col = 0
            for ch in ref:
                if ch.isalpha():
                    col = col * 26 + (ord(ch.upper()) - 64)
            v = c.find(M + 'v')
            t = c.get('t')
            if t == 's' and v is not None:
                val = shared[int(v.text)]
            elif t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter(M + 't'))
            elif v is not None:
                try:
                    val = float(v.text)
                except (TypeError, ValueError):
                    val = v.text
            else:
                val = None
            vals[col - 1] = val
        if vals:
            rows.append([vals.get(i) for i in range(max(vals) + 1)])
    return rows


def load_competition_6v6(base_dir, output):
    """-> {season: {'players': {pnum: {...}}, 'groups': {group: avg}, 'note': str}} or {}."""
    cfg = COMP_6V6
    path = os.path.join(base_dir, cfg['xlsx'])
    if not os.path.exists(path):
        return {}
    rows = read_xlsx_sheet(path, cfg['sheet'])
    if not rows:
        print(f"  ⚠  {cfg['xlsx']}: tab '{cfg['sheet']}' not found", file=sys.stderr)
        return {}
    se = cfg['season']
    roster = {n: p for n, p in (output['players_by_season'].get(se) or {}).items() if not p.get('virtual')}
    by_name = {p['name'].lower(): n for n, p in roster.items()}
    def to_num(raw):
        k = str(raw or '').strip().lower()
        if not k:
            return None
        full = cfg['aliases'].get(k, k)
        if full.lower() in by_name:
            return by_name[full.lower()]
        hits = [n for n, p in roster.items() if k in p['name'].lower().split()]
        return hits[0] if len(hits) == 1 else None
    # header row: position names in the even columns, 'Points' next to each
    hi = next((i for i, r in enumerate(rows) if any(str(x or '').strip().lower() == 'points' for x in r)), None)
    if hi is None:
        return {}
    head = rows[hi]
    groups = [(c, str(head[c]).strip()) for c in range(len(head) - 1)
              if head[c] and str(head[c + 1] or '').strip().lower() == 'points']
    pts, grp_of, unknown = {}, {}, []
    for r in rows[hi + 1:]:
        for c, g in groups:
            name = r[c] if c < len(r) else None
            val = r[c + 1] if c + 1 < len(r) else None
            if name is None or not isinstance(val, (int, float)):
                continue
            pn = to_num(name)
            if not pn:
                unknown.append(str(name)); continue
            pts[pn], grp_of[pn] = float(val), g
    if unknown:
        print(f"  ⚠  6v6 tab: no roster match for {', '.join(unknown)} (add them to COMP_6V6 aliases)", file=sys.stderr)
    # practices held / attended this season (attended = he is in that practice file)
    prac = {x['id']: x['date'] for x in output['sessions'] if x.get('season') == se and x.get('type') == 'practice' and x.get('date')}
    held = sorted(set(prac.values()))
    att = defaultdict(set)
    for key in output['player_session']:
        pn, sid = key.split('|', 1)
        if sid in prac:
            att[pn].add(prac[sid])
    scale = set(cfg['scale'])
    out = {}
    for pn, v in pts.items():
        a = len(att[pn])
        adj = v * len(held) / a if (pn in scale and a) else v
        out[pn] = {'pts': v, 'adj': round(adj, 2), 'grp': grp_of[pn], 'att': a, 'tot': len(held),
                   'exc': pn in scale, 'dates': sorted(att[pn])}
    avgs = {}
    for g in {x['grp'] for x in out.values()}:
        xs = [x['pts'] for pn, x in out.items() if x['grp'] == g and pn not in scale]
        xs = xs or [x['adj'] for x in out.values() if x['grp'] == g]
        avgs[g] = round(sum(xs) / len(xs), 2)
    for pn, x in out.items():
        x['avg'] = avgs[x['grp']]
        x['comp'] = round((x['adj'] - x['avg']) / 2, 2)
    exc_txt = '; '.join(f"{roster[pn]['name']} ({out[pn]['att']} of {len(held)} practices): {out[pn]['pts']:.0f} -> {out[pn]['adj']:.1f}"
                        for pn in cfg['scale'] if pn in out)
    note = (f"{se} competition = 6 v 6 wins from the coaches' Wins Leaderboard (6 v 6 tab), compared with the "
            f"average of the player's position group on that sheet: (points - group average) / 2. "
            f"Players who missed a lot of practice had their points scaled to a full schedule of {len(held)} practices "
            f"(points x {len(held)} / practices attended) and are left out of the group averages: {exc_txt}. "
            f"Practices attended = practice files the player appears in. It counts on the practice dates he attended "
            f"that are in view. Matches: his set record in the sets he played, each set counted by his share of its "
            f"rallies (a libero gets the whole set if he played it; sets to 15 count as full sets): "
            f"(sets won - sets lost) / 2. With practices and matches both selected, the two numbers are added.")
    print(f"  6v6 competition {se}: {len(out)} players, groups " + ', '.join(f"{g} {a:.1f}" for g, a in sorted(avgs.items())), file=sys.stderr)
    return {se: {'players': out, 'groups': avgs, 'note': note}}


# ── Match competition: set record for the sets a player was on the court ────────
# For every match set (final score = last point line of the set, parse_dvw set_scores; short sets to 15 count
# as full sets) each player gets his SHARE of that set's rallies (rallies with him in Loyola's lineup / rallies
# in the set); a libero (not in the lineup, incl. anyone playing libero that set) gets the whole set if he
# touched the ball in it. Competition from
# matches = (sets won - sets lost) / 2 using those shares (50% baseline). It is ADDED to the practice measure.
def compute_match_competition(session, ls):
    """{pnum: {set: [won_share, lost_share]}} for one match, or {} without set scores."""
    scores = session.get('set_scores') or {}
    if not scores or ls not in ('home', 'away'):
        return {}
    by_set = defaultdict(lambda: defaultdict(list))
    for a in session.get('actions', []):
        if isinstance(a.get('set_number'), int):
            by_set[a['set_number']][a.get('rally_seq', 0)].append(a)
    out = defaultdict(dict)
    for sn, rallies in by_set.items():
        sc = scores.get(str(sn))
        if not sc or sc[0] == sc[1]:
            continue
        won = (sc[0] > sc[1]) if ls == 'home' else (sc[1] > sc[0])
        on, total, touched = Counter(), 0, set()
        for rs, acts in rallies.items():
            lu = next((a.get('luc_lineup') for a in acts if a.get('luc_lineup')), None)
            if lu:
                total += 1
                for p in set(str(x) for x in lu):
                    on[p] += 1
            for a in acts:
                if a.get('team_side') == ls and a.get('player_num'):
                    touched.add(str(a['player_num']))
        share = {p: c / total for p, c in on.items()} if total else {}
        libero = set()
        for p in touched:
            if p not in share:        # touched the ball but never in the lineup = he was the libero in that set
                share[p] = 1.0; libero.add(p)
        for p, f in share.items():
            if f > 0:   # [won share, lost share, 1 = he played the set as the libero]
                out[p][str(sn)] = ([round(f, 3), 0] if won else [0, round(f, 3)]) + [1 if p in libero else 0]
    return out


# ── Coach-only virtual players ────────────────────────────────────────────────
# A second, position-specific copy of a real player, shown on the COACH dashboard
# only (build_player_portal.py skips them). Only the listed skills are copied, and
# the page leaves virtual players out of every team / position-group total so
# nothing is double counted. Their group (e.g. LIBEROS) gives them the right
# benchmark: the other players at that position.
VIRTUAL_PLAYERS = [
    {'num': '7L', 'src': '7', 'season': 'Fall 2027', 'name': 'Jake Read 2.0',
     'first_name': 'Jake', 'last_name': 'Read 2.0', 'position_code': '1', 'is_libero': True,
     'skills': ['R', 'RM', 'RQ', 'D', 'F', 'E'],      # passing, digs, free balls, sets
     'pa_skills': ['R', 'D', 'E', 'X']},               # Points Added touch types kept
]


def add_virtual_players(output, sets_out):
    vmeta = {}
    for v in VIRTUAL_PLAYERS:
        src, num, se = v['src'], v['num'], v['season']
        base = (output['players_by_season'].get(se) or {}).get(src)
        if not base:
            continue
        info = dict(base, number=num, name=v['name'], first_name=v['first_name'], last_name=v['last_name'],
                    position_code=v['position_code'], is_libero=v['is_libero'], virtual=True, virtual_of=src)
        output['players_by_season'][se][num] = info
        output['players'][num] = info
        keep = set(v['skills'])

        def copy_ps(ps, sid=None):
            sk = {k: x for k, x in (ps.get('skills') or {}).items() if k in keep}
            out = {k: x for k, x in ps.items() if k != 'skills'}
            out.update(player_num=num, skills=sk, virtual=True,
                       actions=sum(x.get('attempts', 0) for k, x in sk.items() if k not in ('RM', 'RQ')))
            if sid:
                out['session_id'] = sid
            return out

        sids = {e['id'] for e in output['sessions'] if e.get('season') == se}
        for key, ps in list(output['player_session'].items()):
            pn, sid = key.split('|', 1)
            if pn == src and sid in sids:
                c = copy_ps(ps, sid)
                if c['actions']:
                    output['player_session'][f'{num}|{sid}'] = c
        pse = output['player_season'].get(f'{src}|{se}')
        if pse:
            output['player_season'][f'{num}|{se}'] = copy_ps(pse)
        for e in output['sessions']:
            if e['id'] not in sids:
                continue
            if src in (e.get('fbso_by_player') or {}):
                e['fbso_by_player'][num] = list(e['fbso_by_player'][src])
            for sn, pl in (e.get('pts_added') or {}).items():
                if src in pl:
                    kk = {k: x for k, x in pl[src].items() if k.split('|')[0] in v['pa_skills']}
                    if kk:
                        pl[num] = kk
            for sn, se_d in (sets_out.get(e['id']) or {}).items():
                sp = (se_d.get('player_session') or {}).get(src)
                if sp:
                    c = copy_ps(sp)
                    if c['actions']:
                        se_d['player_session'][num] = c
                if src in (se_d.get('fbso_by_player') or {}):
                    se_d['fbso_by_player'][num] = list(se_d['fbso_by_player'][src])
        comp = (output.get('competition') or {}).get(se, {})
        if src in comp:
            comp[num] = comp[src]                    # same drill results as the real player
        vmeta[num] = {'src': src, 'season': se}
    output['virtual_players'] = vmeta


# Name spelling fixes: rosters typed wrong in the DVW files. Applied to every roster and action as soon as
# the data is loaded, so the dashboard, reports, PDFs and player portal all show the right spelling.
# (Renaming a current player also moves his portal password: rename his key in private/player_passwords.json.)
NAME_FIXES = {'Stainslaw': 'Stanislaw'}

def fix_names(raw):
    if not NAME_FIXES:
        return
    def fx(v):
        if isinstance(v, str):
            for bad, good in NAME_FIXES.items():
                v = v.replace(bad, good)
        return v
    def fix_roster(pl):
        for p in (pl or {}).values():
            if isinstance(p, dict):
                for f in ('name', 'first_name', 'last_name'):
                    if f in p:
                        p[f] = fx(p[f])
    fix_roster(raw.get('players'))
    for s in raw.get('sessions', []) + raw.get('scout_sessions', []):
        for key in ('players', 'home_players', 'away_players'):
            fix_roster(s.get(key))
        for a in s.get('actions', []):
            for f in ('player_name', 'player_first', 'player_last'):
                if a.get(f):
                    a[f] = fx(a[f])


def main(raw_path, out_path):
    print(f"Loading {raw_path} ...", file=sys.stderr)
    with open(raw_path) as f:
        raw = json.load(f)

    fix_names(raw)
    sessions_raw = raw.get('sessions', [])
    scouts_raw   = raw.get('scout_sessions', [])

    # Auto-correct impossible X6/X8 codes before anything is aggregated
    combo_fixes = {}
    for s in sessions_raw:
        rep = fix_right_side_typos(s)
        if rep:
            sid = f"{s['season']}|{s['file']}"
            combo_fixes[sid] = [{'side': 'LUC' if side == loyola_side_for_session(s) else 'OPP',
                                 'from': f, 'to': to, 'n': n} for side, f, to, n in rep]
            for side, f, to, n in rep:
                who = 'LUC' if side == loyola_side_for_session(s) else 'OPP'
                print(f"  Combo fix {s['file']} [{who}]: " + f"{f}->{to} x{n}", file=sys.stderr)

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
    for num, override in NAME_OVERRIDES.items():
        for season, roster in players_by_season.items():
            if num not in roster:
                continue
            # Season-scoped override ({season: name}) only applies to listed seasons;
            # a plain string applies to every season that jersey number appears in.
            if isinstance(override, dict):
                if season not in override:
                    continue
                correct_name = override[season]
            else:
                correct_name = override
            old = roster[num]['name']
            if old != correct_name:
                roster[num]['name'] = correct_name
                # Also fix first_name / last_name so no stale misspelling leaks into the output
                parts = correct_name.split(' ', 1)
                roster[num]['first_name'] = parts[0]
                roster[num]['last_name']  = parts[1] if len(parts) > 1 else ''
                print(f"  Name override: #{num} ({season}) '{old}' → '{correct_name}'", file=sys.stderr)
            # Also update name_canon so any variant maps to the override, scoped by
            # (num, variant) — safe here since name_canon lookup happens per (num, name)
            # and a season-scoped override's variant name doesn't occur in other seasons.
            name_canon[(num, old)] = correct_name

    # Apply manual position overrides — force correct position_code regardless
    # of what a particular DVW file happened to record.
    for num, correct_pos in POSITION_OVERRIDES.items():
        for season, roster in players_by_season.items():
            if num in roster:
                old_pos = roster[num]['position_code']
                if old_pos != correct_pos:
                    roster[num]['position_code'] = correct_pos
                    print(f"  Position override: #{num} ({season}) '{old_pos}' -> '{correct_pos}'", file=sys.stderr)

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
        sa = compute_setter_player_attacks(acts, ls)
        rot_stats = compute_match_rotation_stats(acts, ls) if s.get('type') == 'match' else None
        # Practices: the same setter -> hitter -> pass-quality buckets (all / fbso / perf / good / med / oos /
        # transition) as matches, so the player portal's phase filters count practice sets too
        # (no rotation tables for practices - only setter_atk is kept).
        sa_q = compute_match_rotation_stats(practice_rallies(acts), ls).get('setter_atk') if s.get('type') != 'match' else None
        session_rally[sid] = {'dig': dig, 'fbso': fbso, 'call_atk': ca, 'setter_atk': sa, 'rot_stats': rot_stats, 'setter_atk_q': sa_q}
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

    # ── Opponent-side match data (for the Reports "Opponent" view) ─────────────
    # Same report structures as LUC, computed for the other team in each match
    # file that codes both sides. Opponent rosters are often incomplete in the
    # DVW (placeholder names, no positions), so names fall back to "#<num>" and
    # positions are inferred from what each player actually did.
    def _opp_rotation(a, opp_side, set_lineups):
        if opp_side == 'home':
            return a.get('rot_h')
        lu, sn = a.get('opp_lineup'), a.get('set_number')
        start = (set_lineups.get(sn) or set_lineups.get(str(sn))) if sn else None
        if not lu or not start or len(start) < 6:
            return None
        try:
            return (6 - start.index(lu[0])) % 6 + 1
        except ValueError:
            return None

    def _is_placeholder_name(name):
        n = (name or '').strip()
        letters = n.replace(' ', '').lower()
        return (not n) or n.startswith('#') or len(set(letters)) <= 1

    def _opp_roster(s, opp_side, opp_acts):
        src = s.get('away_players' if opp_side == 'away' else 'home_players') or {}
        from collections import Counter as _C
        by_p = defaultdict(list)
        for a in opp_acts:
            if a.get('player_num') not in (None, ''):
                by_p[a['player_num']].append(a)
        total = len(opp_acts) or 1
        roster = {}
        for pn, acts_p in by_p.items():
            info = src.get(pn, {})
            name = info.get('name', '')
            known = not _is_placeholder_name(name)
            skills = _C(a.get('skill_code') for a in acts_p)
            pos = info.get('position_code', '') if known else ''
            is_lib = bool(info.get('is_libero')) if known else False
            if pos not in ('1', '2', '3', '4', '5'):
                atk_z = _C(str(a.get('start_zone') or '') for a in acts_p if a.get('skill_code') == 'A')
                n_e, n_a, n_r = skills.get('E', 0), skills.get('A', 0), skills.get('R', 0)
                if n_e >= 5 and n_e >= 0.3 * len(acts_p):
                    pos = '5'
                elif n_a >= 3:
                    z = atk_z.most_common(1)[0][0]
                    pos = '4' if z == '3' else '3' if z in ('2', '1') else '2'
                elif n_r >= 5:
                    pos, is_lib = '1', True
                else:
                    pos = '2'
            if pos == '1':
                is_lib = True
            if len(acts_p) / total >= 0.5 and not known:
                disp, last = 'Opponent (no jerseys coded)', 'Team'
            elif known:
                disp = name
                parts = name.split(' ', 1)
                last = parts[1] if len(parts) > 1 else name
            else:
                disp = last = f'#{pn}'
            roster[pn] = {'number': pn, 'name': disp, 'last_name': last, 'first_name': '',
                          'position_code': pos, 'is_libero': is_lib}
        return roster

    def opp_bundle(s, acts, roster=None):
        """Opponent-side report data for a match (or one set of it): rotation stats,
        FBSO, per-player stats, LUC comparison totals. None if the file doesn't code
        the other team. Pass the full-match roster when computing a single set."""
        ls = loyola_side_for_session(s)
        if s.get('type') != 'match' or ls not in ('home', 'away'):
            return None
        opp_side = 'away' if ls == 'home' else 'home'
        opp_acts = [a for a in acts if a.get('team_side') == opp_side]
        if roster is None and len(opp_acts) < 20:
            return None
        if not opp_acts:
            return None
        set_lu = s.get('away_set_lineups' if opp_side == 'away' else 'home_set_lineups') or {}
        # DataVolley convention: rotation = court zone of the setter.
        #   opponent at home → home rotation field (rot_h)
        #   opponent away    → last 'az{N}' marker, else the away rotation field
        #   (fallback for old parses: count rotations from the set's starting lineup)
        acts_o = []
        for a in acts:
            b = dict(a)
            if opp_side == 'home':
                rot = a.get('rot_h')
            else:
                rot = a.get('az_zone') or a.get('rot_v')
            b['rotation'] = rot if rot else _opp_rotation(a, opp_side, set_lu)
            acts_o.append(b)
        dig_o, fbso_o = compute_rally_sequences(acts_o, opp_side)
        if roster is None:
            roster = _opp_roster(s, opp_side, opp_acts)
        entry = {
            'rot_stats': compute_match_rotation_stats(acts_o, opp_side),
            'fbso':      fbso_o,
            'roster':    roster,
            'luc_stats': compute_opp_stats({'actions': acts}, opp_side),   # LUC totals, for comparison KPIs
        }
        store = defaultdict(lambda: {'actions': 0, 'skills': {}, 'rcv_zones': {}})
        for a in opp_acts:
            pn = a.get('player_num', '')
            if pn in (None, '') or pn not in roster:
                continue
            ps_ = store[pn]
            ps_['actions'] += 1
            add_action(ps_['skills'], a, ps_['rcv_zones'])
            add_rcv_extras((ps_,), a, True)
        for pn, dstats in dig_o.items():
            if pn in store:
                store[pn]['dig_kill_rallies']  = dstats['dig_kill_rallies']
                store[pn]['dig_total_rallies'] = dstats['dig_rallies']
        for ps_ in store.values():
            derive(ps_['skills'])
            derive_zones(ps_['rcv_zones'], is_match=True)
        return entry, dict(store)

    opp_match = {}           # sid -> {'rot_stats','fbso','roster','luc_stats'}
    opp_player_session = {}  # "pnum|sid" -> same shape as player_session entries
    for s in sessions_raw:
        sid = f"{s['season']}|{s['file']}"
        res = opp_bundle(s, s.get('actions', []))
        if not res:
            continue
        opp_match[sid], store = res
        for pn, ps_ in store.items():
            opp_player_session[f"{pn}|{sid}"] = {'player_num': pn, 'session_id': sid, **ps_}

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
            'fbso_by_player': fbso['by_player'],      # receiver → [rcv rallies, FBSO kills]
            'call_attacks':   session_rally[sid]['call_atk'],
            'setter_attacks': session_rally[sid]['setter_atk'],  # setter_pnum -> attacker_pnum -> combo -> {attempts,kills,errors,blocks}
            'match_rot_stats': session_rally[sid]['rot_stats'],  # None for practice
            'setter_tab':     compute_setter_tab(s.get('actions', []), ls),  # Setter tab: setter → situation → combos/calls
            'pts_added':      compute_points_added(s.get('actions', []), ls),  # Reports: Points Added — set → player → key → [n, wins]
            'pass_hit':       compute_pass_hit(s.get('actions', []), ls),      # Reports: Pass & Hit — first-ball attacks by the passer vs others
            # opp_stats: only populated when the file contains both teams' actions
            'opp_stats': compute_opp_stats(s, ls)
                         if s.get('type') == 'match' and
                            any(a.get('team_side') != ls for a in s.get('actions', []))
                         else None,
            # Opponent view (Reports tab) — None when the file doesn't code the other side
            'match_rot_stats_opp': opp_match.get(sid, {}).get('rot_stats'),
            'opp_fbso_rcv':        opp_match[sid]['fbso']['rcv_rallies'] if sid in opp_match else None,
            'opp_fbso_kills':      opp_match[sid]['fbso']['fbso_kills']  if sid in opp_match else None,
            'opp_fbso_by_player':  opp_match[sid]['fbso']['by_player']   if sid in opp_match else None,
            'opp_roster':          opp_match.get(sid, {}).get('roster'),
            'combo_fixes':         combo_fixes.get(sid),   # X6/X8 typo corrections applied (or skipped)
            'luc_stats':           opp_match.get(sid, {}).get('luc_stats'),
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
            add_rcv_extras((psk, pse, ss), action, stype == 'match')

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
    _SIDE_SKIP = {'university', 'college', 'men', 'mens', 'women', 'womens', 'the', 'of', 'at', 'a', 'and', 'volleyball'}

    def _team_words(name):
        import re as _re
        return [w for w in _re.findall(r"[a-z0-9&]+", (name or '').lower().replace("'s", 's')) if w not in _SIDE_SKIP]

    def _name_score(opp, team):
        """How well the scout label (full name or abbreviation like UCSD / LW / MCK / Pepp) matches a team name."""
        ow, tw = _team_words(opp), _team_words(team)
        if not ow or not tw:
            return 0
        if ow == tw:
            return 100
        if all(w in tw for w in ow):
            return 60
        if len(ow) == 1:
            ab = ow[0]
            full = [w for w in __import__('re').findall(r"[a-z0-9&]+", (team or '').lower()) if w not in ('the', 'of', 'at', 'a', 'and', 's', 'men', 'mens', 'volleyball')]
            if ab == ''.join(w[0] for w in full) or ab == ''.join(w[0] for w in tw):
                return 50                                   # initials: UCSD, NKU, BSU
            if len(ab) >= 3 and any(w.startswith(ab) for w in tw):
                return 40                                   # prefix: MCK, Pepp, Man
            for w in tw:                                    # letters in order within a word: LW → Lindenwood
                if w[0] == ab[0]:
                    it = iter(w)
                    if all(ch in it for ch in ab):
                        return 25
        return 0

    def resolve_opp_sides(opp_groups):
        """
        Decide which side (home/away) is the scouted team in every scout file.
        1) Roster overlap: with 2+ files for an opponent, the scouted team is the side whose
           players show up again in that opponent's other files.
        2) Otherwise the team name that best matches the scout label.
        """
        def roster(sess, side):
            return {(p.get('name') or '').lower() for p in (sess.get(f'{side}_players') or {}).values() if p.get('name')}
        for opp, sess_list in opp_groups.items():
            for s_ in sess_list:
                sc = {sd: 0 for sd in ('home', 'away')}
                if len(sess_list) >= 2:
                    others = [roster(o, 'home') | roster(o, 'away') for o in sess_list if o is not s_]
                    for sd in sc:
                        mine = roster(s_, sd)
                        sc[sd] = sum(1 for o in others if len(mine & o) >= 3)
                if sc['home'] != sc['away']:
                    s_['_opp_side'] = max(sc, key=sc.get)
                    continue
                h, a = _name_score(opp, s_.get('home_team')), _name_score(opp, s_.get('away_team'))
                s_['_opp_side'] = 'home' if h > a else 'away'

    def opp_side_for_session(session):
        """Which team_side ('home' or 'away') is the scouted opponent (set by resolve_opp_sides)."""
        if session.get('_opp_side'):
            return session['_opp_side']
        h, a = _name_score(session.get('opponent'), session.get('home_team')), _name_score(session.get('opponent'), session.get('away_team'))
        return 'home' if h > a else 'away'

    def file_setters(session, side):
        """The scouted team's setter(s) in one file: whoever made a big share of that side's sets
        (one setter in a 5-1, two in a 6-2)."""
        cnt = Counter(str(a.get('player_num')) for a in session.get('actions', [])
                      if a.get('team_side') == side and a.get('skill_code') == 'E' and a.get('player_num'))
        if not cnt:
            return []
        top = cnt.most_common(1)[0][1]
        return [p for p, n in cnt.most_common(2) if n >= max(10, 0.3 * top)]

    def get_opp_rotation(action, opp_side, set_lineups, setters=None):
        """Scouted team's rotation for this action, numbered like Loyola's (R1 = setter in
        zone 1, R2 = zone 6, R3 = zone 5, R4 = zone 4, R5 = zone 3, R6 = zone 2).
        The setter's zone comes from that side's lineup on the action (lineup slot i = zone i+1).
        With two setters on court (6-2) the back-row one is the setter. Home side falls back to
        the coded home setter zone (field 9)."""
        lineup = action.get('luc_lineup') if opp_side == 'home' else action.get('opp_lineup')
        zone = None
        if lineup and setters:
            pos = [i + 1 for i, p in enumerate(lineup) if str(p) in setters]
            if len(pos) == 1:
                zone = pos[0]
            elif len(pos) > 1:
                back = [z for z in pos if z in (1, 6, 5)]
                zone = back[0] if back else pos[0]
        if zone is None and opp_side == 'home' and action.get('rot_h'):
            zone = action.get('rot_h')
        try:
            return SETTER_ZONE_TO_ROT.get(int(zone))
        except (TypeError, ValueError):
            return None

    def atk_zone_cat(action, rot=None):
        """Categorize an attack: Left / Quick / Right in the front row; the back row is split
        into Dball (X8/V8, right back row) and BIC (XP pipe family, middle/left back row).
        rot (1-6): a D-ball can't happen in Rotation 1-3 (the right side is front row there), so an
        attack that only LOOKS like a D-ball by its start zone counts as Right in those rotations."""
        sz = str(action.get('start_zone') or '')
        cc = action.get('combo_code') or ''
        if cc in ('X8', 'V8'): return 'Dball'
        if sz in ('1', '9'): return 'Right' if rot in (1, 2, 3) else 'Dball'
        if cc in ('XP', 'XB', 'XR', 'VP', 'VB', 'VR', 'X0', 'V0') or sz in ('5', '6', '7', '8'): return 'BIC'
        if sz == '4':   return 'Left'
        if sz == '3':   return 'Quick'
        if sz == '2':   return 'Right'
        return 'Other'

    def serve_group(a):
        """Serve type of a serve / reception action: 'spin' (Q = jump spin) or 'float' (M jump float, H float)."""
        t = (a or {}).get('technique') or ''
        return 'spin' if t == 'Q' else ('float' if t in ('M', 'H') else '')

    def in_red_zone(a):
        """Red zone = the last few points of a set: before this rally the leading team has 20+ points
        (10+ in set 5) and the teams are within 3 points. Score = pts_h / pts_a from parse_dvw (the point
        lines in the file). Returns 1 / 0."""
        h, w = a.get('pts_h'), a.get('pts_a')
        if not isinstance(h, int) or not isinstance(w, int):
            return 0
        thr = 10 if a.get('set_number') == 5 else 20
        return 1 if max(h, w) >= thr and abs(h - w) <= 3 else 0

    def compute_scout_rotation_stats(sessions_for_opp, rz_only=False):
        """
        Compute per-rotation and team-level stats for a scouted opponent
        across all their scout sessions.
        Returns dict with keys 'team', 'rotations', 'setters'.
        """
        def new_rot():
            return {
                'atk':  {'att':0,'k':0,'e':0,'bk':0,'by_zone':{'Left':0,'Quick':0,'Right':0,'Dball':0,'BIC':0,'Dump':0}},
                'rcv':  {'att':0,'in_sys':0,'fbso_rallies':0,'fbso_kills':0,'zones':{},'by':{}},
                'dig':  {'att':0,'err':0},
                'set':  {'att':0,'by_zone':{'Left':0,'Quick':0,'Right':0,'Dball':0,'BIC':0}},
            }

        rotations = {str(r): new_rot() for r in range(1, 7)}
        setters   = {}  # jersey_str → {name, sets, rot_counts, rcv_in_sys, rcv_att, rotations_set}
        team = {'att':0,'k':0,'e':0,'bk':0,'so_rallies':0,'so_kills':0,
                'fbso_rallies':0,'fbso_kills':0,'rcv_att':0,'rcv_in_sys':0}
        # same receive numbers split by the serve they passed: 'spin' (jump spin) / 'float' (jump float + float)
        team_by = {g: {'rcv_att':0,'rcv_in_sys':0,'fbso_rallies':0,'fbso_kills':0,'so_rallies':0,'so_kills':0} for g in ('spin', 'float')}

        for session in sessions_for_opp:
            opp_side   = opp_side_for_session(session)
            set_lineups = session.get('away_set_lineups' if opp_side == 'away' else 'home_set_lineups', {})
            setters_f = file_setters(session, opp_side)
            # Convert string keys to int if needed
            set_lineups_int = {}
            for k, v in set_lineups.items():
                try:
                    set_lineups_int[int(k)] = v
                except:
                    set_lineups_int[k] = v
            actions = session.get('actions', [])

            # Group by rally_seq
            rally_groups = defaultdict(list)
            for a in actions:
                rally_groups[a.get('rally_seq', 0)].append(a)

            for rs in sorted(rally_groups):
                rally = rally_groups[rs]
                if rz_only and not (rally and in_red_zone(rally[0])):
                    continue                      # Red zone version: only rallies played in the red zone
                opp_acts = [a for a in rally if a.get('team_side') == opp_side]
                if not opp_acts:
                    continue

                # Determine rotation for this rally from the first receive action or first opp action
                first_rcv  = next((a for a in opp_acts if a.get('skill_code') == 'R'), None)
                first_act  = opp_acts[0]
                rot_action = first_rcv or first_act
                opp_rot    = get_opp_rotation(rot_action, opp_side, set_lineups_int, setters_f)
                rot_key    = str(opp_rot) if opp_rot and 1 <= opp_rot <= 6 else None

                # ── Reception ──────────────────────────────────────────────────
                has_rcv = bool(first_rcv)
                sg = serve_group(first_rcv) if has_rcv else ''
                tb = team_by[sg] if sg else None
                rbb = rotations[rot_key]['rcv']['by'].setdefault(sg, {'att': 0, 'in_sys': 0, 'fbso_kills': 0}) if (sg and rot_key) else None
                if has_rcv:
                    ev  = first_rcv.get('evaluation','')
                    in_sys = 1 if ev in ('#','+') else 0
                    zone   = str(first_rcv.get('end_zone') or '')
                    team['rcv_att']    += 1
                    team['rcv_in_sys'] += in_sys
                    if tb is not None:
                        tb['rcv_att'] += 1; tb['rcv_in_sys'] += in_sys; tb['fbso_rallies'] += 1; tb['so_rallies'] += 1
                    if rbb is not None:
                        rbb['att'] += 1; rbb['in_sys'] += in_sys
                    if rot_key:
                        rb = rotations[rot_key]['rcv']
                        rb['att']    += 1
                        rb['in_sys'] += in_sys
                        rb['fbso_rallies'] += 1
                        # Zone-level reception
                        if zone in ('1','5','6'):
                            if zone not in rb['zones']:
                                rb['zones'][zone] = {'att':0,'in_sys':0,'fbso':0}
                            rb['zones'][zone]['att']    += 1
                            rb['zones'][zone]['in_sys'] += in_sys

                # ── FBSO ───────────────────────────────────────────────────────
                if has_rcv:
                    first_atk = next((a for a in opp_acts if a.get('skill_code') == 'A'), None)
                    is_fbso   = bool(first_atk and first_atk.get('evaluation') == '#')
                    team['fbso_rallies'] += 1
                    if is_fbso:
                        team['fbso_kills'] += 1
                        if tb is not None: tb['fbso_kills'] += 1
                        if rbb is not None: rbb['fbso_kills'] += 1
                    if rot_key:
                        rotations[rot_key]['rcv']['fbso_kills'] += (1 if is_fbso else 0)
                        if zone in ('1','5','6'):
                            rotations[rot_key]['rcv']['zones'][zone]['fbso'] += (1 if is_fbso else 0)

                # ── Sideout ────────────────────────────────────────────────────
                # Find the last decisive action (kill=# or error==) in the rally.
                # Sideout = opp scored = last kill by opp OR last error by non-opp.
                if has_rcv:
                    team['so_rallies'] += 1
                    srv_side = 'home' if opp_side == 'away' else 'away'
                    for a in reversed(rally):
                        ev = a.get('evaluation', '')
                        side = a.get('team_side', '')
                        if ev == '#':
                            # Kill: opp scored if opp killed
                            team['so_kills'] += (1 if side == opp_side else 0)
                            if tb is not None: tb['so_kills'] += (1 if side == opp_side else 0)
                            break
                        elif ev == '=':
                            # Error: opp scored if server (non-opp) errored
                            team['so_kills'] += (1 if side == srv_side else 0)
                            if tb is not None: tb['so_kills'] += (1 if side == srv_side else 0)
                            break

                # ── Attacks ───────────────────────────────────────────────────
                for a in opp_acts:
                    if a.get('skill_code') != 'A':
                        continue
                    ev  = a.get('evaluation', '')
                    cat = atk_zone_cat(a, int(rot_key) if rot_key else None)
                    k   = 1 if ev == '#' else 0
                    er  = 1 if ev == '=' else 0
                    bk  = 1 if ev == '/' else 0
                    team['att'] += 1
                    team['k']   += k
                    team['e']   += er
                    team['bk']  += bk
                    if rot_key:
                        rb = rotations[rot_key]['atk']
                        rb['att'] += 1
                        rb['k']   += k
                        rb['e']   += er
                        rb['bk']  += bk
                        rb['by_zone'][cat] = rb['by_zone'].get(cat, 0) + 1

                # ── Setter actions ──────────────────────────────────────────────
                for a in opp_acts:
                    if a.get('skill_code') != 'E':
                        continue
                    s_rot  = get_opp_rotation(a, opp_side, set_lineups_int, setters_f)
                    s_key  = str(s_rot) if s_rot and 1 <= s_rot <= 6 else None
                    pnum   = a.get('player_num', '')
                    pname  = a.get('player_name', '') or f'#{pnum}'
                    if pnum not in setters:
                        setters[pnum] = {'name':pname,'sets':0,'rcv_att':0,'rcv_in_sys':0,
                                         'rot_count':defaultdict(int)}
                    setters[pnum]['sets'] += 1
                    if s_key:
                        setters[pnum]['rot_count'][s_key] += 1
                    # Find the next attack in this rally for set distribution
                    a_idx = opp_acts.index(a)
                    nxt = next((x for x in opp_acts[a_idx+1:] if x.get('skill_code') == 'A'), None)
                    if nxt and s_key:
                        cat = atk_zone_cat(nxt, s_rot)
                        rotations[s_key]['set']['att']    += 1
                        rotations[s_key]['set']['by_zone'][cat] = \
                            rotations[s_key]['set']['by_zone'].get(cat, 0) + 1

                # ── Digs ───────────────────────────────────────────────────────
                for a in opp_acts:
                    if a.get('skill_code') != 'D':
                        continue
                    ev = a.get('evaluation', '')
                    if rot_key:
                        rb = rotations[rot_key]['dig']
                        rb['att'] += 1
                        if ev == '=':
                            rb['err'] += 1

        # ── Derive percentages ─────────────────────────────────────────────────
        t = team
        t['eff']       = round((t['k']-t['e']-t['bk'])/t['att'],3) if t['att'] else 0
        t['k_pct']     = round(t['k']/t['att']*100,1)              if t['att'] else 0
        t['fbso_pct']  = round(t['fbso_kills']/t['fbso_rallies']*100,1) if t['fbso_rallies'] else 0
        t['so_pct']    = round(t['so_kills']/t['so_rallies']*100,1)     if t['so_rallies'] else 0
        t['in_sys_pct']= round(t['rcv_in_sys']/t['rcv_att']*100,1)     if t['rcv_att'] else 0
        for g, b in team_by.items():
            b['fbso_pct']   = round(b['fbso_kills']/b['fbso_rallies']*100,1) if b['fbso_rallies'] else None
            b['so_pct']     = round(b['so_kills']/b['so_rallies']*100,1)     if b['so_rallies'] else None
            b['in_sys_pct'] = round(b['rcv_in_sys']/b['rcv_att']*100,1)     if b['rcv_att'] else None
        t['by'] = team_by

        for rk, rv in rotations.items():
            a = rv['atk']
            a['eff']   = round((a['k']-a['e']-a['bk'])/a['att'],3) if a['att'] else 0
            a['k_pct'] = round(a['k']/a['att']*100,1)              if a['att'] else 0
            r = rv['rcv']
            r['in_sys_pct']  = round(r['in_sys']/r['att']*100,1)              if r['att'] else 0
            r['fbso_pct']    = round(r['fbso_kills']/r['fbso_rallies']*100,1) if r['fbso_rallies'] else 0
            for g, b in r['by'].items():
                b['in_sys_pct'] = round(b['in_sys']/b['att']*100,1)     if b['att'] else None
                b['fbso_pct']   = round(b['fbso_kills']/b['att']*100,1) if b['att'] else None
            for zk, zv in r['zones'].items():
                zv['in_sys_pct'] = round(zv['in_sys']/zv['att']*100,1) if zv['att'] else 0
                zv['fbso_pct']   = round(zv['fbso']/zv['att']*100,1)   if zv['att'] else 0
            d = rv['dig']
            d['err_pct'] = round(d['err']/d['att']*100,1) if d['att'] else 0

        # Clean setters: convert defaultdict to dict
        for sv in setters.values():
            sv['rot_count']  = dict(sv['rot_count'])
            rots = sv['rot_count']
            total_sets = sv['sets']
            sv['arot'] = round(sum(int(k)*v for k,v in rots.items())/total_sets,1) if total_sets else 0

        return {'team': team, 'rotations': rotations, 'setters': setters}

    # Attack codes that tell us a player's position (VM code names: "O" = opposite, "OH" = outside)
    POS_CODES = {
        'MB':  {'X1', 'X2', 'X7', 'XM', 'CB', 'CF', 'CS'},
        'OPP': {'X6', 'V6', 'X8', 'V8', 'XS', 'XO', 'XQ', 'X3', 'X4', 'XD'},
        'OH':  {'X5', 'V5', 'XP', 'VP', 'X9', 'XT', 'XC', 'X0', 'V0', 'XB', 'VB', 'XR', 'VR'},
    }
    POS_ZONE = {'4': 'OH', '5': 'OH', '6': 'OH', '7': 'OH', '8': 'OH', '3': 'MB', '2': 'OPP', '1': 'OPP', '9': 'OPP'}

    # Lineup order around the setter (lineup slot i = zone i+1, so +1 = the player after the setter in the
    # serving order). Learned from Loyola's match files, where positions are listed:
    # S, +1 OH, +2 MB, +3 OPP, +4 OH, +5 MB  (the middle is one rotation ahead of the setter, the outside
    # one behind). The opposite is always +3; the two other pairs are {+1,+4} and {+2,+5}.
    PAIR_A, PAIR_B = (1, 4), (2, 5)
    LOYOLA_MB_PAIR = PAIR_B

    def compute_scout_positions(sessions_for_opp):
        """Best guess at each scouted player's position (scout files don't list positions):
          L = libero flag, S = main setter,
          OH / MB / OPP = where his attacks come from (set codes first, start zone if uncoded) COMBINED with
            where he stands in the lineup relative to the setter (opposite = 3 spots from the setter; which of
            the other two pairs is the middle pair is decided by that team's own hitting, and falls back to
            Loyola's order S-OH-MB-OPP-OH-MB when the hitting doesn't say),
          DS = passes/digs but rarely attacks, Serve = mostly comes in to serve.
        Returns {jersey: position}."""
        votes = defaultdict(Counter)                 # attack-code votes
        touches = defaultdict(Counter)
        struct = defaultdict(Counter)                # lineup-slot votes (rallies on court)
        lib, setter_files, seen = Counter(), Counter(), set()
        file_slots = []                              # per file: {player: Counter(offset)}
        for session in sessions_for_opp:
            key = (session.get('file'), session.get('date'))
            if key in seen:
                continue
            seen.add(key)
            side = opp_side_for_session(session)
            parse_side = session.get('luc_side') or 'home'
            for num, pl in (session.get(f'{side}_players') or {}).items():
                if pl.get('is_libero') or pl.get('position_code') == '1':
                    lib[str(num)] += 1
            setters_f = [str(x) for x in file_setters(session, side)]
            for pnum in setters_f:
                setter_files[pnum] += 1
            rallies = defaultdict(list)
            for a in session.get('actions', []):
                rallies[a.get('rally_seq', 0)].append(a)
                if a.get('team_side') != side or not a.get('player_num'):
                    continue
                pn, sk = str(a['player_num']), a.get('skill_code')
                touches[pn][sk] += 1
                if sk != 'A':
                    continue
                cc = a.get('combo_code') or ''
                pos = next((k for k, v in POS_CODES.items() if cc in v), None)
                if not pos and cc not in ('PP', 'P2', 'PR', 'XL', 'XG'):
                    pos = POS_ZONE.get(str(a.get('start_zone') or ''))
                if pos:
                    votes[pn][pos] += 1
            slots = defaultdict(Counter)
            for rs, rally in rallies.items():
                lu = None
                for a in rally:
                    lu = a.get('luc_lineup') if side == parse_side else a.get('opp_lineup')
                    if lu:
                        break
                if not lu or len(lu) != 6 or not setters_f:
                    continue
                lu = [str(x) for x in lu]
                idx = [i for i, p_ in enumerate(lu) if p_ in setters_f]
                if not idx:
                    continue                          # backup setter in (double sub): skip
                i0 = idx[0]
                for k in range(1, 6):
                    slots[lu[(i0 + k) % 6]][k] += 1
            file_slots.append(slots)

        def lean(pn):                                 # +1 = hits like a middle, -1 = like an outside
            mb, oh = votes[pn]['MB'], votes[pn]['OH']
            return (mb - oh) / (mb + oh) if mb + oh >= 3 else 0.0

        for slots in file_slots:
            la = sum((c[1] + c[4]) * lean(p_) for p_, c in slots.items())
            lb = sum((c[2] + c[5]) * lean(p_) for p_, c in slots.items())
            mb_pair = PAIR_A if la > lb else PAIR_B if lb > la else LOYOLA_MB_PAIR
            oh_pair = PAIR_B if mb_pair == PAIR_A else PAIR_A
            for p_, c in slots.items():
                struct[p_]['OPP'] += c[3]
                struct[p_]['MB'] += sum(c[k] for k in mb_pair)
                struct[p_]['OH'] += sum(c[k] for k in oh_pair)

        out = {}
        for pn in set(touches) | set(lib):
            t, V, R = touches[pn], sum(votes[pn].values()), sum(struct[pn].values())
            if lib[pn]:
                out[pn] = 'L'
            elif setter_files[pn] or (t['E'] >= 10 and t['E'] > t['A']):
                out[pn] = 'S'
            elif V >= 3:
                score = {k: votes[pn][k] / V + (struct[pn][k] / R if R else 0) for k in ('OH', 'MB', 'OPP')}
                out[pn] = max(score, key=score.get)
            elif t['R'] + t['D'] > 0 and t['R'] + t['D'] >= t['S']:
                out[pn] = 'DS'
            elif t['S']:
                out[pn] = 'Serve'
            elif R >= 10:
                out[pn] = struct[pn].most_common(1)[0][0]
            elif votes[pn]:
                out[pn] = votes[pn].most_common(1)[0][0]
        return out

    def compute_scout_players(sessions_for_opp):
        """Scouting player tab: every attack / serve by the scouted team with the exact court spots
        VolleyMetrics clicked (xy_* from parse_dvw), plus their other touches for the general stats.
          atk: [file_idx, player, rot, combo, eval, pass ('#','+','!','-','/' = grade of their serve-receive
                pass before this first-ball attack, 'T' = transition), start, mid, end, coded_end, set,
                passer (first-ball attacks off a set only, else ''), won (1 = his team won the rally),
                serve group of the pass before a first-ball attack ('spin' / 'float', '' = transition),
                red zone 1/0 (r[14])]   srv rows end with red zone 1/0 (r[9]); oth rows have it at r[8]
                coords = raw DVW int 'yyxx' (-1 = missing). Passer + won feed Pass & Hit (same rules as the
                Reports card: his first-ball attack off a set after his own pass vs after someone else's).
          srv: [file_idx, player, rot, serve_type (Q jump spin / M jump float / H float), eval,
                start, end, coded_end, set]
          oth: [file_idx, player, rot, skill (R pass / D dig / B block / E set), eval]
               R rows add [zone where he passed it (coded, 1-9), eval of the team's first attack after
               his pass ('' = no first-ball attack: overpass, error, freeball), serve type he passed
               (Q jump spin / M jump float / H float)]
        file_idx matches setting.files; duplicate files (same name + date) count once.
        Combo codes are after the X6/X8 (V6/V8) auto-swap; rotation = the same lineup-based rotation as
        the rest of the scouting page."""
        atk, srv, oth, seen = [], [], [], set()
        c_ = lambda v: v if isinstance(v, int) else -1
        for fi, session in enumerate(sessions_for_opp):
            key = (session.get('file'), session.get('date'))
            if key in seen:
                continue
            seen.add(key)
            side = opp_side_for_session(session)
            set_lineups = session.get('away_set_lineups' if side == 'away' else 'home_set_lineups', {})
            setters_f = file_setters(session, side)
            sl = {}
            for k, v in set_lineups.items():
                try: sl[int(k)] = v
                except Exception: sl[k] = v
            groups = defaultdict(list)
            for a in session.get('actions', []):
                groups[a.get('rally_seq', 0)].append(a)
            for rs in sorted(groups):
                fb_open, rcv_row, grade, set_seen, rcv_sg = False, None, 'T', False, ''
                winner = None
                for a in reversed(groups[rs]):
                    tm = _pa_terminal(a)
                    if tm and a.get('team_side') in ('home', 'away'):
                        winner = a['team_side'] if tm > 0 else ('away' if a['team_side'] == 'home' else 'home')
                        break
                won = 1 if winner == side else 0
                for a in groups[rs]:
                    sd, sk = a.get('team_side'), a.get('skill_code')
                    if sd != side:
                        if sk != 'B':
                            fb_open = False
                        continue
                    pn = str(a.get('player_num') or '')
                    if not pn:
                        continue
                    rot = get_opp_rotation(a, side, sl, setters_f)
                    rot = rot if rot and 1 <= rot <= 6 else 0
                    ev = a.get('evaluation') or ''
                    if sk == 'E' and fb_open:
                        set_seen = True
                    if sk == 'R':
                        fb_open, grade, set_seen = True, (ev or '?'), False
                        rcv_sg = serve_group(a)
                        ve = a.get('vm_end') or ''
                        rcv_row = [fi, pn, rot, 'R', ev, ve[:1] if ve[:1].isdigit() else '', '', a.get('technique') or '', in_red_zone(a)]
                        oth.append(rcv_row)
                    elif sk == 'A':
                        if fb_open and rcv_row is not None and not rcv_row[6]:
                            rcv_row[6] = ev or '?'
                        atk.append([fi, pn, rot, a.get('combo_code') or '', ev, grade if fb_open else 'T',
                                    c_(a.get('xy_s')), c_(a.get('xy_m')), c_(a.get('xy_e')),
                                    a.get('vm_end') or '', a.get('set_number') or 0,
                                    rcv_row[1] if (fb_open and set_seen and rcv_row is not None) else '', won,
                                    rcv_sg if fb_open else '', in_red_zone(a)])
                        fb_open = False
                    elif sk == 'S':
                        srv.append([fi, pn, rot, a.get('technique') or '', ev,
                                    c_(a.get('xy_s')), c_(a.get('xy_e')), a.get('vm_end') or '', a.get('set_number') or 0,
                                    in_red_zone(a)])
                    elif sk in ('D', 'B', 'E'):
                        oth.append([fi, pn, rot, sk, ev, '', '', '', in_red_zone(a)])
        return {'atk': atk, 'srv': srv, 'oth': oth}

    def compute_scout_starts(sessions_for_opp):
        """
        Scouting: how the opponent starts each set and what they change.
          sets: [file_idx, set_no, first ('S' served first / 'R' received first / ''),
                 start_rot (1-6, 0 unknown), won (1/0/None), start_lineup [6 jerseys, zone 1..6],
                 pts_for, pts_against]
          subs: [file_idx, set_no, out, in, pts_for, pts_against]   (in-set substitutions, score at the time)
        Files that are exact duplicates (same file name + date) are counted once.
        """
        sets_out, subs_out, seen = [], [], set()
        for fi, session in enumerate(sessions_for_opp):
            key = (session.get('file'), session.get('date'))
            if key in seen:
                continue
            seen.add(key)
            side = opp_side_for_session(session)
            other = 'away' if side == 'home' else 'home'
            parse_side = session.get('luc_side') or 'home'
            setters = file_setters(session, side)
            starts = session.get(f'{side}_set_lineups') or {}
            acts = session.get('actions', [])
            by_set = defaultdict(list)
            for a in acts:
                if isinstance(a.get('set_number'), int):
                    by_set[a['set_number']].append(a)
            for sn in sorted(by_set):
                sa = by_set[sn]
                lu0 = starts.get(str(sn)) or starts.get(sn)
                lineup_of = lambda a: a.get('luc_lineup') if side == parse_side else a.get('opp_lineup')
                if not lu0:
                    lu0 = next((lineup_of(a) for a in sa if lineup_of(a)), None)
                rot = 0
                if lu0 and setters:
                    pos = [i + 1 for i, p in enumerate(lu0) if str(p) in setters]
                    zone = pos[0] if len(pos) == 1 else (next((z for z in pos if z in (1, 6, 5)), pos[0]) if pos else None)
                    rot = SETTER_ZONE_TO_ROT.get(zone, 0) if zone else 0
                first_srv = next((a for a in sa if a.get('skill_code') == 'S'), None)
                first = ('S' if first_srv.get('team_side') == side else 'R') if first_srv else ''
                # rally by rally: score, and substitutions (lineup changes) with the score at that moment
                rallies = defaultdict(list)
                for a in sa:
                    rallies[a.get('rally_seq', 0)].append(a)
                pf = pa_ = 0
                last_winner = None
                prev = list(lu0) if lu0 else None
                for rs in sorted(rallies):
                    rally = rallies[rs]
                    lu = next((lineup_of(a) for a in rally if lineup_of(a)), None)
                    if prev and lu and set(lu) != set(prev):
                        gone = [p for p in prev if p not in lu]
                        came = [p for p in lu if p not in prev]
                        if len(gone) == 1 and len(came) == 1:
                            pairs = [(gone[0], came[0])]
                        else:
                            pairs = [(prev[i], lu[i]) for i in range(min(len(prev), len(lu)))
                                     if prev[i] in gone and lu[i] in came]
                        for o_, i_ in pairs:
                            subs_out.append([fi, sn, str(o_), str(i_), pf, pa_])
                    if lu:
                        prev = list(lu)
                    winner = None
                    for a in reversed(rally):
                        tm = _pa_terminal(a)
                        if tm and a.get('team_side') in ('home', 'away'):
                            winner = a['team_side'] if tm > 0 else ('away' if a['team_side'] == 'home' else 'home')
                            break
                    if winner == side: pf += 1
                    elif winner == other: pa_ += 1
                    if winner: last_winner = winner
                # The team that wins the last rally of a set wins the set (more robust than point totals)
                won = (1 if last_winner == side else 0) if (last_winner and pf + pa_ >= 10) else None
                sets_out.append([fi, sn, first, rot, won, [str(p) for p in (lu0 or [])], pf, pa_])
        return {'sets': sets_out, 'subs': subs_out}

    def set_zone_of(e):
        """Zone the setter set from (1-9, his own side) for a set (E) action. Best source = the exact spot
        VolleyMetrics clicked for the set (xy_s, setter's side: net y=50, 3 m line ~36.8, 6 m ~23.65, his left
        = small x). Fallback = the code: the single-digit form ('~~~3') holds it in code[9] (end_zone), the
        two-digit form ('~~~24C') in code[10] (vm_end) - code[9] is something else there.
        Checked: coords agree with code[9] on 99% of single-digit sets and with code[10] on 91% of two-digit sets."""
        v = e.get('xy_s')
        if isinstance(v, int) and v >= 0:
            x, y = v % 100, v // 100
            if 3 <= x <= 97 and 5 <= y <= 50:
                col = 0 if x < 37.2 else (1 if x < 62.8 else 2)          # his left / middle / right
                row = 0 if y >= 36.8 else (1 if y >= 23.65 else 2)       # front / middle / back
                return [['4', '3', '2'], ['7', '8', '9'], ['5', '6', '1']][row][col]
        ve = e.get('vm_end') or ''
        if ve[:1].isdigit():
            return ve[0]
        z = str(e.get('end_zone') or '')
        return z if z not in ('0', 'None') else ''

    def compute_scout_setting(sessions_for_opp):
        """
        Scouting: one compact row per opponent set that was attacked, so the page can
        slice it any way (setter, rotation, pass quality, where the pass was taken,
        where the set was made, set type) — plus Pass & Hit per opponent player.

        set rows: [file_idx, rot, setter, hitter, phase, pass_zone, passer, set_zone,
                   combo, cat, attack_eval, opp_won_rally, serve_group]
          serve_group = serve they passed before a first-ball set: 'spin' / 'float' ('' = transition)
          r[13] = 1 if the rally was in the red zone (in_red_zone)
          phase     = grade of the serve-receive pass for first-ball sets (# + ! - /),
                      'T' = transition (any later possession)
          pass_zone = zone where the serve was passed (reception end zone) — first ball only
          set_zone  = zone the setter set from (= where the pass / dig went)
        """
        files, names, rows, ph = [], {}, [], {}
        for fi, session in enumerate(sessions_for_opp):
            files.append({'file': session.get('file'), 'season': session.get('season'), 'date': session.get('date')})
            opp_side = opp_side_for_session(session)
            for n_, pinfo in (session.get(f'{opp_side}_players') or {}).items():
                if pinfo.get('name'):
                    names[str(n_)] = pinfo['name']
            set_lineups = session.get('away_set_lineups' if opp_side == 'away' else 'home_set_lineups', {})
            setters_f = file_setters(session, opp_side)
            sl = {}
            for k, v in set_lineups.items():
                try: sl[int(k)] = v
                except Exception: sl[k] = v
            groups = defaultdict(list)
            for a in session.get('actions', []):
                groups[a.get('rally_seq', 0)].append(a)
            for rs in sorted(groups):
                rally = groups[rs]
                winner = None
                for a in reversed(rally):
                    tm = _pa_terminal(a)
                    if tm and a.get('team_side') in ('home', 'away'):
                        winner = a['team_side'] if tm > 0 else ('away' if a['team_side'] == 'home' else 'home')
                        break
                won = 1 if winner == opp_side else 0
                rcv, fb_open = None, False
                for i, a in enumerate(rally):
                    side, sk = a.get('team_side'), a.get('skill_code')
                    if side != opp_side:
                        if sk not in ('B',):          # a block touch doesn't end the possession phase
                            fb_open = False
                        continue
                    if sk == 'R' and rcv is None:
                        rcv, fb_open = a, True
                        continue
                    if sk != 'E':
                        continue
                    nxt = rally[i + 1] if i + 1 < len(rally) else None
                    first_ball = fb_open and rcv is not None
                    fb_open = False
                    if not nxt or nxt.get('team_side') != opp_side or nxt.get('skill_code') != 'A':
                        continue
                    ev = nxt.get('evaluation', '')
                    if ev not in ('#', '+', '!', '-', '/', '='):
                        continue
                    rot = get_opp_rotation(a, opp_side, sl, setters_f)
                    combo = nxt.get('combo_code') or ''
                    cat = 'Dump' if combo in ('PP', 'P2', 'PR') else atk_zone_cat(nxt, rot)
                    szone = set_zone_of(a)
                    rows.append([fi, rot if rot and 1 <= rot <= 6 else 0, a.get('player_num') or '', nxt.get('player_num') or '',
                                 (rcv.get('evaluation') or '?') if first_ball else 'T',
                                 str(rcv.get('end_zone') or '') if first_ball else '',
                                 (rcv.get('player_num') or '') if first_ball else '',
                                 szone, combo, cat, ev, won, serve_group(rcv) if first_ball else '', in_red_zone(a)])
            # Pass & Hit for the opponent's players (same rules as Loyola's card)
            for sn, pl in compute_pass_hit(session.get('actions', []), opp_side).items():
                for pn, v in pl.items():
                    dst = ph.setdefault(str(fi), {}).setdefault(pn, {'ph': {}, 'np': {}, 'pg': {}})
                    for g in ('ph', 'np'):
                        for e_, rec in v[g].items():
                            c = dst[g].setdefault(e_, [0, 0]); c[0] += rec[0]; c[1] += rec[1]
                    for e_, n_ in v['pg'].items():
                        dst['pg'][e_] = dst['pg'].get(e_, 0) + n_
        return {'files': files, 'names': names, 'sets': rows, 'ph': ph}

    scout_index    = []
    opponent_stats = defaultdict(lambda: {'files':0,'actions':0,'skills':{}})
    # Group scout sessions by opponent for rotation stats
    opp_sessions   = defaultdict(list)

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
        opp_sessions[opp].append(s)

    resolve_opp_sides(opp_sessions)
    scout_fix_total = Counter()
    for s_ in scouts_raw:
        side_ = opp_side_for_session(s_)
        for _side, f_, t_, n_ in swap_right_side(s_, [side_], (lambda sd, a: a.get('rot_h')) if side_ == 'home' else None):
            scout_fix_total[(f_, t_)] += n_
    if scout_fix_total:
        print("  Scout combo fixes: " + ", ".join(f"{f}->{t} x{n}" for (f, t), n in scout_fix_total.items()), file=sys.stderr)
    for s in scouts_raw:
        opp = s.get('opponent') or 'Unknown'
        side = opp_side_for_session(s)
        os_ = opponent_stats[opp]
        os_['files']   += 1
        os_['actions'] += s.get('action_count', 0)
        for action in s.get('actions', []):
            if action.get('team_side') == side:          # the scouted team only (not the team they played)
                add_action(os_['skills'], action)

    for os_ in opponent_stats.values():
        derive(os_['skills'])

    # Compute rotation-level stats for each opponent
    print("Computing scout rotation stats ...", file=sys.stderr)
    opp_rotation_stats = {}
    for opp, sess_list in opp_sessions.items():
        if opp == 'Unknown':
            continue
        opp_rotation_stats[opp] = compute_scout_rotation_stats(sess_list)
        opp_rotation_stats[opp]['setting'] = compute_scout_setting(sess_list)
        opp_rotation_stats[opp]['starts'] = compute_scout_starts(sess_list)
        opp_rotation_stats[opp]['starts']['pos'] = compute_scout_positions(sess_list)
        opp_rotation_stats[opp]['players'] = compute_scout_players(sess_list)
        _rz = compute_scout_rotation_stats(sess_list, rz_only=True)       # same numbers, red-zone rallies only
        opp_rotation_stats[opp]['rz'] = {'team': _rz['team'], 'rotations': _rz['rotations']}

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
        'opp_player_session': opp_player_session,   # opponent players, match files only
        'player_season':  pse_out,
        'scout_index':    scout_index,
        'opponent_stats': dict(opponent_stats),
        'opp_rotation_stats': opp_rotation_stats,
        'call_attacks': {
            season: {
                k: {cc: dict(cv) for cc, cv in combos.items()}
                for k, combos in kcodes.items()
            }
            for season, kcodes in call_attacks_by_season.items()
        },
    }


    # ── Per-set match data (Reports tab "Set" filter) ─────────────────────────
    # Same structures the match reports read, recomputed from each set's actions
    # alone. Written to a separate dashboard_sets.json that the page only loads
    # when a set is picked, so the main dashboard stays the same size.
    print("Computing per-set match data ...", file=sys.stderr)

    def luc_player_store(s, acts, ls, keep_unnamed):
        store = defaultdict(lambda: {'actions': 0, 'skills': {}, 'rcv_zones': {}})
        for a in acts:
            pnum = a.get('player_num', '')
            if not pnum:
                continue
            pname = a.get('player_name', '')
            if (not pname or pname.startswith('#')) and pnum not in keep_unnamed:
                continue
            if ls != 'both' and a.get('team_side') != ls:
                continue
            if pnum not in loyola_player_nums:
                continue
            ps_ = store[pnum]
            ps_['actions'] += 1
            add_action(ps_['skills'], a, ps_['rcv_zones'])
            add_rcv_extras((ps_,), a, True)
        dig, _ = compute_rally_sequences(acts, ls)
        for pnum, dstats in dig.items():
            pname = s.get('players', {}).get(pnum, {}).get('name', '')
            if (not pname or pname.startswith('#')) and pnum not in keep_unnamed:
                continue
            if ls != 'both' and pnum not in loyola_player_nums:
                continue
            ps_ = store[pnum]
            ps_['dig_kill_rallies']  = ps_.get('dig_kill_rallies', 0)  + dstats['dig_kill_rallies']
            ps_['dig_total_rallies'] = ps_.get('dig_total_rallies', 0) + dstats['dig_rallies']
        for ps_ in store.values():
            derive(ps_['skills'])
            derive_zones(ps_['rcv_zones'], is_match=True)
        return dict(store)

    idx_by_sid = {e['id']: e for e in sessions_index}
    sets_out = {}
    for s in sessions_raw:
        if s.get('type') != 'match':
            continue
        sid  = f"{s['season']}|{s['file']}"
        ls   = loyola_side_for_session(s)
        acts = s.get('actions', [])
        set_nums = sorted({a.get('set_number') for a in acts if isinstance(a.get('set_number'), int)})
        if not set_nums:
            continue
        idx_by_sid[sid]['sets'] = set_nums
        two_sided = any(a.get('team_side') != ls for a in acts)
        full_roster = opp_match.get(sid, {}).get('roster')
        per = {}
        for n in set_nums:
            acts_n = [a for a in acts if a.get('set_number') == n]
            _, fbso_n = compute_rally_sequences(acts_n, ls)
            e = {
                'fbso_rcv':        fbso_n['rcv_rallies'],
                'fbso_kills':      fbso_n['fbso_kills'],
                'fbso_by_player':  fbso_n['by_player'],
                'match_rot_stats': compute_match_rotation_stats(acts_n, ls),
                'opp_stats':       compute_opp_stats({'actions': acts_n}, ls) if two_sided else None,
                'player_session':  luc_player_store(s, acts_n, ls, s.get('_keep_unnamed', set())),
                'setter_tab':      compute_setter_tab(acts_n, ls),   # Reports: pass-adjusted setter efficiency by set
            }
            if full_roster:
                res = opp_bundle(s, acts_n, roster=full_roster)
                if res:
                    om, ostore = res
                    e.update({
                        'match_rot_stats_opp': om['rot_stats'],
                        'opp_fbso_rcv':        om['fbso']['rcv_rallies'],
                        'opp_fbso_kills':      om['fbso']['fbso_kills'],
                        'opp_fbso_by_player':  om['fbso']['by_player'],
                        'luc_stats':           om['luc_stats'],
                        'opp_player_session':  ostore,
                    })
            per[str(n)] = e
        sets_out[sid] = per

    output['pa_weights'] = PA_WEIGHTS
    output['competition'] = load_competition(
        os.path.join(os.path.dirname(os.path.abspath(out_path)), COMPETITION_CSV),
        output['sessions'], output['players_by_season'])
    output['competition_6v6'] = load_competition_6v6(os.path.dirname(os.path.abspath(out_path)), output)
    # match competition: {season: {pnum: {session_id: {set: [won_share, lost_share]}}}}
    cm = defaultdict(lambda: defaultdict(dict))
    sess_by_id = {f"{x['season']}|{x['file']}": x for x in sessions_raw}
    for x in output['sessions']:
        if x.get('type') != 'match':
            continue
        raw_s = sess_by_id.get(x['id'])
        if not raw_s:
            continue
        roster_se = output['players_by_season'].get(x['season']) or {}
        for pn, sets in compute_match_competition(raw_s, loyola_side_for_session(raw_s)).items():
            if pn in roster_se and not roster_se[pn].get('virtual'):
                cm[x['season']][pn][x['id']] = sets
    # Coach-only virtual players (Jake Read 2.0 = #7 as a libero): the sets #7 played AS THE LIBERO, and his
    # practice 6v6 competition (same player, same practices; earned with the outsides on the 6v6 sheet)
    for v in VIRTUAL_PLAYERS:
        se, src, num = v['season'], v['src'], v['num']
        for sid, sets in (cm.get(se, {}).get(src) or {}).items():
            lib = {sn: r for sn, r in sets.items() if len(r) > 2 and r[2]}
            if lib:
                cm[se][num][sid] = lib
        six = (output['competition_6v6'].get(se) or {}).get('players') or {}
        if src in six:
            six[num] = dict(six[src], copy_of=src)
    output['competition_match'] = {se: dict(v) for se, v in cm.items()}
    add_virtual_players(output, sets_out)

    # Player portal only (kept out of dashboard.html): practice setter -> hitter -> pass-quality buckets
    extra_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), 'portal_extra.json')
    sid_by_key = {f"{x['season']}|{x['file']}": x['id'] for x in output['sessions'] if x.get('season') and x.get('file')}
    with open(extra_path, 'w') as f:
        json.dump({'setter_atk_q': {sid_by_key.get(k, k): v['setter_atk_q'] for k, v in session_rally.items() if v.get('setter_atk_q')}},
                  f, separators=(',', ':'))

    sets_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), 'dashboard_sets.json')
    with open(sets_path, 'w') as f:
        json.dump(sets_out, f, separators=(',', ':'))
    print(f"  wrote {sets_path} ({os.path.getsize(sets_path)/1024/1024:.1f} MB, {len(sets_out)} matches)", file=sys.stderr)

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
