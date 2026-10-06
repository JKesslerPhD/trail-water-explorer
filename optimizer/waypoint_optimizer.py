#!/usr/bin/env python3
"""Pick a small set of water waypoints along a route: high chance of water, short carries, few points.

Reads the offline app's data.json (see docs/DATA_FORMAT.md) and writes
  <out>/water-waypoints.gpx   <wpt> elements, one per chosen source, placed ON the route
  <out>/water-waypoints.csv   the same rows with every field
  <out>/route-with-water.gpx  the route track plus the waypoints, ready for the GPX course splitter

    python3 optimizer/waypoint_optimizer.py                       # template data, default weights
    python3 optimizer/waypoint_optimizer.py --wp-cost 2 --risk 8  # sparser, more careful
    python3 optimizer/waypoint_optimizer.py --data my/data.json --kind any

Cost, in "mile-equivalents", of a candidate set of waypoints:

    sum over gaps g between consecutive chosen points of carry(g)
  + wp_cost for every chosen point
  + off_pen * (miles the source sits off the route)
  + risk * (1 - reliability) for every chosen point
  + for every chosen point j (neighbours i and k): (1 - reliability_j) * (carry(gap i->k) - carry(i->j) - carry(j->k))

The last term is the first-order cost of source j being dry: you then carry the whole i->k gap
instead of two shorter ones. carry(g) is free from min_gap to target, rises gently past target,
and steeply past mid and far. The problem is solved exactly by dynamic programming over
(previous, current) pairs of chosen points.

Requires numpy.
"""
import argparse, csv, datetime, json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(HERE, "..", "cdt-water-app", "data.json")

DEFAULTS = dict(
    off_pen=4.0,     # cost per mile a source sits off the route
    risk=0.0,        # cost per unit of unreliability of a chosen source
    min_gap=2.0,     # waypoints closer than this are penalised as crowding
    crowd=0.6,       # crowding penalty per mile under min_gap
    target=5.0,      # carries up to this length are free
    s1=1.0, mid=8.0,  # slope between target and mid
    s2=2.5, far=15.0,  # slope between mid and far
    s3=5.0,          # slope past far
    wp_cost=1.2,     # fixed cost of adding a waypoint
    gmax=15.0,       # consider hops up to this long (always allows the next few sources)
    off_max=0.3,     # ignore sources farther than this off the route (miles)
    start=125,       # day of year the route is started
    days=140,        # days the whole route takes
    half=21,         # +/- days of arrival uncertainty (worst case over this window)
    kind="usable",   # any = P(q>=1), usable = P(q>=2), good = P(q>=3)
    min_conf=0,      # minimum coarse confidence flag (0, 1 or 2)
)
KIND = {"any": 0, "usable": 1, "good": 2}


# ---------------------------------------------------------------- data
class Data:
    def __init__(self, path):
        D = json.load(open(path))
        self.grid = np.array(D["grid"], float)
        self.total = float(D["track"]["total"])
        t = D["track"]
        lat = np.cumsum(np.r_[t["lat0"], t["dlat"]]) / 1e5
        lon = np.cumsum(np.r_[t["lon0"], t["dlon"]]) / 1e5
        self.route = np.c_[lat, lon]
        self.sec = D.get("sec", [])
        self.src = [dict(name=w[0], mile=w[1], off=w[2], lat=w[3], lon=w[4], elev=w[5], conf=w[6], sec=w[7],
                         c=np.array(w[8], float) / 100.0, vol=w[10] if len(w) > 10 else None) for w in D["wp"]]
        self.template = bool(D.get("template"))

    def interp(self, arr, doy):
        return float(np.interp(doy, self.grid, arr))

    def sched(self, mile, P):
        """Day of year a hiker reaches this mile, assuming a constant pace across the whole route."""
        return P["start"] + mile * P["days"] / self.total

    def reliability(self, s, P):
        """Chance the source has water on arrival: the WORST value over +/- `half` days of arrival uncertainty.
        Sources flagged as volatile are scaled down toward the low end of their year-to-year range."""
        d0 = self.sched(s["mile"], P)
        k = KIND[P["kind"]]
        r = min(self.interp(s["c"][k], d0 + x) for x in range(-P["half"], P["half"] + 1, 7))
        if s["vol"]:
            lo = np.array(s["vol"][0]) / 100.0
            typ = s["c"][2]
            r *= min(1.0, self.interp(lo, d0) / max(self.interp(typ, d0), 1e-6))
        return r


# ---------------------------------------------------------------- optimiser
def carry_cost(g, P):
    """Convex carry penalty: free inside the target band, steeper for longer carries."""
    if g < P["min_gap"]:
        return P["crowd"] * (P["min_gap"] - g)
    x = 0.0
    if g > P["target"]:
        x += P["s1"] * (min(g, P["mid"]) - P["target"])
    if g > P["mid"]:
        x += P["s2"] * (min(g, P["far"]) - P["mid"])
    if g > P["far"]:
        x += P["s3"] * (g - P["far"])
    return x


def optimize(data, params=None):
    """Return (selected nodes, all nodes, merged params, total cost)."""
    P = {**DEFAULTS, **(params or {})}
    cand = sorted((s for s in data.src if s["off"] <= P["off_max"] and s["conf"] >= P["min_conf"]), key=lambda s: s["mile"])
    if not cand:
        raise SystemExit("no candidate sources: widen --off-max or lower --min-conf")
    nodes = ([dict(mile=0.0, r=1.0, off=0.0, name="START")]
             + [dict(mile=s["mile"], off=s["off"], r=data.reliability(s, P), name=s["name"], src=s) for s in cand]
             + [dict(mile=data.total, r=1.0, off=0.0, name="END")])
    n = len(nodes)
    mi = np.array([x["mile"] for x in nodes]); r = np.array([x["r"] for x in nodes]); off = np.array([x["off"] for x in nodes])
    f = lambda g: carry_cost(g, P)
    add = lambda k: (P["wp_cost"] + P["risk"] * (1 - r[k]) + P["off_pen"] * off[k]) if k < n - 1 else 0.0

    succ = []
    for j in range(n):
        ks = [k for k in range(j + 1, n) if mi[k] - mi[j] <= P["gmax"]]
        if len(ks) < 4:
            ks = list(range(j + 1, min(n, j + 5)))              # long dry stretches: always allow the next few sources
        if j < n - 1 and (n - 1) not in ks and mi[n - 1] - mi[j] <= P["gmax"] * 2:
            ks.append(n - 1)
        succ.append(sorted(set(ks)))

    INF = 1e18
    best, back = {}, {}
    by_second = [[] for _ in range(n)]                          # by_second[j] = list of i with a state (i, j)
    for k in succ[0]:
        best[(0, k)] = f(mi[k] - mi[0]) + add(k); back[(0, k)] = None; by_second[k].append(0)
    for j in range(1, n - 1):
        for i in list(by_second[j]):
            c0, gij = best[(i, j)], mi[j] - mi[i]
            for k in succ[j]:
                gjk, gik = mi[k] - mi[j], mi[k] - mi[i]
                c = c0 + f(gjk) + add(k) + (1 - r[j]) * (f(gik) - f(gij) - f(gjk))
                key = (j, k)
                if c < best.get(key, INF):
                    if key not in best:
                        by_second[k].append(j)
                    best[key], back[key] = c, (i, j)
    ends = [key for key in best if key[1] == n - 1]
    if not ends:
        raise SystemExit("no feasible path to the end of the route: raise --gmax or --off-max")
    endkey = min(ends, key=lambda key: best[key])
    path, key = [], endkey
    while key is not None:
        path.append(key[1])
        prev = back[key]
        if prev is None:
            path.append(key[0]); break
        key = prev
    path.reverse()
    return [nodes[i] for i in path if 0 < i < n - 1], nodes, P, best[endkey]


def simulate(sel_miles, sel_r, total, nsim=4000, seed=0):
    """Monte-Carlo: each chosen waypoint independently has water with probability r.
    Returns total route miles spent in carries longer than 10 mi, per simulation."""
    rng = np.random.default_rng(seed)
    m = np.r_[0.0, sel_miles, total]; rr = np.r_[1.0, sel_r, 1.0]
    over10 = np.zeros(nsim)
    for s in range(nsim):
        ok = rng.random(len(rr)) < rr; ok[0] = ok[-1] = True
        gaps = np.diff(m[ok])
        over10[s] = gaps[gaps > 10].sum()
    return over10


# ---------------------------------------------------------------- output
def project(route, lat, lon):
    """Nearest point on the route polyline to (lat, lon): (distance m, lat, lon)."""
    cl = math.cos(math.radians(lat))
    d = np.hypot((route[:, 1] - lon) * cl * 111320, (route[:, 0] - lat) * 110574)
    k = int(d.argmin()); best = (float(d[k]), route[k, 0], route[k, 1])
    for a, b in ((k - 1, k), (k, k + 1)):
        if a < 0 or b >= len(route):
            continue
        ax, ay = (route[a, 1] - lon) * cl * 111320, (route[a, 0] - lat) * 110574
        bx, by = (route[b, 1] - lon) * cl * 111320, (route[b, 0] - lat) * 110574
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        t = 0 if L2 == 0 else max(0, min(1, -(ax * vx + ay * vy) / L2))
        dd = math.hypot(ax + t * vx, ay + t * vy)
        if dd < best[0]:
            best = (dd, route[a, 0] + t * (route[b, 0] - route[a, 0]), route[a, 1] + t * (route[b, 1] - route[a, 1]))
    return best


def date_of(doy, year=2026):
    return (datetime.date(year, 1, 1) + datetime.timedelta(days=int(round(doy)) - 1)).strftime("%b %d").replace(" 0", " ")


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_rows(data, sel, P):
    rows = []
    for k, x in enumerate(sel):
        s, r = x["src"], x["r"]
        d0 = data.sched(s["mile"], P)
        p_flow = data.interp(s["c"][2], d0)
        dist, la, lo = project(data.route, s["lat"], s["lon"])
        tier = "" if r >= 0.85 else ("~" if r >= 0.6 else "?")
        nxt = (sel[k + 1]["mile"] if k + 1 < len(sel) else data.total) - s["mile"]
        name = (f"{tier}{s['mile']:.0f} {s['name']}")[:15].rstrip()          # watch course-point names are limited to 15 characters
        desc = (f"Route mi {s['mile']:.1f} | water {r * 100:.0f}% low-end, flowing {p_flow * 100:.0f}% typical | ~{date_of(d0)} | "
                + (f"{s['elev']:,} ft | " if s["elev"] else "") + f"next water {nxt:.1f} mi"
                + (f" | {s['off']:.2f} mi off route" if s["off"] > 0.1 else "")
                + (" | varies a lot year to year" if s["vol"] else ""))[:255]
        rows.append(dict(order=k + 1, mile=round(s["mile"], 1), name=name, source=s["name"], lat=la, lon=lo, elev_ft=s["elev"],
                         reliability=round(r, 3), p_flowing=round(p_flow, 3), tier=tier or "A", date=date_of(d0),
                         gap_next=round(nxt, 1), volatile=bool(s["vol"]), off_mi=s["off"], desc=desc))
    return rows


def wpt_xml(rows):
    return "".join(
        f'  <wpt lat="{r["lat"]:.7f}" lon="{r["lon"]:.7f}">' + (f'<ele>{r["elev_ft"] * 0.3048:.1f}</ele>' if r["elev_ft"] else "")
        + f'<name>{esc(r["name"])}</name><desc>{esc(r["desc"])}</desc><sym>Water</sym><type>Water</type></wpt>\n' for r in rows)


def write_outputs(data, rows, out, title):
    os.makedirs(out, exist_ok=True)
    head = '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="trail-water-explorer optimizer" xmlns="http://www.topografix.com/GPX/1/1">\n'
    w = wpt_xml(rows)
    open(os.path.join(out, "water-waypoints.gpx"), "w").write(f"{head}  <metadata><name>{esc(title)}</name></metadata>\n{w}</gpx>\n")
    trk = "".join(f'    <trkpt lat="{a:.6f}" lon="{b:.6f}"/>\n' for a, b in data.route)
    open(os.path.join(out, "route-with-water.gpx"), "w").write(
        f"{head}  <metadata><name>{esc(title)}</name></metadata>\n{w}  <trk><name>{esc(title)}</name><trkseg>\n{trk}  </trkseg></trk>\n</gpx>\n")
    with open(os.path.join(out, "water-waypoints.csv"), "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["order", "route_mile", "name_on_watch", "source_name", "lat_on_route", "lon_on_route", "elev_ft",
                     "chance_of_water_low_end", "chance_flowing_or_better_typical", "tier(A>=85%,~60-85%,?<60%)",
                     "est_date", "miles_to_next_water", "volatile", "off_route_mi", "description"])
        for r in rows:
            cw.writerow([r["order"], r["mile"], r["name"], r["source"], f'{r["lat"]:.7f}', f'{r["lon"]:.7f}', r["elev_ft"],
                         r["reliability"], r["p_flowing"], r["tier"], r["date"], r["gap_next"], int(r["volatile"]), r["off_mi"], r["desc"]])


def summarize(data, sel):
    mm = np.array([x["mile"] for x in sel]); rr = np.array([x["r"] for x in sel])
    gaps = np.diff(np.r_[0, mm, data.total])
    return (f"{len(sel)} waypoints ({len(sel) / data.total * 100:.1f} per 100 mi) | planned gaps: median {np.median(gaps):.1f} mi, "
            f"<=5 mi {np.mean(gaps <= 5):.0%}, >10 mi {np.mean(gaps > 10):.0%}, longest {gaps.max():.0f} mi | "
            f"reliability: mean {rr.mean():.2f}, <0.7 {np.mean(rr < .7):.0%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=DEFAULT_DATA, help="app data.json (default: the template data)")
    ap.add_argument("--out", default=os.path.join(HERE, "example-output"), help="output directory")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), default=None, help=f"default {v}")
    a = ap.parse_args()
    over = {k: getattr(a, k) for k in DEFAULTS if getattr(a, k) is not None}
    if over.get("kind", "usable") not in KIND:
        sys.exit("--kind must be any, usable or good")
    data = Data(a.data)
    sel, nodes, P, cost = optimize(data, over)
    rows = build_rows(data, sel, P)
    write_outputs(data, rows, a.out, "Water waypoints" + (" (template data)" if data.template else ""))
    print(summarize(data, sel) + f" | total cost {cost:.0f}")
    over10 = simulate([x["mile"] for x in sel], [x["r"] for x in sel], data.total)
    print(f"if each chosen source is independently dry with probability 1 - reliability: mean route miles in carries over 10 mi = {over10.mean():.0f}")
    print("wrote", os.path.relpath(a.out))


if __name__ == "__main__":
    main()
