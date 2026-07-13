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
- Setter breakdown TOTAL column: `K | E | BLK | ATT | EFF%`
- Any group/team subtotal row that has a K/E/Att/Eff% pattern

Per-combo columns in the setter breakdown table use 4 columns (K, E, ATT, EFF%) without BLK for space reasons; BLK appears in the TOTAL column only.

---

## Data Pipeline

```
parse_dvw.py → volleyball_data.json → aggregate.py → dashboard_data.json
                                                    ↓
                             dashboard_template.html (with __DASHBOARD_DATA__ placeholder)
                                                    ↓
                                          dashboard.html (final file)
```

After any change to `aggregate.py` or `parse_dvw.py`, rebuild with:
```bash
python3 aggregate.py volleyball_data.json dashboard_data.json
python3 -c "
with open('dashboard_template.html') as f: t = f.read()
with open('dashboard_data.json') as f: data = f.read()
open('dashboard.html','w').write(t.replace('__DASHBOARD_DATA__', data))
"
```

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
