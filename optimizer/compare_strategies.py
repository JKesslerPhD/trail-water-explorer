#!/usr/bin/env python3
"""Compare ways of choosing waypoints, on the same data.

    python3 optimizer/compare_strategies.py [--data path/to/data.json]

Each row is one strategy:
  * "every source"           every candidate source (the reference: most waypoints, shortest gaps)
  * "naive"                  the single most reliable source in each 4-mile cell
  * "DP wp_cost=.. risk=.."  the dynamic-programming optimizer at several weights

Columns: how many waypoints, how long the planned gaps are, how reliable the chosen sources are, and
the miles spent in carries over 10 mi both as planned and as expected if each chosen source is
independently dry with probability 1 - reliability (a Monte Carlo estimate).
"""
import argparse
from dataclasses import replace

import numpy as np

from waypoint_optimizer import DEFAULT_DATA, RouteData, Settings, optimize, reliability, simulate


def report(label, data, miles, reliabilities, runs=3000):
    """Print one row of the comparison table."""
    order = np.argsort(miles)
    miles = np.array(miles)[order]
    rel = np.array(reliabilities)[order]
    gaps = np.diff(np.r_[0, miles, data.total])
    expected_long = simulate(miles, rel, data.total, runs).mean()
    print(f"{label:34} {len(miles):4d} wpts ({len(miles) / data.total * 100:4.1f}/100mi) | gap median {np.median(gaps):4.1f} "
          f"<=5mi {np.mean(gaps <= 5):4.0%} >10mi {np.mean(gaps > 10):4.0%} | reliability mean {rel.mean():.2f} "
          f"<0.7 {np.mean(rel < .7):4.0%} | miles in >10-mi carries: planned {gaps[gaps > 10].sum():4.0f}, expected {expected_long:5.0f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=DEFAULT_DATA)
    args = parser.parse_args()

    data = RouteData(args.data)
    base = Settings()
    candidates = sorted((s for s in data.sources if s.off_route <= base.off_max), key=lambda s: s.mile)

    # Strategy 1: take everything.
    report("every source (reference)", data, [s.mile for s in candidates], [reliability(data, s, base) for s in candidates])

    # Strategy 2: naive. Chop the route into 4-mile cells and keep the most reliable source in each.
    naive_miles, naive_rel = [], []
    for cell_start in np.arange(0, data.total, 4.0):
        in_cell = [(reliability(data, s, base), s.mile) for s in candidates if cell_start <= s.mile < cell_start + 4.0]
        if in_cell:
            best_rel, best_mile = max(in_cell)
            naive_miles.append(best_mile)
            naive_rel.append(best_rel)
    report("naive: best source per 4-mile cell", data, naive_miles, naive_rel)

    # Strategy 3: the optimizer at a range of weights.
    for wp_cost, risk in ((1.2, 0), (1.2, 4), (1.2, 8), (1.2, 15), (2.0, 8), (2.0, 15), (3.0, 15)):
        chosen, _, _, _ = optimize(data, replace(base, wp_cost=wp_cost, risk=risk))
        report(f"DP wp_cost={wp_cost} risk={risk}", data, [n.mile for n in chosen], [n.reliability for n in chosen])


if __name__ == "__main__":
    main()
