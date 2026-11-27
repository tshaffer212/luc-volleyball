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

## Name spelling fixes

DVW rosters sometimes misspell names. `NAME_FIXES` in aggregate.py (`fix_names()`, run right after loading
volleyball_data.json) corrects every roster and action, so all pages and the portal use the fixed spelling.
Current: Stainslaw -> Stanislaw (#9 Chacinski). When renaming a current player, also rename his key in
`private/player_passwords.json` so he keeps the same portal password (his portal file id changes with the name).

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

### Season folders (`seasons.json`) and the upload page (`upload.py`)
- `seasons.json` = which folders in `Volleyball DVW Files` get read: `{folder, season (label), type, scout}`.
  `rebuild.py` reads it (its built-in `FOLDER_MAP` is only the fallback). "Spring 27" -> season "Spring 2027" and
  "Spring 2027 Scout" were added Nov 23, 2026. `LUC_DVW_ROOT` env var overrides the DVW folder (used for tests).
- Upload page: double-click `Upload DVW.command` (runs `python3 upload.py`) -> http://127.0.0.1:8765 opens. The page
  is `upload_page.html` (token placeholder `__TOKEN__`). Stdlib only, listens on 127.0.0.1, every API call needs the
  random per-start token + Host/Origin checks; `/preview/` serves only dashboard.html, dashboard_sets.json,
  players.html, scout_app.html, player_data/*.json (never private/).
- Flow: drop .dvw -> staged in a temp dir -> `inspect()` parses it with `parse_dvw.parse_dvw` (teams, date, type,
  suggested folder via `season_from_date()`: Jan-Jul = Spring YYYY, Aug - Dec 19 = Fall YYYY+1, Dec 20+ = Spring
  YYYY+1) and `check()` flags: Loyola file in a scout folder / opponent file in a Loyola folder, date vs folder
  season, a match whose file name doesn't say "vs" / VM (would count as a practice), same file already in the folder
  (identical = nothing to add; different = replace or skip), same match already on the dashboard (same date + loosely
  matched opponent, `same_team()`, read from dashboard_data.json `sessions` / `scout_index`), same-date practice.
- Save: new folders appended to seasons.json; files copied in; a replaced file's old copy goes to
  `Volleyball DVW Files/_replaced/<stamp>/<folder>/` and its old session is removed from volleyball_data.json
  (`drop_sessions()`, copy in `volleyball_data.backup.json`, git-ignored) because the parser skips files already in
  the dataset (Loyola key = season + file, scout = opponent + date). Then `rebuild.py` runs (with `--no-push` if the
  box is unticked), log streamed to the page; per-file outcome read from the parse lines (`Parsing X ...`,
  `[skip] X`). Stale `.git/*.lock` files older than 10 s are removed before a push.
- Downloads watcher (Nov 27, 2026): double-click `Watch Downloads.command` (runs `python3 watch_downloads.py`). Polls
  ~/Downloads every 3 s for finished .dvw files (size unchanged for a full check; .crdownload/.download/.part
  ignored), waits 8 s after the last new one, then handles the batch with upload.py's own code: `stage_file()` +
  `inspect()` (folder suggestion, warnings), and `run_job()` (save + rebuild, NO push unless `--push`). Clean files
  are filed (copied into the season folder, then the Downloads copy is removed once the filed copy's sha matches =
  a move; nothing is left in Downloads). Duplicates go to `Volleyball DVW Files/_duplicates/<folder>/`: an exact copy
  of a filed file (state 'identical') or the same match already on the dashboard under another name (`res['dup_of']`
  / `res['dup_msg']`, set by `check()`, and no other warning). A different version of a same-named file, errors and
  other warnings stay in Downloads and are listed in `watch_downloads.log` + a Mac notification. (Before Nov 27 the
  watcher moved filed downloads into `Downloads/DVW filed/`; that folder only holds copies and can be deleted.) Only files downloaded after the first start are handled (`.watch_downloads.json` keeps `since`,
  `seen`, `done`; `--since-days N` for older ones; `--once` for tests; `--no-rebuild`). upload.py's `run_job()` now
  holds a shared lock file `.rebuild.lock` (stale after 30 min) so the page and the watcher never rebuild at the same
  time. Hudl's Acceptable Use Policy forbids bots / automated access to VolleyMetrics, so downloading stays a manual
  click; everything after the download is automatic.
- Standard file names (Nov 27, 2026): new match / scouting files coming through the upload page or the watcher are
  saved as "LOY vs UCLA (3-23).dvw" (`STANDARD_NAMES` in upload.py; "/" can't be in a file name, so month-day).
  `apply_standard_name()` runs in `inspect()`: our team first; in a scouting file a team we've played (from the
  dashboard's match sessions) first, else the home team; abbreviations from `team_abbr.json` ("ours": "LOY", teams =
  full name -> code, loose match; VolleyMetrics download names "&date id HOME-AWAY(VM)" add new codes automatically
  via `learn_abbr()`; otherwise initials / first letters). Same name already in the folder: same match (same teams +
  set scores) keeps the name (identical / different version as before); a different match that day gets " (2)".
  The staged file is re-read under the new name and the original name is kept if date / type / teams (and, for
  scouting, the opponent label) would change. Practices and files without two different teams keep their names.
  Saved renames are logged in `Volleyball DVW Files/_file_names.csv` (saved_as, folder, original_name, saved_on) so
  the VolleyMetrics match id isn't lost. parse_dvw.py reads standard names like VM names: `STD_NAME` /
  `std_name_month_day()` -> opponent from the teams in the file (not the name), date = month/day from the name +
  year from the file (`date_with_month_day()`, no DD/MM vs MM/DD ambiguity). No existing file matches the pattern,
  so existing sessions are unchanged; files already filed are never renamed. Same parser change is in the Volley App
  engine.
- Scouting groups (Nov 27, 2026, aggregate.py scout block): every scout file is listed under EACH team in it that
  Loyola has played in a match (teams from Loyola's match files; loose key `_team_key()` drops university / college /
  men's / volleyball, so "Lewis", "Lewis Mens Volleyball" and "Lewis University (Men's)" are one team), as a shallow
  copy with `opponent` = the school name (`_label`: a "University / College" name if any, then the most common) and
  `_opp_side` fixed to that team's side (`_opp_fixed`, skipped by `resolve_opp_sides()`). A file with no team Loyola
  has played keeps the old label (file name / away team) and side detection. `scout_index` has one entry per
  (file, team). Old labels like LW / UCSD / Pepp / MCK merged into school names; teams Loyola never played (Central
  State, Park, Ottawa, NJIT, ...) no longer get their own entry. Harness picks the UC San Diego group (`SC_OPP`).
- On hold (user will bring it back): once Loyola posts its spring 2027 schedule, list every match (date, time CT,
  opponent, site) for the user to confirm; then gather every opponent's schedule the same way (confirm too); only
  then build a daily email ("matches to download" = opponents who played the day before). As of Nov 27, 2026 only the
  fall dates (Oct 24 at PFW, Nov 1 and Nov 14 at Lewis) and Jan 16, 2027 vs UCLA in Anaheim (7 PM PT, from UCLA's
  schedule) were confirmed. Use only schedule pages, never VolleyMetrics / Hudl.
- Duplicates removed Nov 23, 2026 (found by identical serve order = same match, even when coded twice):
  16 Loyola sessions + 8 scout sessions taken out of volleyball_data.json (pre-cleanup copy:
  `volleyball_data.backup.json`). Kept the VolleyMetrics "&date id TEAM-TEAM(VM)" version each time. Removed:
  Spring 2026 `LMV vs UH(1).dvw` + `LMV vs UCLA.dvw` (in-house copies of the Jan 9 UH / Jan 19 UCLA VM files; their
  files were already gone), Spring 2025 `...LUC-LU(VM)-2.dvw` + `1-8-25.mp4.dvw`, Fall 2026 `Lewis1.dvw`, 11 "Unknown
  Season" copies (Jan10PSU, PSU, PFW, quincy, the PEPP/OSU/QU VM copies, `& 2025-03-26 LMV-LMV`, 6233, `OSU Vs.
  Loyola`, `BSU vs Loyola (2)`), and scout copies `STA vs LEW` (x2), Quincy, UCSD5, LW4, MCK4, MCK5, and the second
  Pepp2 (re-added with a flipped date). Their files in read folders were moved to `Volleyball DVW Files/_duplicates/`
  (MISC is not read, left alone). 27 unique "Unknown Season" sessions remain (2024 VM matches, fall 2024 exhibitions,
  old MISC files).
- Scout dedup (parse_dvw `merge_session`) now also skips a file already read from the same folder (folder + file
  name from `filepath`): `parse_date` picks the non-future reading of an ambiguous date like 01/11/2026, so the
  same file could get a new date (Nov 1 once that passed) and slip past the opponent + date check.
- Scouting folders now carry the plain season label ("Spring 2026 Scout" folder -> season "Spring 2026") so the
  scouting Season filter has one entry per season (the old "Spring 2026 Scout" label only existed on duplicates).
- Known, not fixed: all 71 Spring 2025 sessions are typed "match", including 44 Loyola-vs-Loyola practice files.

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
- Competition from the coaches' Wins Leaderboard (`COMP_6V6` / `load_competition_6v6()` in aggregate.py ->
  `DATA.competition_6v6`): `private/wins_leaderboard.xlsx` (git-ignored, read with a stdlib xlsx reader), tab
  "6 v 6" = season 6v6 points by position group. Competition = (points - group average) / 2, spread over the
  practice dates he attended (practice files he appears in) and counted on those in view. Fall 2027 exception:
  #2 Ben Winokur and #13 Thomas Vesty missed practices, so their points are scaled to a full schedule
  (points x practices held / attended) and left out of the group averages (`COMP_6V6['scale']`). Sheet names /
  nicknames map to the roster via `COMP_6V6['aliases']`. A note under the Reports Points Added table (and on
  the Player tab / portal PA card) explains it for that season only; competition.csv is the fallback for
  other seasons. Next season: update `COMP_6V6` (season, scale list) or switch to win % from "6 v 6 2.0".
- Match competition (`compute_match_competition()` -> `DATA.competition_match` = {season: {pnum: {session_id:
  {set: [won_share, lost_share]}}}}): set winner from the final score of each set (`set_scores` = last point
  line per set, parse_dvw; match sessions backfilled; short sets to 15 count as full sets); each player gets his
  share of the set's rallies (in Loyola's lineup on that rally); someone who touched the ball in a set but was
  never in the lineup (the libero) gets the whole set. Match Competition = (sets won - sets lost) / 2 (50%
  baseline) over the match sessions / set in view. Practice + match Competition are ADDED when both are in view
  (`paCompetition(pn, season, dates, sids, setN)`; portal: `payload.compm`).
  Set rows are [won, lost, 1 if he played that set as the libero]. Virtual players (Jake Read 2.0 = 7L) get the
  sets #7 played as the libero and a copy of #7's practice 6v6 competition (`copy_of`).

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

- Team report PDF: "Export team report to PDF" (`exportScoutTeamPDF()`) feeds sections to the shared `_writePdf()`
  (each section fits one page; consecutive short ones merge; very wide ones print sideways): overview KPIs +
  rotation tables (`_stRotTables()`), set starts / subs (cards forced to 980px so text wraps), setting
  breakdown (`buildScoutSetting(name, true)` returns the pieces), where they passed / set from, pass-to-set
  grids, Pass & Hit. Uses the page's current Season and setting-breakdown filters. As many pages as needed.

- Red zone ("Situation" filter `scout-rz-filter`, `scRZ()`): rallies where, before the rally, the leading
  team has 20+ points (10+ in set 5) and the teams are within 3. Score comes from the point lines in the file
  (`pts_h` / `pts_a` = score before the rally, parse_dvw; scout sessions backfilled) via `in_red_zone()` in
  aggregate.py. Flags: setting.sets r[13], players.atk r[14], players.srv r[9], players.oth r[8]; team strip /
  rotation numbers from `opp_rotation_stats[opp].rz` (compute_scout_rotation_stats(rz_only=True)); subs use
  their own score (`scRZsub`). Applies to Team report, Player scouting, Setter locations and all PDFs; set starts
  and set-to-set changes are set-level, so they are not filtered (noted on the cards).

### Scouting match checklist (Nov 27, 2026)
"Matches" picker in the scouting detail header (`scFilesPicker()`, next to the Team report / Player scouting /
Setter locations switch): checklist of the opponent's files in the Season filter, grouped by season (All / None,
season checkbox + "only"), labeled "1/14 vs Roosevelt University" (vs = they were home, at = away). State `scFiles` =
null (all) or a Set of file indexes in `setting.files` order; reset when another opponent is opened; `scFilesOpen` /
`scFilesScroll` keep the panel open while ticking. `scFileOk(fi, files)` (season AND checklist) replaced every
section's season check (set starts, setting breakdown, Pass & Hit, player scouting, setter locations, combos chart).
Team strip, rotation cards, setters table and the team PDF use `scAgg(name)`: precomputed totals when nothing is
filtered, otherwise summed from per-file raw counts `opp_rotation_stats[opp].pf` / `pf_rz` (aggregate.py `_pf_raw()`;
percentages worked out on the page like compute_scout_rotation_stats does). So the Season filter now also changes the
team strip / rotation cards (before, those always showed every season). `setting.files` entries carry `side` and
`vs`. PDFs list the matches used. Harness: 'scout files consistency' (all files summed == precomputed, normal + red
zone) and views scout_UCSD_files2 / _players / _setmap / _none, pdf_scout_team_files2. Same in the engine template.

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

### Player tab session checklist
The Player tab's session filter is a checklist (Nov 27, 2026), not a single-session dropdown: `plSessions` = null
(all sessions in view) or a Set of session ids; `plSessList` = the player's sessions for the Matches / Practices /
Both chip. Panel `#pl-sess-panel` (reuses the Reports player picker's `.pp-*` styles): All / None, a Matches and a
Practices group (group checkbox + "only"), one checkbox per session. Ticking every session goes back to null. The
whole header redraws on each tick, so `plSessOpen` / `plSessScroll` keep the panel open and scrolled. Header line
shows "X of N matches, Y of M practices selected"; the Set filter lists sets from the selected matches; the player
PDF filter line lists the sessions. The same change is in the Volley App engine template (keep them in sync).
Harness view `player_<n>_sessmix` checks a mixed selection.

### Player tab skill rankings (coach dashboard only, Nov 27, 2026)
Section "Skill rankings" (`rankSec` in renderPlayerDetail, id `pl-sec-rank`, nav "Rankings", right after Overview;
in the player PDF it's one more block on the single page). Rows: Overall = the header's Points Added rank (same
list: teammates at his position with 10+ touches), then every skill with a Points Added weight of 5%+ for his
position (`paWeightsFor(group)`), ordered by weight: Attacking = eff (K-E-BLK)/Att, Passing = pass avg on the current
pass scale, Serving = ace % minus error %, Blocking = block pts per set (stuff 1, touch 0.5, same as the tile),
Defense = digs per set (D attempts minus dig errors / sets played), Setting = eff of attacks off his sets
(`plSetterEval().raw`), Competition = Points Added competition points. Columns: Rank ("#2 of 6", ties share a rank,
#1 green / last red when 3+), His, #1 (number only), position average (pooled over real teammates; virtual players
like 7L are ranked but never in averages). Everyone with attempts in the sessions in view is ranked (no minimum, user's
choice). Never shows who is above / below. NOT in the player portal (players.html). Same patch in the engine template.

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
- Sections follow what the player really does (`roleTotals()` / `does(skill)` in players.html: his season totals,
  matches + practices, all sessions; needs 30 attacks / passes / serves, 15 blocks / digs / free balls). No
  "Attack" tabs or "Expected vs Actual" (own swings) for setters / liberos, no Reception or Pass & Hit for
  non-passers, no block card for players who barely block; KPI tiles and Trend by Month columns follow the same
  rule (empty months dropped, setters get a Sets column). Rotation tables say they are matches only.
- Practices in the portal's pass-quality filters (Setter Breakdown / Attack by Setter phases): practice files have
  no rally markers, so aggregate.py cuts them into rallies (`practice_rallies()`: new rally at each serve and
  after each rally-ending action) and runs the same setter -> hitter -> bucket logic as matches. The result goes
  to `portal_extra.json` (`setter_atk_q`, git-ignored, NOT in dashboard_data so dashboard.html stays small);
  build_player_portal.py uses it instead of the bucket-less `setter_attacks` for practices. The coach
  dashboard's Reports setter breakdown still uses match data only (match_rot_stats).
- Opponent scouting in the portal ("My stats | Opponent scouting" switch): the coach dashboard's whole scouting
  section (Team report, Player scouting, Setter locations, Red zone, PDFs) runs in an iframe.
  `build_player_portal.py` writes `player_data/scouting.json` (scouting-only copy of dashboard_data via
  `build_scouting()`: opponent_stats, opp_rotation_stats, scout_index + `eff_base`; every other key empty),
  locked with a random shared key in `private/scout_key.txt` (never commit); that key is put in every player's
  own locked file (`payload.scout_key`). It also copies `dashboard_template.html` -> `scout_app.html` (code
  only, no data). players.html `openScouting()` unlocks the data, fills `__DASHBOARD_DATA__` and sets
  `window.SCOUT_APP` -> the template's `scoutAppInit()` (instead of `init()`) shows only the scout tab and the
  `html.scout-app` CSS hides the coach gate, header and nav. Push `scout_app.html` too.

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
