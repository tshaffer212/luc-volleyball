# LUC Volleyball Dashboard — Project Rules

## Attack Efficiency Formula

**Always use (Kills − Errors − Blocks) / Attempts** for attack efficiency. Never use (K−E)/A.

In DataVolley/Volleymetrics coding, an attack evaluated with `/` means the opponent scored a block kill on that attack (confirmed by the opponent's `B#` action in the same rally). These blocked attacks must be subtracted from the numerator alongside errors.

```
Efficiency = (K − E − B) / A
```

Where:
- `K` = kills (`#` evaluation)
- `E` = errors (`=` evaluation — into net, out of bounds)
- `B` = blocked (`/` evaluation — opponent scored a block kill)
- `A` = total attempts

In `aggregate.py` this is implemented in `derive()` using `ev.get('/', 0)` for skill `'A'`.
In JavaScript, use `ev['/']||0` (from evals) or `cv.blocked||0` / `c.bk||0` (from pre-aggregated fields).

### Where this applies
Every surface that shows attack efficiency: KPI tickers, combo tables, rotation tables, setter breakdown, shot charts, zone courts, call-attack tables, player heatmaps, and any inline derived formula.

---

## Always Show Blocks (BLK) Alongside K / E / Att

Whenever a table or row displays Kills (K), Errors (E), and Attempts (Att) together, it must also show Blocked (BLK) as a separate column between E and Att. This applies to:

- Match overview Attacking table: `K | E | BLK | ATT | EFF% | K%`
- Rotation attack tables: `K | E | BLK | A | Eff% | K%`
- Setter breakdown per-combo columns: `K | E | BLK | ATT | EFF%`
- Setter breakdown TOTAL column: `K | E | BLK | ATT | EFF%`
- Any group/team subtotal row that has a K/E/Att/Eff% pattern

---

## Data Pipeline

```
parse_dvw.py → volleyball_data.json → aggregate.py → dashboard_data.json
                                                    ↓
                             dashboard_template.html (with __DASHBOARD_DATA__ placeholder)
                                                    ↓
                                          dashboard.html (final file)
```

`aggregate.py` also writes `dashboard_sets.json` (per-set match data for the Reports tab's Set filter).
The page fetches it on demand, so it must be committed and pushed alongside `dashboard.html`.

After any change to `aggregate.py` or `parse_dvw.py`, rebuild with:
```bash
python3 aggregate.py volleyball_data.json dashboard_data.json
python3 -c "
with open('dashboard_template.html') as f: t = f.read()
with open('dashboard_data.json') as f: data = f.read()
open('dashboard.html','w').write(t.replace('__DASHBOARD_DATA__', data))
"
```

### Design: editorial "sports page" (live since Nov 18, 2026)
`dashboard_template.html` / `dashboard.html` and `players.html` use the editorial design: light paper
background, ink text, maroon accent, Source Serif 4 + IBM Plex Sans (Google Fonts, system fallbacks),
efficiency shown as .340 via `fmtEff3()` / `fmtEffPct3()`, ASCII-only text (no special symbols: use "-" and
"," not em dashes or middle dots). `dashboard-v2.html` / `players-v2.html` are now just redirects to the live
pages; `dashboard_template_v2.html` is retired (a stub).
- Rollback: the old dark (v1) files are in `archive/v1/` (git-ignored, local only) and in git history.
  To go back: copy `archive/v1/dashboard_template.html` -> `dashboard_template.html` and
  `archive/v1/players.html` -> `players.html`, rebuild, push.
- Verification harness: `node "<outputs>/v2_harness/render_all.js" dashboard_template.html dashboard_data.json <outdir>`
  (renders every tab / report / PDF and flags JS errors, dark leftovers, efficiency shown as %, junk text).

### Points Added (weighted by position)
Basic values only (each touch vs the average touch of that skill; no situation-adjusted mode). Setting =
attack result off his sets vs the average attack off a set (`T.setVal`) + set errors. Each player's score =
Σ position weight × category points, weights in `PA_WEIGHTS` (aggregate.py → `DATA.pa_weights`, rows = 100%).
Competition = practice drill results from `private/competition.csv` (Date, Number, Player, Wins, Losses;
git-ignored) → (W − L) / 2, counted on dates of the sessions in view. Reports card: "Overall team" and
"By position" views. Same scoring on the coach Player tab and the player portal.

### Scouting (opponent files)
`resolve_opp_sides()` in aggregate.py decides which side of each scout file is the scouted team: roster
overlap with that opponent's other files first, then name matching (handles abbreviations like UCSD, NKU,
LW, MCK, Pepp). `opponent_stats` skills count the scouted team only. `opp_rotation_stats[opp].setting` =
one row per attacked set `[file_idx, rot, setter, hitter, phase, pass_zone, passer, set_zone, combo, cat,
eval, won]` (phase = pass grade or 'T'; set_zone = E end zone = where the setter set from) + `ph`
(Pass & Hit per file). The page (`buildScoutSetting`) slices it by setter / rotation / pass quality.

### Coach-only virtual players (Jake Read 2.0)
`VIRTUAL_PLAYERS` in `aggregate.py` adds `7L` = "Jake Read 2.0", a libero-only copy of #7 (Fall 2027 only):
only R/RM/RQ/D/F/E skills (and R/D/E/X Points Added touches) are copied, he's in the LIBEROS group, and
`DATA.virtual_players` tells the dashboard to show his rows but leave him out of EVERY team and
position-group total (JS helper `isVirt(pnum)`). Any new code that sums players into a team/group total
must skip `isVirt(...)` players. The player portal never creates a login for virtual players.
The real #7 Jake Read stays an outside hitter with all his data.

### Player portal (players.html)
`build_player_portal.py` (run automatically by `rebuild.py`, or by hand after the rebuild above) writes one
locked file per current-roster player to `player_data/<id>.json` plus `player_data/roster.json`.
`players.html` contains NO stats; a player picks their name, enters their password, and only their own file is
unlocked in the browser. Hitters see only their own numbers (attack-by-setter shows setter names only);
setters additionally get their full distribution by hitter, and their Rotation tab shows the team's attack by
rotation on balls they set (`match_rot_stats.setter_rot` from `aggregate.py`) instead of their own attacks.
- Passwords: `private/player_passwords.json` + `private/Player Passwords.csv`. `private/` is git-ignored —
  **never commit or push it, never paste passwords into code or the repo.**
- New roster players get a password automatically; existing ones never change.
  Reset one: `python3 build_player_portal.py --reset "Player Name"`.
- Push `players.html` and `player_data/` alongside `dashboard.html`.

To push to GitHub (must be done from terminal, not sandbox — git lock issues):
```bash
cd "/Users/thomasshaffer/Desktop/Volleystation Files/DVW Project/LUC Volleyball"
rm -f .git/HEAD.lock .git/index.lock
git add -A && git commit -m "..." && git push
```

---

## DVW / Volleymetrics Attack Code Format

VM format: `code[8]='~'`, then:
- `code[9]` = start zone (digit)
- `code[10]` = start sub-zone (digit 1–9, 3×3 grid within zone)
- `code[11]` = direction letter (A/B/C/D)
- `code[12]` = height letter
- `code[13]` = end zone (digit)

Sub-zone letters (A–D) apply to serve landing zones (2×2 quadrant grid).

Evaluation codes for attacks: `#`=kill, `=`=error (out/net), `/`=blocked, `+`=positive/in-play, `-`=negative/in-play.

---

## Zones

- Zones 1–6 = standard court zones (front row 4/3/2, back row 5/6/1)
- Zones 7/8/9 = exist on LUC's side only (back-row attack starts, serve origins) — **do NOT appear in ZONE_DST** (attacks never land in extended zones)
