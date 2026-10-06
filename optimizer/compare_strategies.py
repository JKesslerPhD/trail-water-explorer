#!/usr/bin/env python3
"""Compare waypoint-selection strategies on the same data.

    python3 optimizer/compare_strategies.py [--data path/to/data.json]

Rows: every candidate source (reference), a naive "best source in each 4-mile cell", and the
dynamic-programming optimizer at several weights. For each it prints how many waypoints it uses,
the planned gaps, the mean reliability of the chosen sources, and, when each chosen source is
independently dry with probability 1 - reliability, the expected route miles spent in carries over 10 mi.
"""
import argparse, os
import numpy as np
from waypoint_optimizer import Data, DEFAULTS, DEFAULT_DATA, optimize, simulate


def summarize(label, data, miles, rel, nsim=3000):
    order = np.argsort(miles); mm = np.array(miles)[order]; rr = np.array(rel)[order]
    gaps = np.diff(np.r_[0, mm, data.total])
    over10 = simulate(mm, rr, data.total, nsim)
    print(f"{label:34} {len(mm):4d} wpts ({len(mm) / data.total * 100:4.1f}/100mi) | gap median {np.median(gaps):4.1f} "
          f"<=5mi {np.mean(gaps <= 5):4.0%} >10mi {np.mean(gaps > 10):4.0%} | reliability mean {rr.mean():.2f} "
          f"<0.7 {np.mean(rr < .7):4.0%} | miles in >10-mi carries: planned {gaps[gaps > 10].sum():4.0f}, expected {over10.mean():5.0f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=DEFAULT_DATA)
    a = ap.parse_args()
    data = Data(a.data); P = dict(DEFAULTS)
    cand = sorted((s for s in data.src if s["off"] <= P["off_max"]), key=lambda s: s["mile"])
    summarize("every source (reference)", data, [s["mile"] for s in cand], [data.reliability(s, P) for s in cand])
    nm, nr = [], []
    for b in np.arange(0, data.total, 4.0):
        w = [(data.reliability(s, P), s["mile"]) for s in cand if b <= s["mile"] < b + 4.0]
        if w:
            r_, m_ = max(w); nm.append(m_); nr.append(r_)
    summarize("naive: best source per 4-mile cell", data, nm, nr)
    for wp_cost, risk in ((1.2, 0), (1.2, 4), (1.2, 8), (1.2, 15), (2.0, 8), (2.0, 15), (3.0, 15)):
        sel, _, _, _ = optimize(data, dict(wp_cost=wp_cost, risk=risk))
        summarize(f"DP wp_cost={wp_cost} risk={risk}", data, [x["mile"] for x in sel], [x["r"] for x in sel])


if __name__ == "__main__":
    main()
