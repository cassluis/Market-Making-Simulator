# Diagnostics

These are the tools and results used to find out why the original backtest looked profitable, and to test the fixed version.

The central tool is `as_diag.py`, an instrumented replica of the pre-fix `as.py`.
- **Exact match:** with every fix switched off, it reproduces the pre-fix `as.py` exactly on all 18 strategies.
- **Switchable fixes:** each fix can be turned on separately, so its effect can be measured.
- **PnL breakdown:** it splits each strategy's PnL exactly into four parts:
  - spread earned against the mid
  - inventory carry before 16:00
  - inventory carry after 16:00
  - the cost of exiting

## Scripts

| Script | What it does |
|---|---|
| `as_diag.py` | Replays one day through every variant in a single pass. `--cal`/`--date` pick the calibration and test days, `--original` runs `as.run_backtest` itself, and `--inv` adds the inventory-control variants |
| `multiday.py` | Pools `as_diag.py`'s daily outputs over the 21 sessions of 2025: daily PnL t-stats, and per-fill markouts with day-clustered SEs |
| `inv_multiday.py` | Does the same for the `--inv` inventory-control sweep |
| `mirror_test.py` | Mirror test: reflects the tape (p → C − p, bid ↔ ask) and checks that the simulator's output flips exactly |
| `market_asym.py` | Simulator-free check of how often the tape reaches the bid level versus the ask level |
| `microprice_horizons_fixed.py` | Out-of-sample forecasting study of mid vs weighted mid vs micro-price, using the corrected estimator |
| `as_results.py` | Per-strategy statistics from `as.py`'s `backtest_daily_results.csv` |
| `plot_inventory.py` | Charts of price against each strategy's inventory through one day |

The `as_diag.py` variants:

| Config | Fix |
|---|---|
| `base` | `as.py` as written before the fixes |
| `a` | traded volume is removed from the queue once (on the cancel/modify), not twice |
| `b` | an order ahead that grows loses priority; the queue can't go negative |
| `c` | quotes stop and inventory is flattened at the last regular-hours sample |
| `d` | the micro-price uses a correctly shaped estimator |
| `e` | the micro-price is applied causally (calibration-day buckets, no future-move filter) |
| `f` | a quote that crosses the market fills as a taker |
| `all` | all of the above |

## Write-ups

| Note | Question |
|---|---|
| [`multiday-sweep-results.md`](notes/multiday-sweep-results.md) | Once the bugs are fixed, does any fair value have an edge over 21 sessions? |
| [`long-bias-diagnosis.md`](notes/long-bias-diagnosis.md) | Why does every strategy end the day long, and is it a simulator bug? |
| [`inventory-control-sweep.md`](notes/inventory-control-sweep.md) | Does working A-S inventory control rescue the strategy? |
| [`microprice-horizons-fixed.md`](notes/microprice-horizons-fixed.md) | Does a correctly estimated micro-price forecast better than the mid or the weighted mid? |

## Outputs

`diag_out/` holds the per-day summaries (`summary_<date>.csv`, `inv_summary_<date>.csv`) and the pooled tables and charts behind the write-ups.

The per-fill (`fills_*.parquet`) and per-sample (`paths_*.npz`) files aren't committed because they're large. Re-running the scripts regenerates them.

The scripts read the data from `ROOT = '/Volumes/T9/Market_making'`, so change this to match your setup. All PnL is in ES index points; multiply by $50 for dollars.
