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
- Number colors: numbers are plain ink. Only notably high/low values get color, via `var(--hi)` (green) /
  `var(--lo)` (red): efficiency through `effTone(v, n, base)` = at least .100 above/below the comparison
  average (default `EFF_BASE`, the dataset's Loyola efficiency) with 10+ attempts. No yellow/orange/gold on
  numbers; CSS neutralises old inline `var(--green)/var(--red)/var(--gold)` text colors. players.html uses
  classes `hi` / `lo` the same way.
- Scouting back row is split into `Dball` (X8/V8, zone 1/9) and `BIC` (XP family, zone 5-8) in
  `atk_zone_cat()` (aggregate.py) and the page's `SC_CATS`.
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
Opponent rotations use the SAME numbering as Loyola (R1 = setter in zone 1, R2 = zone 6, R3 = zone 5,
R4 = zone 4, R5 = zone 3, R6 = zone 2): `get_opp_rotation()` finds the setter's zone from that side's lineup
on each action (`file_setters()` = the file's main setter(s); with two setters on court the back-row one sets),
then maps it through `SETTER_ZONE_TO_ROT`. Sanity check: D-balls (X8/V8) land in R4-R6, X6 in R1-R3.
- Rotation cards read 1 2 3 on top, 4 5 6 on the bottom (`ROT_ORDER = [1,2,3,4,5,6]`, Rotation 6 bottom-right).
- `opp_rotation_stats[opp].starts` (`compute_scout_starts()`): `sets` = `[file_idx, set, 'S'/'R' (served or
  received first), start_rot, won, starting six, pts_for, pts_against]`, `subs` = `[file_idx, set, out, in,
  pts_for, pts_against]`. Shown by `buildScoutStarts()` as "Set Starts and Substitutions" (starting rotation
  by serve/receive, next set after losing vs winning, in-set subs). file_idx matches `setting.files`.
  A "true rotation switch" between sets = the first server changed (receiving first in Rk = serving first in
  R(k+1), 6 wraps to 1) AND the starting rotation changed; serve R1 -> receive R6 is not a switch.
  `starts.pos` = `{jersey: OH/MB/OPP/S/L/DS/Serve}` from `compute_scout_positions()`: scout files carry no
  positions, so it's a best guess: libero flag, main setter, then attack codes/start zones (`POS_CODES`)
  combined 50/50 with lineup slot relative to the setter on every rally (opposite = 3 spots away; the
  middle pair vs outside pair is picked by that team's hitting, else Loyola's order S, OH, MB, OPP, OH, MB =
  middle one rotation ahead of the setter, outside one behind). DS = passes/digs but rarely attacks,
  Serve = mostly comes in to serve. Shown as tags in the subs table.
  Subs table: a sub that just reverses an earlier one in the same set (same player straight back in) is
  folded into the original row ("Comes back" column: back in X of N, after about N points); a different
  player coming back in, or a sub that stays in, still gets its own row.
- Scouting set types use the same list as the Loyola reports (`VALID_COMBOS`: X5 V5 X6 V6 XP X8 X1 X2 X7);
  every other code (PP, V8, XM, XR, ...) counts as "Other" via `scCombo()`. The "where the ball was set"
  bars (Left/Quick/Right/D-ball/BIC/Dump) are location-based and still include those codes.
  `scColorKey()` = the plain-English color key (colors -> play calls via `SC_CAT_CODES`, descriptions in
  `SC_CAT_DESC`), shown above the rotation boxes and in the setting breakdown; set-type rows carry the swatch.
- Serve type everywhere serving / passing shows up in the Team report: vs jump spin / vs float / total
  (`serve_group()` in aggregate.py: technique Q = spin, M / H = float). Team strip SO% / FBSO% / In-Sys
  (`team.by.spin|float`), rotation cards' Receiving table (`rotations[r].rcv.by`), setting breakdown pass tables
  (setting rows add r[12] = serve group, '' = transition), the pass-zone matrix (three side by side) and
  opponent Pass & Hit (`buildScoutPassHit()`, now built from `players.atk` rows; atk rows add r[13] = serve group).

### Scouting player tab ("Player scouting" view)
The scouting detail has a "Team report | Player scouting" switch (`scoutView`, `setScoutView()`). Player
scouting = a checklist of the opponent's players (`scoutPF.checked`), filters (rotation, set type, first ball /
transition, serve type, plus the page's Season filter) and one card per checked player (`spCard()`): attack
stats, attack by rotation / set type / pass before the set, serving by type, passing by zone (attempts, pass
rating on the dashboard's `rcvPassScale`, error %) split all serves / vs jump spin / vs float, Pass & Hit
(`spPassHitTable()`: his first-ball attacks off a set after his own pass vs when someone else passed, same
rules as the Reports card; atk rows carry passer + rally won), and two charts (`spCourt()`).
"Export checked to PDF (2 per page)" = `exportScoutPlayersPDF()`.
- Data: `opp_rotation_stats[opp].players` from `compute_scout_players()` (see its docstring for row layouts).
- Third view "Setter locations" (`buildScoutSetterMap()`, state `scoutMap`): their half court as the 9 DVW
  zones (seen from Loyola's side, net at the bottom: 1 6 5 / 9 8 7 / 2 3 4), each zone = the set distribution
  (Left / Quick / Right / D-ball / BIC / Dump) of sets made FROM that zone, sets + share, Eff off those sets;
  box shade = how often he sets from there. Setter checklist; Show = each setter / checked setters combined /
  whole team (`mode: 'team'`, every set by any setter, checklist ignored); rotation and pass
  filters; `exportSetterMapPDF()` = one full page per map. Set zone = `set_zone_of()` in aggregate.py (exact
  set spot `xy_s`, else code: code[9] for '~~~3' style, code[10] for '~~~24C' style). This also fixed
  setting.sets set_zone (r[7]) for ~1,600 sets that had read the wrong character.
- Lines are drawn from the exact court spots VolleyMetrics clicked: `parse_dvw.py` stores DVW fields 4/5/6 as
  `xy_s` / `xy_m` / `xy_e` (int 'yyxx', x = v % 100, y = v // 100) and the coded landing zone as `vm_end`.
  Every attack / serve is stored from the hitter's (server's) side: net y=50, far baseline ~89.5, sidelines
  ~11.5 / 88.5, his left = small x. Charts turn the court 180 degrees (x -> 100-x, y down the page): their side
  on top, Loyola's at the bottom. `xy_m` on attacks = where the ball met the block / crossed the net (blocked
  lines stop there). Check: 99.2% of in-court attack lines and 99.6% of serve lines land in the coded zone.
- Existing scout sessions in volleyball_data.json were backfilled with these fields; new files get them from the
  parser. Loyola's own charts were NOT changed (user's choice) and still use the old zone fields.

### X6 / X8 auto-correction (always on)
X6 is impossible in R4-R6 and X8 in R1-R3, so `swap_right_side()` (aggregate.py) swaps every such code
(X6 in R4-R6 -> X8, X8 in R1-R3 -> X6, start zone adjusted) for Loyola AND opponents, with no cap.
The high versions get the same rule (V6 in R4-R6 -> V8, V8 in R1-R3 -> V6). In scouting, an attack with no
X8/V8 code that only starts in zone 1/9 counts as Right (not D-ball) in R1-R3 (`atk_zone_cat(action, rot)`),
so D-balls never show in Rotation 1-3.
It uses the same rotation the pages show: match files = coded setter zone (lineup as fallback, via
`fix_right_side_typos()`), scout files = setter's lineup position. Fixes are listed in `combo_fixes`
and printed when aggregate.py runs. Rotation boxes are labeled "Rotation 1" ... "Rotation 6".

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

**Correction (verified Nov 2026 against the court coordinates):** in VM attack codes `code[10]` is the END
(landing) zone 1-9 (zones 7/8/9 included), `code[11]` its sub-zone letter, `code[12]` the attack type
(H hard / T tip / P soft) and `code[13]` the NUMBER OF BLOCKERS (0-4), not the end zone. The layout above is
what the Loyola charts still use (left unchanged on purpose). New code should use `vm_end` / `xy_*` instead.

Evaluation codes for attacks: `#`=kill, `=`=error (out/net), `/`=blocked, `+`=positive/in-play, `-`=negative/in-play.

---

## Zones

- Zones 1–6 = standard court zones (front row 4/3/2, back row 5/6/1)
- Zones 7/8/9 = exist on LUC's side only (back-row attack starts, serve origins) — **do NOT appear in ZONE_DST** (attacks never land in extended zones)
