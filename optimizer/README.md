# Water waypoint optimizer

Choosing which water sources to put on a watch is a trade-off. Every source is another point to read, and too few leaves you carrying water for 20 miles. This script picks a small set of waypoints with high chance of water, short carries, and few points.

It reads the same `data.json` as the offline app, so anything that produces that file (see [`../docs/DATA_FORMAT.md`](../docs/DATA_FORMAT.md)) can feed it. By default it runs on the synthetic template data.

```bash
pip install numpy
python3 optimizer/waypoint_optimizer.py                          # default weights
python3 optimizer/waypoint_optimizer.py --wp-cost 2 --risk 8     # sparser and more careful
python3 optimizer/waypoint_optimizer.py --data my/data.json --kind any --start 84 --days 50
python3 optimizer/compare_strategies.py                          # DP vs naive baselines
```

## Output

Written to `optimizer/example-output/` (or `--out`):

| File | What |
|:-|:-|
| `water-waypoints.gpx` | One `<wpt>` per chosen source, placed on the route. Names are 15 characters or fewer, the limit for watch course points |
| `water-waypoints.csv` | The same rows with every field |
| `route-with-water.gpx` | The route track plus those waypoints. Load it in the [GPX course splitter](https://github.com/JKesslerPhD/gpx-course-splitter) and every waypoint snaps with the default 100 m corridor |

The checked-in example output is made from the template data, so it describes a made-up route.

## What a name and description mean

- Name: `[tier]<route mile> <source name>`, for example `26 Sample Troug`.
- Tier prefix: none means a low-end water chance of 85% or better, `~` means 60 to 85%, and `?` means under 60%. A `?` waypoint is the best option in a stretch with nothing better, so read it as "may be dry".
- Description: "water X% low-end" is the chance of at least the chosen quantity at the worst arrival date within plus or minus 3 weeks of plan. "flowing Y% typical" is the chance of flowing or better on the planned date. Volatile sources use the low end of their range.

## Method

For a candidate set of waypoints, the cost (in "mile-equivalents") is:

```
  sum over consecutive gaps g of carry(g)
+ wp_cost for each chosen point
+ off_pen * miles the source sits off the route
+ risk * (1 - reliability) for each chosen point
+ for each chosen point j between i and k:
      (1 - reliability_j) * ( carry(gap i->k) - carry(gap i->j) - carry(gap j->k) )
```

The last term is the first-order cost of j being dry. You then carry the whole i-to-k gap instead of two shorter ones. `carry(g)` is flat between `min_gap` and `target`, rises gently to `mid`, faster to `far`, and steeply after that. Gaps under `min_gap` pay a small crowding penalty.

The minimum is found exactly by dynamic programming over pairs (previous point, current point). The pair state is what lets the dry-source term look at both neighbors.

**Reliability** of a source is the worst of its chance of water over arrival dates within `--half` days (default 21) of the planned date. The planned date comes from a constant pace over the whole route (`--start` is the start day of year and `--days` the length of the trip). `--kind` sets what counts as water:

| `--kind` | Counts as water |
|:-|:-|
| `any` | quantity 1 or more (pools and tanks count) |
| `usable` (default) | 2 or more (a trickle) |
| `good` | 3 or more (flowing) |

## Parameters

Every default lives in `DEFAULTS` at the top of `waypoint_optimizer.py` and is a command-line flag (`--wp-cost`, `--risk`, `--off-pen`, `--target`, `--mid`, `--far`, `--off-max`, `--min-conf`, ...).

## Limits

- Reliability treats sources as independent. Real droughts are correlated, so a dry year hurts more than the simulation at the end of each run shows.
- Pace is a single constant. A real schedule has zero days and long days.
- It optimizes one direction of travel. For the other direction, run again with the schedule reversed.
- Carries can stay long even with every known source. That is the water, not the optimizer.
