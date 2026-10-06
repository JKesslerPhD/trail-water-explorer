#!/usr/bin/env python3
"""Generate synthetic template datasets for the three front ends.

    python3 scripts/make_template_data.py

Writes (all synthetic, deterministic, standard library only):
    cdt-water/data.js          window.CDT_WP, window.CDT_MODEL
    azt-water/data.js          window.AZT_WP, window.AZT_MODEL
    cdt-water-app/data.json    offline-app bundle

The numbers come from a made-up smooth seasonal curve plus noise. They exist only so the
pages render and so the shape of every field is documented by example (see docs/DATA_FORMAT.md).
Replace these files with output from your own model to use the front ends for real.
"""
import json, math, random, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rnd = random.Random(20260101)
sig = lambda x: 1 / (1 + math.exp(-x))
clamp = lambda v, a, b: max(a, min(b, v))
MON_MID = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]   # mid-month day of year

SRC_TYPES = ["Spring", "Creek", "Stream", "Water Tank", "Windmill", "Cache Box", "Lake", "Pond", "Trough", "River"]
BASE_BY_TYPE = {"Spring": 2.6, "Creek": 2.8, "Stream": 2.7, "Water Tank": 2.0, "Windmill": 1.9, "Cache Box": 1.6,
                "Lake": 3.0, "Pond": 1.8, "Trough": 1.9, "River": 3.4}
SEC_CDT = ["New Mexico", "Colorado", "Wyoming", "S. Montana / Idaho", "N. Montana"]
YEAR_LABELS = ["≤2016"] + [str(y) for y in range(2017, 2027)]

# ---------------------------------------------------------------- seasonal model
def make_source(grid, base, amp, wet_peak, jitter):
    """Return per-grid arrays: q (0-4), d (% dry), ql (-1..1), s (-2..2) and their half-widths."""
    g0, g1 = grid[0], grid[-1]
    q, d, ql, s, hq, hd, hql, hs = ([] for _ in range(8))
    for doy in grid:
        t = (doy - g0) / (g1 - g0)                         # 0..1 across the season
        snowmelt = math.exp(-((t - wet_peak) ** 2) / 0.05)     # a spring/early-summer bump
        qv = clamp(base + amp * (snowmelt - 0.45) - 0.9 * t * (4 - base) / 4 + jitter, 0, 4)
        dv = clamp(100 * sig(-(qv - 0.9) * 1.7), 0, 100)
        q.append(round(qv, 2)); d.append(int(round(dv)))
        ql.append(round(clamp(0.25 * (qv - 2) - 0.2 * t, -1, 1), 2))
        s.append(round(clamp(0.45 * (qv - 2), -2, 2), 2))
        edge = 1 + 0.9 * abs(t - 0.5)                                    # wider bands at the ends of the season
        hq.append(round(0.35 * edge, 2)); hd.append(int(round(12 * edge)))
        hql.append(round(0.38 * edge, 2)); hs.append(round(0.35 * edge, 2))
    return q, d, ql, s, hq, hd, hql, hs

def year_effects(labels, rng, base_dry):
    out = {k: {"eff": [], "lo": [], "hi": []} for k in ("q", "d", "ql", "s")}
    for i in range(len(labels)):
        w = rng.uniform(-1, 1)                                           # +1 wet year, -1 dry year
        e = {"q": 0.12 * w, "d": base_dry - 7 * w, "ql": 0.05 * w, "s": 0.08 * w}
        wd = {"q": 0.07, "d": 3.5, "ql": 0.1, "s": 0.08}
        for k in out:
            out[k]["eff"].append(round(e[k], 3 if k != "d" else 1))
            out[k]["lo"].append(round(e[k] - wd[k], 3 if k != "d" else 1))
            out[k]["hi"].append(round(e[k] + wd[k], 3 if k != "d" else 1))
    return out

def heat_grid(models, miles, grid, n_bins, keys, bin_mi=50):
    """Average the per-source curves into (month x 50-mile bin); None where there is no data."""
    heat = {k: [[None] * n_bins for _ in range(12)] for k in keys}
    first, last = grid[0], grid[-1]
    for m in range(12):
        doy = MON_MID[m]
        if doy < first or doy > last:
            continue
        idx = min(len(grid) - 2, max(0, int((doy - first) / (grid[1] - grid[0]))))
        f = (doy - grid[idx]) / (grid[idx + 1] - grid[idx])
        for b in range(n_bins):
            members = [i for i, mi in enumerate(miles) if int(mi // bin_mi) == b]
            if not members:
                continue
            for k in keys:
                key = {"q": 0, "d": 1, "ql": 2, "s": 3}[k]
                vals = [models[i][key][idx] + (models[i][key][idx + 1] - models[i][key][idx]) * f for i in members]
                v = sum(vals) / len(vals)
                heat[k][m][b] = round(v, 2) if k != "d" else round(v, 2)
    return heat

# ---------------------------------------------------------------- shared route
def make_route(n_pts, lat0, lon0, step_deg):
    """A meandering, roughly northbound synthetic line. Returns lists of lat, lon."""
    lat, lon, hd = lat0, lon0, 0.0
    la, lo = [lat], [lon]
    for i in range(n_pts - 1):
        hd += rnd.uniform(-0.1, 0.1) - 0.06 * hd + 0.03 * math.sin(i / 90)
        lat += step_deg * math.cos(hd)
        lon += step_deg * math.sin(hd) / math.cos(math.radians(lat))
        la.append(lat); lo.append(lon)
    return la, lo

def hav_mi(a, b, c, d):
    R = 6371008.8
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h)) / 1609.344

def source_name(i):
    t = rnd.choice(SRC_TYPES)
    return t, f"Sample {t} {i + 1:02d}"

# ================================================================ CDT tool page + app
def build_cdt():
    grid = list(range(91, 302, 7))                                          # 31 points, Apr 1 - Oct 28
    n_pts = 1500
    la, lo = make_route(n_pts, 36.9, -107.4, 0.0036)
    cum = [0.0]
    for i in range(1, n_pts):
        cum.append(cum[-1] + hav_mi(la[i - 1], lo[i - 1], la[i], lo[i]))
    total = cum[-1]
    n_src = 54
    miles = sorted(rnd.uniform(2, total - 2) for _ in range(n_src))
    n_bins = int(total // 50) + 1
    sec_len = total / 5
    wps, models, app_wp = [], [], []
    vol_ix = set(rnd.sample(range(n_src), 6))
    for i, mi in enumerate(miles):
        typ, name = source_name(i)
        base = BASE_BY_TYPE[typ] + rnd.uniform(-0.5, 0.5)
        m = make_source(grid, base, rnd.uniform(0.4, 1.4), rnd.uniform(0.15, 0.4), rnd.uniform(-0.3, 0.3))
        models.append(m)
        k = min(range(n_pts), key=lambda j: abs(cum[j] - mi))
        off_mi = rnd.choice([0.0] * 6 + [0.1, 0.3, 0.6])
        slat = la[k] + off_mi / 69.0 * 0.7
        slon = lo[k] + off_mi / 55.0 * 0.7
        elev_m = int(1900 + 900 * math.sin(i / 7) + rnd.uniform(-200, 200))
        sec = min(4, int(mi // sec_len))
        conf = rnd.choice([0, 1, 1, 2, 2, 2])
        wps.append([name, round(mi, 1), sec, round(slat, 4), round(slon, 4), elev_m, conf])
        # app record: P(q>=k) in percent for k = 1..4 on the grid
        c = [[int(round(100 * sig((qv - (kk - 0.5)) * 1.6))) for qv in m[0]] for kk in (1, 2, 3, 4)]
        vol = None
        if i in vol_ix:
            tau = rnd.uniform(0.9, 1.5)
            lg = [math.log(max(0.02, min(0.98, v / 100)) / (1 - max(0.02, min(0.98, v / 100)))) for v in c[2]]
            vol = [[int(round(100 * sig(x - 1.28 * tau))) for x in lg], [int(round(100 * sig(x + 1.28 * tau))) for x in lg], round(tau, 2)]
        alts = [f"Sample {typ} (alt name)"] if rnd.random() < 0.15 else []
        app_wp.append([name, round(mi, 2), off_mi, round(slat, 4), round(slon, 4), int(elev_m * 3.281), conf, sec, c, alts, vol])

    years = year_effects(YEAR_LABELS, rnd, 11.0)
    years["labels"] = YEAR_LABELS
    years["few"] = [True] + [False] * 10
    model = {
        "grid": grid,
        "m": {k: [mm[j] for mm in models] for k, j in (("q", 0), ("d", 1), ("ql", 2), ("s", 3))},
        "h": {k: [mm[j] for mm in models] for k, j in (("q", 4), ("d", 5), ("ql", 6), ("s", 7))},
        "years": years,
        "heat": heat_grid(models, miles, grid, n_bins, ("q", "d", "ql", "s")),
        "def": n_src // 2,
        "legmap": {"thin": [0.0, round(total, 2)], "true": [0.0, round(total, 2)]},   # identity: no thinning correction
        "vol": {str(i): [round(rnd.uniform(0.9, 1.5), 3), round(rnd.uniform(0.12, 0.5), 3)] for i in sorted(vol_ix)},
    }
    with open(os.path.join(ROOT, "cdt-water", "data.js"), "w") as f:
        f.write("window.TEMPLATE_DATA=true;\n")
        f.write("window.CDT_WP=" + json.dumps(wps, separators=(",", ":")) + ";\n")
        f.write("window.CDT_MODEL=" + json.dumps(model, separators=(",", ":")) + ";\n")

    # offline app bundle: route is delta-encoded in 1e-5 degrees
    ilat = [int(round(v * 1e5)) for v in la]; ilon = [int(round(v * 1e5)) for v in lo]
    bundle = {
        "v": 1, "template": True, "grid": grid,
        "track": {"lat0": ilat[0], "lon0": ilon[0],
                  "dlat": [b - a for a, b in zip(ilat, ilat[1:])], "dlon": [b - a for a, b in zip(ilon, ilon[1:])],
                  "total": round(total, 2)},
        "legmap": {"thin": [0.0, round(total, 4)], "true": [0.0, round(total, 4)]},
        "wp": app_wp, "sec": SEC_CDT,
    }
    with open(os.path.join(ROOT, "cdt-water-app", "data.json"), "w") as f:
        json.dump(bundle, f, separators=(",", ":"))
    print(f"cdt: {n_src} sources, {total:.0f} mi, {n_bins} heat bins")

# ================================================================ AZT page
def build_azt():
    grid = list(range(22, 352, 7))                                          # 48 points, most of the calendar year
    total = 300.0
    n_src = 40
    miles = sorted(rnd.uniform(1, total - 1) for _ in range(n_src))
    secs = [["Template Section A", 0, 100], ["Template Section B", 100, 200], ["Template Section C", 200, 300]]
    n_bins = int(total // 50)
    wps, models = [], []
    vol_ix = set(rnd.sample(range(n_src), 4))
    la, lo = 33.0, -111.5
    for i, mi in enumerate(miles):
        typ, name = source_name(i)
        base = BASE_BY_TYPE[typ] - 0.4 + rnd.uniform(-0.5, 0.5)
        m = make_source(grid, base, rnd.uniform(0.3, 1.0), rnd.uniform(0.0, 0.25), rnd.uniform(-0.3, 0.3))
        models.append(m)
        sec = min(2, int(mi // 100))
        wps.append([name, round(mi, 1), sec, round(la + mi * 0.0125, 5), round(lo + 0.3 * math.sin(mi / 40), 5),
                    int(1300 + 600 * math.sin(i / 5) + rnd.uniform(-100, 100)), rnd.choice([0, 1, 2, 2])])
    years = year_effects(YEAR_LABELS, rnd, 14.0)
    azt_years = {"labels": YEAR_LABELS, "few": [True] + [False] * 10,
                 "n": [rnd.randint(80, 400) for _ in YEAR_LABELS], "d": years["d"]}
    model = {
        "grid": grid,
        "m": {k: [mm[j] for mm in models] for k, j in (("q", 0), ("d", 1), ("ql", 2), ("s", 3))},
        "h": {k: [mm[j] for mm in models] for k, j in (("q", 4), ("d", 5), ("ql", 6), ("s", 7))},
        "years": azt_years,
        "heat": heat_grid(models, miles, grid, n_bins, ("q", "d", "s")),
        "vol": {str(i): [round(rnd.uniform(1.1, 1.9), 2), round(rnd.uniform(0.12, 0.5), 2)] for i in sorted(vol_ix)},
        "def": n_src // 2,
        "secs": secs,
    }
    with open(os.path.join(ROOT, "azt-water", "data.js"), "w") as f:
        f.write("window.TEMPLATE_DATA=true;\n")
        f.write("window.AZT_WP=" + json.dumps(wps, separators=(",", ":")) + ";\n")
        f.write("window.AZT_MODEL=" + json.dumps(model, separators=(",", ":")) + ";\n")
    print(f"azt: {n_src} sources, {n_bins} heat bins")

if __name__ == "__main__":
    build_cdt()
    build_azt()
