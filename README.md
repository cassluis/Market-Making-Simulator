# Market Making Simulator

This is an event-driven Avellaneda–Stoikov market-making backtest on CME E-mini S&P 500 (ES) futures. It runs on an L3 order book reconstructed from Databento market-by-order (MBO) data.

The backtest covers 34 sessions: 21 in Jan–Feb 2025 (ESH5) and 13 in Aug–Sep 2026 (the front month each day).

**Result: none of the three fair values makes money.** I tested the mid, the imbalance-weighted mid and the Stoikov micro-price.
- Every strategy loses about 31–32 index points a day after fees, about $1,600/day (t ≈ −3.0).
- The quotes earn about 0.08 pt of spread per fill, but the price moves about 0.12 pt against them within a minute.
- So the losses come from adverse selection, not just from fees.

## Results

Net PnL after fees, in index points per day for a 1-lot strategy (×$50 for dollars):

| Fair value | 2025 (21 days) | 2026 (13 days) | All 34 days | t (all) |
|---|---|---|---|---|
| Mid | −44.3 | −12.5 | −32.2 | −3.03 |
| Weighted mid | −41.8 | −13.1 | −30.8 | −3.07 |
| Micro-price | −43.1 | −12.9 | −31.6 | −3.04 |

- **Before fees:** −17 to −19 pt/day (t ≈ −2).
- **Fair values:** the three are statistically indistinguishable. They differ by at most 1.4 pt/day, against a standard error of about 10.
- **By year:** 2025 is clearly negative on its own (t ≈ −3.1). 2026 is not significant on its own (t ≈ −0.9).
- **Inventory caps:** caps from 25 to 500 contracts give identical results, because inventory stays near zero.

## Fair-value forecasting

This compares how well each fair value predicts the future mid. Each one is fitted on one day and tested on the next, across 21 days. The table shows mean squared error relative to the plain mid and to the weighted mid.

| Horizon (book events) | Typical time | Micro vs mid | Micro vs weighted mid |
|---|---|---|---|
| 1–5 | under 1 ms | worse (the mid barely moves) | 13–25% better |
| 10 | ~7 ms | 8% better | 5.5% better |
| 25 | ~30 ms | **23% better** | tied |
| 50–1,000 | 85 ms – 2 s | 20% → 2% better | 0.1–0.7% worse, on every day |

What this shows:
- **Against the plain mid,** the micro-price is clearly better from about 10 ms onwards.
- **Against the weighted mid,** it only wins at the very shortest horizons. Beyond a few milliseconds it is no better.
- **Why:** ES almost always trades with a one-tick spread, so queue imbalance already carries most of the information. The weighted mid captures it without a model.

The full results are in [`diagnostics/notes/microprice-horizons-fixed.md`](diagnostics/notes/microprice-horizons-fixed.md).

## How it works

- **Order book (`orderbook.py`):** replays MBO messages into a book with price-time priority. Each order's place in its queue is tracked.
- **Validation (`recon_check.py`):** the book is checked against Databento's mbp-1 feed at every packet end (F_LAST).
  - **Result:** 147,826,414 book states over the 27 sessions from 2025 that have both feeds, with 100% exact top of book and 0 mismatches. See `results/recon_accuracy.csv`.
  - **Join on F_LAST, not the MDP3 sequence number.** Trailing records in a packet carry the previous sequence number, so joining on sequence reports a false 0.008% error.
- **Fair values:**
  - the mid
  - the imbalance-weighted mid
  - the Stoikov (2018) micro-price, from `estimate_G6` in `as.py`, with 10 imbalance buckets, 6 terms, and fitted on the previous session
- **Quoting (`as.py`):** Avellaneda–Stoikov reservation price and spread, with σ in index points, γ = 1e-4 and k = 1.549. One lot each side, requoted every second from 09:30 to 16:00 ET, and flat at the close.
- **Fills:**
  - **Passive fill:** a quote fills when the volume traded at or through it exceeds the queue ahead of it.
  - **Priority loss:** an order ahead that increases its size moves behind us.
  - **Crossing quotes:** a quote that crosses the market fills immediately at the touch.
  - **Fees:** 0.024 pt per contract per side, about $1.20.

## What I got wrong first

An earlier version of this project reported a profitable micro-price strategy. It was wrong, for five reasons:

1. **Estimator bug.** The next state was shifted before filtering out wide spreads, so the transition table came out 10×30. `np.resize` then silently cut it to 10×10. That biased the micro-price upward, so the strategy kept buying. Fixing this alone turned +16,079 pt into −2,156 pt on the test day.
2. **Holding after the close.** 77% of the old micro-price PnL came from inventory held from 16:00 to 19:00 while the strategy wasn't quoting.
3. **Queue double count.** Traded volume was removed from the queue on the trade record and again on the cancel that follows each resting fill. That overstated the spread earned by about 60%.
4. **Inventory control switched off.** σ was in log-return units, which made the A-S inventory skew about 2e-6 pt per contract, so inventory drifted towards the caps.
5. **One session.** The headline came from a single trending day. Over 21 days, even the buggy version wasn't significant (t = 0.73).

Each fix was measured separately with an instrumented replica of the backtest, `diagnostics/as_diag.py`. The pre-fix code and results are in [`legacy/`](legacy/).

## Repository layout

| Path | What |
|---|---|
| `as.py` | the backtest: fair values, A-S quoting, queue and fill simulation, fees |
| `orderbook.py` | the L3 order book |
| `es_mbo_to_parquet.py` | replays MBO files into `features/<date>.parquet` top-of-book snapshots |
| `split_es_cl_mbo.py`, `es_front_month.py` | turn a multi-contract `ES.FUT`/`CL.FUT` download into single-contract files (the 2026 data) |
| `recon_check.py`, `recon_diagnose.py`, `recon_trace.py`, `fetch_mbp1.py` | the reconstruction check against mbp-1 |
| `results/recon_accuracy.csv` | per-session reconstruction accuracy |
| `backtest_daily_results.csv`, `backtest_summary.csv` | `as.py` output: per day and per strategy |
| `diagnostics/` | replica backtest, multi-day sweeps, mirror test, forecasting study and write-ups (see its [README](diagnostics/README.md)) |
| `legacy/` | the pre-fix backtest and its results (see its [README](legacy/README.md)) |
| `mm_sim.py`, `fvest.py`, `analysis.py` | earlier research code (see Limitations) |

## Reproducing

The data isn't included: it is Databento GLBX.MDP3 MBO, with mbp-1 for validation. The scripts expect it under `/Volumes/T9/Market_making`, so change `ROOT` and the paths in `as.py` to match your setup.

```bash
python es_mbo_to_parquet.py        # MBO -> features/<date>.parquet
python recon_check.py              # validate the book against mbp-1 (~25 min)
python as.py                       # backtest, 34 sessions (~23 min)
python diagnostics/as_results.py   # per-strategy t-stats
```

## Limitations

- **No latency:** orders are live the instant they are placed, and our own orders don't affect the replayed book.
- **Top of book only:** the validation can't confirm queue order within a price level.
- **The 2026 books are unvalidated:** there is no mbp-1 data for those sessions.
- **Old estimators remain:** `fvest.py` (`Stoikov.estimate`) and `mm_sim.py` still contain the broken micro-price estimators, and `analysis.py` relies on them. `as.py` uses neither, apart from `fvest.get_imbalance_bins`.

## Attribution

The order book, the analysis and the backtest are mine: `orderbook.py`/`mm_sim.py`, `es_mbo_to_parquet.py`, `analysis.py` and `as.py`.

The following were written with AI assistance:
- the mbp-1 validation harness (`recon_*.py`) and `fetch_mbp1.py`
- the plotting helpers
- the 2026 data scripts (`split_es_cl_mbo.py`, `es_front_month.py`)
- everything in `diagnostics/`
- the October 2026 fixes to `as.py`
