#!/usr/bin/env python3
"""
build_player_portal.py: per-player locked stat files for players.html
======================================================================
Reads dashboard_data.json + dashboard_sets.json and writes one encrypted
file per current-roster player to player_data/<id>.json, plus
player_data/roster.json (names only, no stats).

Each player's file can only be opened with that player's password.
Passwords live in private/player_passwords.json (git-ignored, never pushed).
New roster players get a new random password automatically; existing
passwords never change unless you delete that player's entry
(or run with --reset "Player Name").

Crypto (stdlib only, matched in players.html with WebCrypto):
  key  = PBKDF2-HMAC-SHA256(password, salt, ITER) -> 64 bytes (enc 32 | mac 32)
  ks   = HMAC-SHA256(enc, nonce || counter_be32) blocks   (keystream)
  ct   = zlib(payload_json) XOR ks
  tag  = HMAC-SHA256(mac, nonce || ct)                     (wrong password -> tag mismatch)

Usage:
  python3 build_player_portal.py                # build
  python3 build_player_portal.py --season "Fall 2027"
  python3 build_player_portal.py --reset "Luka Lojic"
"""
import argparse, base64, collections, csv, hashlib, hmac, json, os, secrets, shutil, struct, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASH = HERE / 'dashboard_data.json'
SETS = HERE / 'dashboard_sets.json'
OUT = HERE / 'player_data'
PRIV = HERE / 'private'
PWF = PRIV / 'player_passwords.json'
PWCSV = PRIV / 'Player Passwords.csv'
ITER = 300_000
SKIP_SEASONS = {'Unknown Season'}
ALPH = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'   # no 0/O/1/l/I


def new_password():
    g = lambda: ''.join(secrets.choice(ALPH) for _ in range(4))
    return f'{g()}-{g()}-{g()}'


def pid(name):
    return hashlib.sha256(('luc-portal|' + name).encode()).hexdigest()[:16]


def encrypt(obj, password):
    raw = zlib.compress(json.dumps(obj, separators=(',', ':')).encode(), 9)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    k = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITER, 64)
    ek, mk = k[:32], k[32:]
    ks = bytearray()
    for i in range((len(raw) + 31) // 32):
        ks += hmac.new(ek, nonce + struct.pack('>I', i), hashlib.sha256).digest()
    ct = bytes(a ^ b for a, b in zip(raw, ks))
    tag = hmac.new(mk, nonce + ct, hashlib.sha256).digest()
    b = lambda x: base64.b64encode(x).decode()
    return {'v': 1, 'iter': ITER, 'salt': b(salt), 'nonce': b(nonce), 'ct': b(ct), 'tag': b(tag)}


# ── helpers to sum nested count dicts ─────────────────────────────────────────
def add_into(dst, src):
    for k, v in (src or {}).items():
        if isinstance(v, dict):
            add_into(dst.setdefault(k, {}), v)
        elif isinstance(v, list):
            cur = dst.setdefault(k, [0] * len(v))
            for i, x in enumerate(v):
                cur[i] += x or 0
        elif isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + v
    return dst


def slim_skills(ps):
    if not ps:
        return None
    out = {}
    for sk, v in (ps.get('skills') or {}).items():
        r = {'n': v.get('attempts', 0), 'ev': v.get('evals') or {}}
        if v.get('rcv_cat'):
            r['cat'] = v['rcv_cat']
        if sk == 'A' and v.get('combos'):
            r['c'] = {c: {'n': cv['attempts'], 'ev': cv['evals']} for c, cv in v['combos'].items()}
        if sk == 'E' and v.get('combos'):
            r['c'] = {c: {'n': cv['attempts'], 'ev': cv['evals']} for c, cv in v['combos'].items()}
        out[sk] = r
    rec = {'sk': out}
    if ps.get('rcv_zones'):
        rec['rz'] = {z: {'n': v['attempts'], 'ev': v['evals']} for z, v in ps['rcv_zones'].items()}
    if ps.get('dig_total_rallies'):
        rec['dk'] = [ps.get('dig_kill_rallies', 0), ps.get('dig_total_rallies', 0)]
    return rec


def sa_from_practice(sa):
    """setter_attacks[setter][hitter][combo] {attempts,kills,errors,blocks} -> {combo:{k,e,bk,a}} + OVR"""
    out = {}
    for c, v in sa.items():
        out[c] = {'k': v['kills'], 'e': v['errors'], 'bk': v['blocks'], 'a': v['attempts']}
    ovr = {'k': 0, 'e': 0, 'bk': 0, 'a': 0}
    for v in out.values():
        for k in ovr:
            ovr[k] += v[k]
    out['OVR'] = ovr
    return out


def trim_atk(d):
    return {c: {'k': v.get('k', 0), 'e': v.get('e', 0), 'bk': v.get('bk', 0), 'a': v.get('a', 0)} for c, v in (d or {}).items()}


def sum_atk(sit):
    r = [0, 0, 0, 0]   # k e b a
    for v in ((sit or {}).get('atk') or {}).values():
        r[0] += v['k']; r[1] += v['e']; r[2] += v['b']; r[3] += v['a']
    return r


def build_slice(P, is_setter, setters, *, ps, mrs, fbso_bp, setter_atk, prac_sa, setter_tab, pa, ph, pa_all):
    """One slice (whole session or one set) of this player's data + team aggregates."""
    rec = slim_skills(ps) or {}
    bp = ((mrs or {}).get('by_player') or {}).get(P) or {}
    rot = {}
    if bp.get('rotation_atk'):
        rot['a'] = {r: trim_atk(v) for r, v in bp['rotation_atk'].items()}
    for k, kk in (('rotation_srv', 's'), ('rotation_rcv', 'r'), ('rotation_so', 'so')):
        if bp.get(k):
            rot[kk] = bp[k]
    if rot:
        rec['rot'] = rot
    # attacks by setter (this hitter only)
    bs = {}
    if setter_atk is not None:
        for st, hitters in setter_atk.items():
            if P in hitters and (hitters[P].get('all')):
                bs[st] = trim_atk(hitters[P]['all'])
    elif prac_sa:
        for st, hitters in prac_sa.items():
            if P in hitters:
                bs[st] = sa_from_practice(hitters[P])
    if bs:
        # only real setters keep their own row; second-ball sets by anyone else -> OTHER
        merged = {}
        for st, combos in bs.items():
            key = st if (st in setters or st == 'MISC') else 'OTHER'
            dst = merged.setdefault(key, {})
            for c, v in combos.items():
                x = dst.setdefault(c, {'k': 0, 'e': 0, 'bk': 0, 'a': 0})
                for kk in x:
                    x[kk] += v.get(kk, 0)
        rec['bs'] = merged
    if fbso_bp and P in fbso_bp:
        rec['fb'] = fbso_bp[P]
    if pa:
        rec['pa'] = pa
    if ph:
        rec['ph'] = ph
    # team hitter cells (aggregate of ALL hitters, no names): sit -> [k,e,b,a]
    hc = {}
    for keys in (pa_all or {}).values():
        for k, v in keys.items():
            if k[0] != 'A':
                continue
            _, ev, sit = k.split('|')
            c = hc.setdefault(sit, [0, 0, 0, 0])
            c[3] += v[0]
            if ev == '#': c[0] += v[0]
            elif ev == '=': c[1] += v[0]
            elif ev == '/': c[2] += v[0]
    if hc:
        rec['hc'] = hc
    if is_setter:
        # full distribution for this setter: hitter -> phase -> combo
        if setter_atk is not None and P in setter_atk:
            rec['sd'] = {h: {ph_: trim_atk(c) for ph_, c in phases.items()} for h, phases in setter_atk[P].items()}
        elif prac_sa and P in prac_sa:
            rec['sd'] = {h: {'all': sa_from_practice(c)} for h, c in prac_sa[P].items()}
        # setter-adjusted: own buckets + team buckets (sum over all setters)
        if setter_tab:
            BK = ['perf', 'good', 'med', 'oos', 'fbso', 'transition']
            team = {b: [0, 0, 0, 0] for b in BK}
            for sp, sits in setter_tab.items():
                for b in BK:
                    r = sum_atk(sits.get(b))
                    for i in range(4):
                        team[b][i] += r[i]
            rec['sbt'] = team
            if P in setter_tab:
                rec['sbo'] = {b: sum_atk(setter_tab[P].get(b)) for b in BK}
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default=None, help='roster season (default: latest)')
    ap.add_argument('--reset', action='append', default=[], help='give this player a new password')
    args = ap.parse_args()

    d = json.load(open(DASH))
    sets_data = json.load(open(SETS)) if SETS.exists() else {}
    pbs = d['players_by_season']
    order = [s for s in ['Spring 2025', 'Fall 2025', 'Spring 2026', 'Fall 2026', 'Spring 2027', 'Fall 2027',
                         'Spring 2028', 'Fall 2028'] if s in pbs]
    roster_season = args.season or order[-1]
    roster = pbs[roster_season]

    # name -> {season: num}
    by_name = collections.defaultdict(dict)
    for se, pl in pbs.items():
        if se in SKIP_SEASONS:
            continue
        for n, p in pl.items():
            by_name[p['name']][se] = n

    # passwords
    PRIV.mkdir(exist_ok=True)
    pw = json.load(open(PWF)) if PWF.exists() else {}
    used = set(pw.values())
    for name in args.reset:
        pw.pop(name, None)
    for p in roster.values():
        if p['name'] not in pw:
            while True:
                np_ = new_password()
                if np_ not in used:
                    break
            pw[p['name']] = np_; used.add(np_)
    json.dump(pw, open(PWF, 'w'), indent=2)

    # value-table pools for Points Added (team aggregate, no names): season -> type -> key -> [n, won]
    pool = {}
    for s in d['sessions']:
        if s['season'] in SKIP_SEASONS or not s.get('pts_added'):
            continue
        dst = pool.setdefault(s['season'], {}).setdefault(s['type'], {})
        for pl in s['pts_added'].values():
            for keys in pl.values():
                for k, v in keys.items():
                    if k[0] == 'X':
                        continue
                    c = dst.setdefault(k, [0, 0]); c[0] += v[0]; c[1] += v[1]

    sessions = sorted([s for s in d['sessions'] if s['season'] not in SKIP_SEASONS and s['type'] in ('match', 'practice')],
                      key=lambda s: (s['date'], s['id']))

    # combo / call code names (team-level labels, no stats)
    cn = {}
    for ps in d['player_session'].values():
        for sk in ('A', 'E'):
            for c, cv in ((ps.get('skills') or {}).get(sk, {}).get('combos') or {}).items():
                if cv.get('name') and cv['name'] != c:
                    cn.setdefault(c, cv['name'])
    import datetime
    gen = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    if OUT.exists():
        for f in OUT.glob('*.json'):
            try: f.unlink()
            except OSError: f.write_text('{}')
    OUT.mkdir(exist_ok=True)
    roster_out = []
    for num, p in sorted(roster.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        name = p['name']
        nums = by_name[name]
        is_setter = p.get('position_code') == '5'
        payload = {'v': 1, 'player': {'name': name, 'pos': p.get('position_code'), 'lib': bool(p.get('is_libero')),
                                      'setter': is_setter, 'nums': nums},
                   'names': {}, 'pool': {}, 'sessions': [], 'cn': cn, 'gen': gen}
        for s in sessions:
            se = s['season']
            if se not in nums:
                continue
            P = nums[se]
            set_list = {n for n, q in pbs[se].items() if q.get('position_code') == '5'}
            ps_key = f"{P}|{s['id']}"
            ps = d['player_session'].get(ps_key)
            mrs = s.get('match_rot_stats') or {}
            is_match = s['type'] == 'match'
            setter_atk = mrs.get('setter_atk') if is_match and mrs.get('setter_atk') is not None else None
            prac_sa = s.get('setter_attacks') if setter_atk is None else None
            appears = bool(ps) or any(P in (pl or {}) for pl in (s.get('pts_added') or {}).values())
            if not appears and not (is_setter and ((setter_atk or {}).get(P) or (prac_sa or {}).get(P))):
                continue

            def pa_for(sn):
                return ((s.get('pts_added') or {}).get(sn) or {}).get(P)

            def ph_for(sn):
                return ((s.get('pass_hit') or {}).get(sn) or {}).get(P)

            pa_sum, ph_sum = {}, {}
            for sn in (s.get('pts_added') or {}):
                add_into(pa_sum, pa_for(sn))
            for sn in (s.get('pass_hit') or {}):
                add_into(ph_sum, ph_for(sn))
            pa_all_sets = {}
            for sn, pl in (s.get('pts_added') or {}).items():
                for pn, keys in pl.items():
                    add_into(pa_all_sets.setdefault('_', {}), keys)
            a = build_slice(P, is_setter, set_list, ps=ps, mrs=mrs, fbso_bp=s.get('fbso_by_player'), setter_atk=setter_atk,
                            prac_sa=prac_sa, setter_tab=s.get('setter_tab'), pa=pa_sum, ph=ph_sum, pa_all=pa_all_sets)
            ent = {'id': s['id'], 'd': s['date'], 'se': se, 't': s['type'], 'o': s.get('opponent') or s['file'], 'a': a}
            if is_match:
                per = {}
                sd = sets_data.get(s['id']) or {}
                for sn, e in sd.items():
                    sp = (e.get('player_session') or {}).get(P)
                    sp = dict(sp, player_num=P) if sp else None
                    emrs = e.get('match_rot_stats') or {}
                    pa_set = (s.get('pts_added') or {}).get(sn) or {}
                    per[sn] = build_slice(P, is_setter, set_list, ps=sp, mrs=emrs, fbso_bp=e.get('fbso_by_player'),
                                          setter_atk=emrs.get('setter_atk') or {}, prac_sa=None,
                                          setter_tab=e.get('setter_tab'), pa=pa_for(sn), ph=ph_for(sn),
                                          pa_all=pa_set)
                if per:
                    ent['s'] = per
            payload['sessions'].append(ent)
            # names this player is allowed to see: setters who set him; for setters, hitters they set
            nm = payload['names'].setdefault(se, {})
            src = setter_atk if setter_atk is not None else (prac_sa or {})
            for st, hitters in src.items():
                if P in hitters and st in set_list:
                    nm[st] = pbs[se][st]['name']
            if is_setter and P in src:
                for h in src[P]:
                    if h in pbs[se]:
                        nm[h] = pbs[se][h]['name']
            nm[P] = name
            # setter positions so the page can tell setters from 2nd-ball sets
            payload.setdefault('setters', {}).setdefault(se, sorted({n for n, q in pbs[se].items() if q.get('position_code') == '5'}))
        for se in {x['se'] for x in payload['sessions']}:
            if se in pool:
                payload['pool'][se] = pool[se]
        payload['seasons'] = [s for s in order if any(x['se'] == s for x in payload['sessions'])]
        fid = pid(name)
        (OUT / f'{fid}.json').write_text(json.dumps(encrypt(payload, pw[name]), separators=(',', ':')))
        roster_out.append({'id': fid, 'num': num, 'name': name})

    (OUT / 'roster.json').write_text(json.dumps({'season': roster_season, 'players': roster_out}, indent=1))
    with open(PWCSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Number', 'Player', 'Password'])
        for r in roster_out:
            w.writerow([r['num'], r['name'], pw[r['name']]])
    print(f'  ✅  Player portal: {len(roster_out)} locked player files → player_data/  (passwords: private/)')


if __name__ == '__main__':
    main()
