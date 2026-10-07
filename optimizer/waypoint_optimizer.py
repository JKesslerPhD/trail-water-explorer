#!/usr/bin/env python3
"""Choose which water sources to put on your watch.

THE PROBLEM
-----------
A route has far more water sources than you want as waypoints. Every extra waypoint is one more
thing to read on a small screen, but too few leaves you carrying water for 20 miles. This script
picks a small set of sources that
  * are likely to actually have water when you walk past,
  * keep the longest dry stretches ("carries") short, and
  * stay few in number.

THE IDEA IN PLAIN ENGLISH
-------------------------
Every possible set of waypoints gets a cost (lower is better), measured in "mile-equivalents":

    cost = carry penalties        a long gap between waypoints costs more than a short one
         + a fixed price          for each waypoint chosen, so we do not take all of them
         + a detour penalty       for sources that sit off the route
         + a reliability penalty  for sources that are often dry
         + a "what if it's dry?"  term (explained below)

The script finds the single cheapest set. It does this exactly (not by guessing) with a technique
called dynamic programming, described in section 5.

THE "WHAT IF IT'S DRY?" TERM
----------------------------
Suppose we choose waypoints i, j, k in that order. If j is dry, we really carry the whole i->k
stretch, not the two shorter pieces i->j and j->k. The extra penalty for that is

    carry(i->k) - carry(i->j) - carry(j->k)

and we charge it in proportion to how likely j is to be dry (1 - reliability of j). This is a
"first-order" estimate: it assumes only j fails, not j and its neighbors together.

HOW TO READ THIS FILE
---------------------
    1. Settings          every knob, with its default and what it does
    2. Loading the data  turns the app's data.json into plain Python objects
    3. Reliability       how likely is a source to have water when you arrive?
    4. Carry cost        how bad is a gap of N miles?
    5. The search        the dynamic program (the interesting part)
    6. Writing results   GPX / CSV / summary
    7. Command line

USAGE
-----
    python3 optimizer/waypoint_optimizer.py                          # template data, default settings
    python3 optimizer/waypoint_optimizer.py --wp-cost 2 --risk 8     # sparser, more cautious
    python3 optimizer/waypoint_optimizer.py --data my/data.json --kind any

Outputs (default folder optimizer/example-output/):
    water-waypoints.gpx   one <wpt> per chosen source, placed ON the route
    water-waypoints.csv   the same rows with every field
    route-with-water.gpx  the route plus those waypoints, ready for the GPX course splitter

Requires numpy.
"""
import argparse
import csv
import datetime
import json
import math
import os
import sys
from dataclasses import dataclass, field, fields, replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(HERE, "..", "cdt-water-app", "data.json")


# =====================================================================================
# 1. SETTINGS
# =====================================================================================
def setting(default, help_text):
    """One tunable setting. The help text also becomes the --help line for the command-line flag."""
    return field(default=default, metadata={"help": help_text})


@dataclass(frozen=True)
class Settings:
    """Every tunable number in one place. Each one is also a command-line flag (underscores -> dashes)."""

    # ---- Costs of choosing waypoints ------------------------------------------------
    wp_cost: float = setting(1.2, "fixed cost of adding any waypoint (higher = fewer waypoints)")
    risk: float = setting(0.0, "extra cost per unit of unreliability of a chosen source (higher = prefers surer water)")
    off_pen: float = setting(4.0, "cost per mile a source sits off the route")

    # ---- Shape of the carry penalty (see section 4) ---------------------------------
    min_gap: float = setting(2.0, "waypoints closer together than this (miles) are penalised as crowding")
    crowd: float = setting(0.6, "crowding penalty per mile under min_gap")
    target: float = setting(5.0, "carries up to this length (miles) are free")
    mid: float = setting(8.0, "miles where the penalty slope steepens the first time")
    far: float = setting(15.0, "miles where the penalty slope steepens the second time")
    s1: float = setting(1.0, "penalty per mile between target and mid")
    s2: float = setting(2.5, "penalty per mile between mid and far")
    s3: float = setting(5.0, "penalty per mile beyond far")

    # ---- Which sources and hops the search considers --------------------------------
    off_max: float = setting(0.3, "ignore sources farther than this (miles) from the route")
    min_conf: int = setting(0, "ignore sources whose coarse confidence flag (0, 1 or 2) is below this")
    gmax: float = setting(15.0, "normally only consider hops up to this many miles between waypoints")

    # ---- When you arrive, and what counts as "water" --------------------------------
    start: int = setting(125, "day of year you start the route (125 = early May)")
    days: int = setting(140, "how many days the whole route takes, at a constant pace")
    half: int = setting(21, "arrival-date uncertainty: plan for the worst day within +/- this many days")
    kind: str = setting("usable", "what counts as water: any (a pool counts), usable (a trickle), good (flowing)")


# Which of the app's four probability curves each `kind` reads:
# the app stores P(quantity >= 1), P(>= 2), P(>= 3), P(>= 4) as c[0..3].
KIND_TO_CURVE = {"any": 0, "usable": 1, "good": 2}
GOOD_CURVE = 2   # "flowing" curve, reported separately in the output


# =====================================================================================
# 2. LOADING THE DATA
# =====================================================================================
@dataclass
class Source:
    """One water source, as stored in the offline app's data.json."""
    name: str
    mile: float          # distance along the route from the start
    off_route: float     # miles the source sits from the route
    lat: float
    lon: float
    elev_ft: int
    conf: int            # coarse confidence: 0 limited data, 1 moderate, 2 good
    curves: np.ndarray   # shape (4, n_days): % chance of quantity >= 1, 2, 3, 4 for each day on the grid
    volatile: list       # None, or [low_band, high_band, spread] for sources that swing between years


class RouteData:
    """The route line, the day-of-year grid, and every source."""

    def __init__(self, path):
        raw = json.load(open(path))
        self.grid = np.array(raw["grid"], float)               # the days of the year the curves are sampled on
        self.total = float(raw["track"]["total"])              # route length in miles
        self.template = bool(raw.get("template"))              # True for the synthetic sample data

        # The route is stored compactly: first point, then integer steps in 1e-5 degrees. Undo that.
        t = raw["track"]
        lat = np.cumsum(np.r_[t["lat0"], t["dlat"]]) / 1e5
        lon = np.cumsum(np.r_[t["lon0"], t["dlon"]]) / 1e5
        self.route = np.c_[lat, lon]                           # shape (n_points, 2)

        # Each source is a list: [name, mile, off_route, lat, lon, elev_ft, conf, section, curves, alt_names, volatile]
        self.sources = [
            Source(name=w[0], mile=w[1], off_route=w[2], lat=w[3], lon=w[4], elev_ft=w[5], conf=w[6],
                   curves=np.array(w[8], float) / 100.0,       # percent -> probability
                   volatile=w[10] if len(w) > 10 else None)
            for w in raw["wp"]
        ]

    def value_on(self, series, day_of_year):
        """Read a per-day curve at any day (linear interpolation between grid days)."""
        return float(np.interp(day_of_year, self.grid, series))

    def arrival_day(self, mile, s: Settings):
        """Day of year you reach this mile, assuming a constant pace over the whole route."""
        return s.start + mile * s.days / self.total


# =====================================================================================
# 3. RELIABILITY
# =====================================================================================
def reliability(data: RouteData, src: Source, s: Settings):
    """Chance this source has water when you get there, between 0 and 1.

    Two cautious choices:
      * Arrival date is uncertain, so we take the WORST value within +/- `half` days
        of the planned date (checked every 7 days).
      * Sources that swing a lot from year to year are scaled down toward the low end of
        their year-to-year range.
    """
    curve = src.curves[KIND_TO_CURVE[s.kind]]
    planned = data.arrival_day(src.mile, s)

    # (a) worst case over the arrival window
    window = range(-s.half, s.half + 1, 7)
    worst = min(data.value_on(curve, planned + shift) for shift in window)

    # (b) volatile sources: shrink by (low end of the range) / (typical value) on the planned day
    if src.volatile:
        low_end = np.array(src.volatile[0]) / 100.0
        typical = src.curves[GOOD_CURVE]
        ratio = data.value_on(low_end, planned) / max(data.value_on(typical, planned), 1e-6)
        worst *= min(1.0, ratio)
    return worst


# =====================================================================================
# 4. CARRY COST
# =====================================================================================
def carry_cost(gap, s: Settings):
    """Penalty for a gap of `gap` miles between consecutive waypoints. Shape:

        cost
         |                                              /   slope s3
         |                                  ___________/
         |                      __________/                 slope s2
         |          ___________/                            slope s1
         |  \\_____/                                         free from min_gap to target
         +--+-----+-----------+------------+----------> gap (miles)
          min_gap target     mid          far
    """
    if gap < s.min_gap:                                  # too crowded: small penalty
        return s.crowd * (s.min_gap - gap)
    cost = 0.0
    if gap > s.target:                                   # beyond the free zone: gentle slope
        cost += s.s1 * (min(gap, s.mid) - s.target)
    if gap > s.mid:                                      # getting long: steeper
        cost += s.s2 * (min(gap, s.far) - s.mid)
    if gap > s.far:                                      # very long: steepest
        cost += s.s3 * (gap - s.far)
    return cost


# =====================================================================================
# 5. THE SEARCH
# =====================================================================================
# We search over "paths" START -> (some sources) -> END, in increasing mile order.
#
# Why dynamic programming? The cost of adding a waypoint depends on the two waypoints
# before it (the "what if it's dry?" term looks at both neighbors). So we remember, for
# every pair (i, j) of consecutive waypoints, the cheapest way to get there:
#
#       best[(i, j)] = cheapest cost of any path that starts at START and whose
#                      last two points are i then j.
#
# To extend by one more point k, we compute the new cost of (j, k) from (i, j):
#
#       best[(j, k)] = best[(i, j)]
#                    + carry(j->k)                          the new gap
#                    + cost of adding k                     wp_cost + risk + detour
#                    + (1 - reliability of j) *             "what if j is dry?"
#                         ( carry(i->k) - carry(i->j) - carry(j->k) )
#
# and keep the cheapest `i` for each (j, k). A worked example with START at mile 0, j at 6, k at 12:
# if j is dry you carry 12 miles instead of 6 + 6, so the extra carry penalty is
# carry(12) - 2 * carry(6), and it is multiplied by how likely j is to be dry.
# Because we keep the best for every (j, k), the result is the exact minimum, not an approximation.
@dataclass
class Node:
    """A point on the search path: the START, the END, or a candidate source."""
    mile: float
    reliability: float
    off_route: float
    name: str
    source: Source = None


def build_nodes(data: RouteData, s: Settings):
    """[START] + candidate sources (sorted by mile) + [END]."""
    candidates = sorted((src for src in data.sources if src.off_route <= s.off_max and src.conf >= s.min_conf),
                        key=lambda src: src.mile)
    if not candidates:
        sys.exit("no candidate sources: widen --off-max or lower --min-conf")
    return ([Node(0.0, 1.0, 0.0, "START")]
            + [Node(c.mile, reliability(data, c, s), c.off_route, c.name, c) for c in candidates]
            + [Node(data.total, 1.0, 0.0, "END")])


def allowed_hops(nodes, s: Settings):
    """For each node, which later nodes may come directly after it?

    Normally only hops of at most `gmax` miles. But if a stretch is so dry that fewer than four
    sources fall inside that range, we always allow the next four nodes however far they are, so
    a path always exists. We also always allow jumping straight to the END when it is within 2*gmax.
    """
    last = len(nodes) - 1
    miles = [n.mile for n in nodes]
    hops = []
    for j in range(len(nodes)):
        nxt = [k for k in range(j + 1, len(nodes)) if miles[k] - miles[j] <= s.gmax]
        if len(nxt) < 4:
            nxt = list(range(j + 1, min(len(nodes), j + 5)))
        if j < last and last not in nxt and miles[last] - miles[j] <= s.gmax * 2:
            nxt.append(last)
        hops.append(sorted(set(nxt)))
    return hops


def solve(nodes, s: Settings):
    """Run the dynamic program. Returns (chosen nodes, total cost)."""
    n = len(nodes)
    END = n - 1
    miles = np.array([x.mile for x in nodes])
    rel = np.array([x.reliability for x in nodes])
    off = np.array([x.off_route for x in nodes])
    carry = lambda gap: carry_cost(gap, s)

    def cost_of_adding(k):
        """Fixed price of choosing node k as a waypoint (the END is free)."""
        if k == END:
            return 0.0
        return s.wp_cost + s.risk * (1 - rel[k]) + s.off_pen * off[k]

    hops = allowed_hops(nodes, s)
    best = {}                           # (i, j) -> cheapest cost of a path ending ... i, j
    came_from = {}                      # (i, j) -> the previous state (h, i), or None at the start
    starts_before = [[] for _ in range(n)]   # starts_before[j] = every i that has a state (i, j)

    # Paths that have only START and one more point: state (START, k).
    for k in hops[0]:
        best[(0, k)] = carry(miles[k] - miles[0]) + cost_of_adding(k)
        came_from[(0, k)] = None
        starts_before[k].append(0)

    # Extend states left to right. A state (i, j) is final once every earlier node has been processed.
    for j in range(1, END):
        for i in list(starts_before[j]):
            so_far = best[(i, j)]
            gap_ij = miles[j] - miles[i]
            for k in hops[j]:
                gap_jk = miles[k] - miles[j]
                gap_ik = miles[k] - miles[i]
                dry_penalty = (1 - rel[j]) * (carry(gap_ik) - carry(gap_ij) - carry(gap_jk))
                total = so_far + carry(gap_jk) + cost_of_adding(k) + dry_penalty
                if total < best.get((j, k), math.inf):
                    if (j, k) not in best:
                        starts_before[k].append(j)
                    best[(j, k)] = total
                    came_from[(j, k)] = (i, j)

    # The answer is the cheapest state whose last point is the END.
    end_states = [state for state in best if state[1] == END]
    if not end_states:
        sys.exit("no feasible path to the end of the route: raise --gmax or --off-max")
    final = min(end_states, key=lambda state: best[state])

    # Walk backwards through came_from to recover the path.
    path = []
    state = final
    while state is not None:
        path.append(state[1])
        previous = came_from[state]
        if previous is None:
            path.append(state[0])        # the START
            break
        state = previous
    path.reverse()
    chosen = [nodes[i] for i in path if 0 < i < END]   # drop START and END
    return chosen, best[final]


def optimize(data: RouteData, settings: Settings = None):
    """Pick the waypoints. Returns (chosen nodes, all nodes, settings, total cost)."""
    s = settings or Settings()
    nodes = build_nodes(data, s)
    chosen, cost = solve(nodes, s)
    return chosen, nodes, s, cost


def simulate(chosen_miles, chosen_reliability, total, runs=4000, seed=0):
    """Monte Carlo sanity check. In each run every chosen waypoint independently has water with
    probability = its reliability. Returns, per run, the route miles spent in carries longer than
    10 miles. The mean of this is a rough measure of how badly dry sources could hurt you."""
    rng = np.random.default_rng(seed)
    miles = np.r_[0.0, chosen_miles, total]
    rel = np.r_[1.0, chosen_reliability, 1.0]
    miles_in_long_carries = np.zeros(runs)
    for run in range(runs):
        has_water = rng.random(len(rel)) < rel
        has_water[0] = has_water[-1] = True                     # start and finish always count
        gaps = np.diff(miles[has_water])
        miles_in_long_carries[run] = gaps[gaps > 10].sum()
    return miles_in_long_carries


# =====================================================================================
# 6. WRITING RESULTS
# =====================================================================================
def nearest_point_on_route(route, lat, lon):
    """Closest point on the route line to (lat, lon). Returns (distance in metres, lat, lon).

    Works in a flat local approximation (fine at trail scale): longitude is shrunk by cos(latitude).
    We first find the nearest vertex, then check the two segments touching it for a closer point."""
    cos_lat = math.cos(math.radians(lat))
    metres_x = lambda lons: (lons - lon) * cos_lat * 111320
    metres_y = lambda lats: (lats - lat) * 110574
    dist = np.hypot(metres_x(route[:, 1]), metres_y(route[:, 0]))
    k = int(dist.argmin())
    best = (float(dist[k]), route[k, 0], route[k, 1])
    for a, b in ((k - 1, k), (k, k + 1)):                      # the two segments around vertex k
        if a < 0 or b >= len(route):
            continue
        ax, ay = metres_x(route[a, 1]), metres_y(route[a, 0])
        bx, by = metres_x(route[b, 1]), metres_y(route[b, 0])
        vx, vy = bx - ax, by - ay
        length2 = vx * vx + vy * vy
        t = 0 if length2 == 0 else max(0, min(1, -(ax * vx + ay * vy) / length2))   # how far along a->b
        d = math.hypot(ax + t * vx, ay + t * vy)
        if d < best[0]:
            best = (d, route[a, 0] + t * (route[b, 0] - route[a, 0]), route[a, 1] + t * (route[b, 1] - route[a, 1]))
    return best


def date_label(day_of_year, year=2026):
    return (datetime.date(year, 1, 1) + datetime.timedelta(days=int(round(day_of_year)) - 1)).strftime("%b %d").replace(" 0", " ")


def xml_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_rows(data: RouteData, chosen, s: Settings):
    """One dictionary per chosen waypoint, with the name and description that go on the watch."""
    rows = []
    for k, node in enumerate(chosen):
        src, rel = node.source, node.reliability
        planned = data.arrival_day(src.mile, s)
        p_flowing = data.value_on(src.curves[GOOD_CURVE], planned)
        _, route_lat, route_lon = nearest_point_on_route(data.route, src.lat, src.lon)

        # Tier prefix on the name: none = reliable, "~" = fair, "?" = best of a bad stretch.
        tier = "" if rel >= 0.85 else ("~" if rel >= 0.6 else "?")
        next_mile = chosen[k + 1].mile if k + 1 < len(chosen) else data.total
        miles_to_next = next_mile - src.mile

        # Watch course-point names are limited to 15 characters.
        name = f"{tier}{src.mile:.0f} {src.name}"[:15].rstrip()
        description = (f"Route mi {src.mile:.1f} | water {rel * 100:.0f}% low-end, flowing {p_flowing * 100:.0f}% typical"
                       f" | ~{date_label(planned)} | "
                       + (f"{src.elev_ft:,} ft | " if src.elev_ft else "")
                       + f"next water {miles_to_next:.1f} mi"
                       + (f" | {src.off_route:.2f} mi off route" if src.off_route > 0.1 else "")
                       + (" | varies a lot year to year" if src.volatile else ""))[:255]
        rows.append(dict(order=k + 1, mile=round(src.mile, 1), name=name, source=src.name,
                         lat=route_lat, lon=route_lon, elev_ft=src.elev_ft,
                         reliability=round(rel, 3), p_flowing=round(p_flowing, 3), tier=tier or "A",
                         date=date_label(planned), gap_next=round(miles_to_next, 1),
                         volatile=bool(src.volatile), off_mi=src.off_route, desc=description))
    return rows


def waypoints_xml(rows):
    """The <wpt> elements. Each waypoint is written ON the route, so the splitter's corridor check never drops it."""
    out = ""
    for r in rows:
        ele = f'<ele>{r["elev_ft"] * 0.3048:.1f}</ele>' if r["elev_ft"] else ""
        out += (f'  <wpt lat="{r["lat"]:.7f}" lon="{r["lon"]:.7f}">{ele}<name>{xml_escape(r["name"])}</name>'
                f'<desc>{xml_escape(r["desc"])}</desc><sym>Water</sym><type>Water</type></wpt>\n')
    return out


def write_outputs(data: RouteData, rows, out_dir, title):
    os.makedirs(out_dir, exist_ok=True)
    header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<gpx version="1.1" creator="trail-water-explorer optimizer" xmlns="http://www.topografix.com/GPX/1/1">\n'
              f'  <metadata><name>{xml_escape(title)}</name></metadata>\n')
    wpts = waypoints_xml(rows)

    # 1. just the waypoints
    open(os.path.join(out_dir, "water-waypoints.gpx"), "w").write(f"{header}{wpts}</gpx>\n")

    # 2. the route plus the waypoints (load this one in the splitter)
    trackpoints = "".join(f'    <trkpt lat="{la:.6f}" lon="{lo:.6f}"/>\n' for la, lo in data.route)
    open(os.path.join(out_dir, "route-with-water.gpx"), "w").write(
        f"{header}{wpts}  <trk><name>{xml_escape(title)}</name><trkseg>\n{trackpoints}  </trkseg></trk>\n</gpx>\n")

    # 3. a spreadsheet of everything
    with open(os.path.join(out_dir, "water-waypoints.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["order", "route_mile", "name_on_watch", "source_name", "lat_on_route", "lon_on_route", "elev_ft",
                         "chance_of_water_low_end", "chance_flowing_or_better_typical", "tier(A>=85%,~60-85%,?<60%)",
                         "est_date", "miles_to_next_water", "volatile", "off_route_mi", "description"])
        for r in rows:
            writer.writerow([r["order"], r["mile"], r["name"], r["source"], f'{r["lat"]:.7f}', f'{r["lon"]:.7f}', r["elev_ft"],
                             r["reliability"], r["p_flowing"], r["tier"], r["date"], r["gap_next"],
                             int(r["volatile"]), r["off_mi"], r["desc"]])


def summarize(data: RouteData, chosen):
    """One-line summary: how many waypoints, how long the gaps are, how reliable the sources are."""
    miles = np.array([x.mile for x in chosen])
    rel = np.array([x.reliability for x in chosen])
    gaps = np.diff(np.r_[0, miles, data.total])
    return (f"{len(chosen)} waypoints ({len(chosen) / data.total * 100:.1f} per 100 mi) | planned gaps: "
            f"median {np.median(gaps):.1f} mi, <=5 mi {np.mean(gaps <= 5):.0%}, >10 mi {np.mean(gaps > 10):.0%}, "
            f"longest {gaps.max():.0f} mi | reliability: mean {rel.mean():.2f}, <0.7 {np.mean(rel < .7):.0%}")


# =====================================================================================
# 7. COMMAND LINE
# =====================================================================================
def settings_from_args(args):
    """Build a Settings, overriding only the defaults the user changed."""
    changed = {f.name: getattr(args, f.name) for f in fields(Settings) if getattr(args, f.name) is not None}
    if changed.get("kind", Settings.kind) not in KIND_TO_CURVE:
        sys.exit("--kind must be any, usable or good")
    return replace(Settings(), **changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=DEFAULT_DATA, help="app data.json (default: the template data)")
    parser.add_argument("--out", default=os.path.join(HERE, "example-output"), help="output folder")
    for f in fields(Settings):                                   # one flag per setting
        parser.add_argument("--" + f.name.replace("_", "-"), type=type(f.default), default=None,
                            help=f'{f.metadata["help"]} (default {f.default})')
    args = parser.parse_args()

    settings = settings_from_args(args)
    data = RouteData(args.data)
    chosen, _, settings, cost = optimize(data, settings)
    rows = build_rows(data, chosen, settings)
    write_outputs(data, rows, args.out, "Water waypoints" + (" (template data)" if data.template else ""))

    print(summarize(data, chosen) + f" | total cost {cost:.0f}")
    long_carry_miles = simulate([x.mile for x in chosen], [x.reliability for x in chosen], data.total)
    print("if each chosen source is independently dry with probability 1 - reliability: "
          f"mean route miles in carries over 10 mi = {long_carry_miles.mean():.0f}")
    print("wrote", os.path.relpath(args.out))


if __name__ == "__main__":
    main()
