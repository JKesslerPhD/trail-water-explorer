# Data format

The front ends read plain data files and do no modeling of their own. Whatever produces these files (a regression, a lookup table, hand-entered estimates) is up to you. `scripts/make_template_data.py` writes a complete synthetic example of each file, which is the best reference for the exact shapes.

Conventions used throughout:

- A **source** is one water source. Sources are indexed `0..N-1` and every per-source array shares that order.
- A **grid** is a list of days of the year (1 = Jan 1). Every per-source curve has one value per grid day.
- Quantity scale: `0` dry, `1` stagnant or pools, `2` trickle, `3` flowing, `4` abundant.

## `cdt-water/data.js` and `azt-water/data.js`

Two globals, `window.<TRAIL>_WP` and `window.<TRAIL>_MODEL` (`CDT_` or `AZT_`). Optionally `window.TEMPLATE_DATA = true` shows the "template data" banner; delete that line for real data.

### `*_WP`: one row per source

```
[name, mile, sectionIndex, lat, lon, elevationMeters, confidence]
```

| Field | Meaning |
|:-|:-|
| `name` | Display name |
| `mile` | Miles along the route from the start |
| `sectionIndex` | Index into the section list (`SEC` in `cdt-water/index.html`, `*_MODEL.secs` for AZT) |
| `lat`, `lon` | Decimal degrees |
| `elevationMeters` | Shown in feet; may be `null` |
| `confidence` | `0` limited data, `1` moderate, `2` good. A coarse flag only; no counts |

### `*_MODEL`

| Key | Shape | Meaning |
|:-|:-|:-|
| `grid` | `[G]` | Days of year |
| `m.q`, `m.d`, `m.ql`, `m.s` | `[N][G]` | Typical-year curves: quantity (0 to 4), percent chance dry (0 to 100), quality (about -1 to +1), sentiment (about -2 to +2) |
| `h.q`, `h.d`, `h.ql`, `h.s` | `[N][G]` | Half-widths of the approximate 80% band around each curve, in the same units |
| `years` | object | Year-to-year effects, see below |
| `heat.q`, `heat.d`, `heat.s` (`heat.ql` on CDT) | `[12][B]` | Month (Jan = 0) by 50-mile-bin averages for the trail-wide heatmap. `null` where there is no data |
| `def` | int | Source index shown first |
| `vol` | `{ "<index>": [a, b] }` | Sources whose behavior swings a lot between years. `a` is the year-to-year spread in quantity points and `b` is the spread in dry fraction. These widen the shaded bands and show a note |
| `legmap` (CDT only) | `{thin: [...], true: [...]}` | Piecewise-linear map from heat-bin miles to displayed route miles. Use two identical knots (`[0, total]`) if you do not need a correction |
| `secs` (AZT only) | `[[name, startMile, endMile], ...]` | Section names for the dropdown |

`years` has `labels` (strings), `few` (booleans, `true` draws a hollow dot for limited data), and `{eff, lo, hi}` arrays per measure. CDT has `q`, `d`, `ql` and `s`. AZT has `d` only, plus `n` (one weight per year, used to compute the baseline dry percentage).

## `cdt-water-app/data.json`

The offline app bundle. Everything lives in one file so the service worker can cache it.

```json
{
  "v": 1,
  "template": true,
  "grid": [91, 98, ...],
  "track":  { "lat0": 3690000, "lon0": -10740000, "dlat": [...], "dlon": [...], "total": 372.9 },
  "legmap": { "thin": [0, 372.9], "true": [0, 372.9] },
  "wp":  [ [name, mile, offRouteMiles, lat, lon, elevationFeet, confidence, sectionIndex, c, alts, vol], ... ],
  "sec": ["Section 1", "Section 2", ...]
}
```

- `track` is the route, delta-encoded: `lat0`/`lon0` are the first point in 1e-5 degrees, `dlat`/`dlon` are integer steps to each next point. `total` is the route length in miles.
- `legmap` maps miles measured along the encoded track to the miles you want displayed (same idea as the tool page). Identical knots mean no correction.
- `wp[i].c` is four arrays (one value per grid day) holding `P(quantity >= 1)`, `P(>= 2)`, `P(>= 3)` and `P(>= 4)` in percent. Each day must be non-increasing from `>= 1` to `>= 4`. The app turns these into the probability bars and the "good water" score (`>= 3`).
- `wp[i].alts` is a list of other names for the same source, or `[]`.
- `wp[i].vol` is `null`, or `[lowArray, highArray, tau]`: lower and upper bounds of the good-water percentage across years (one value per grid day), plus a spread number.
- `offRouteMiles` above 0.25 shows an "off route" note.
- `template: true` shows the template notice in Settings. Omit it for real data.

## Versioning the offline app

`cdt-water-app/sw.js` and `index.html` carry a version `V`. Browsers only update an installed app when `sw.js` changes, so after replacing `data.json` run:

```bash
cd cdt-water-app && ./bump-version.sh 2
```

This keeps `sw.js`, `index.html` and the `?v=` strings in step. Do not overwrite a versioned URL in place behind a CDN; bump the number.
