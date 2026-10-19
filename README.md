# Market Making Simulator

This is an event-driven Avellaneda–Stoikov market-making backtest on CME E-mini S&P 500 (ES) futures. It runs on an L3 order book reconstructed from Databento market-by-order (MBO) data. It is tested on 34 sessions: 21 in Jan–Feb 2025 and 13 in Aug–Sep 2026.

**Result: none of the fair values makes money.** I tested the mid, the weighted mid and the Stoikov micro-price. Every strategy loses about 31–32 index points a day after fees (about $1,600/day, t ≈ −3.0). The quotes earn about 0.08 pt of spread per fill, but the price moves about 0.12 pt against them within a minute. That is adverse selection.

## Results (after fees)

| Fair value | 2025 (21 days) | 2026 (13 days) | All 34 days | t (all) |
|---|---|---|---|---|
| Mid | −44.3 | −12.5 | −32.2 | −3.03 |
| Weighted mid | −41.8 | −13.1 | −30.8 | −3.07 |
| Micro-price | −43.1 | −12.9 | −31.6 | −3.04 |

- **Units:** index points per day for a 1-lot strategy; multiply by $50 for dollars.
- **Before fees:** −17 to −19 pt/day (t ≈ −2).
- **The three fair values are statistically indistinguishable.**

## How it works

- **Order book:** `orderbook.py` replays MBO messages into a book with price-time priority. <!-- RECON -->
- **Fair values:** the mid, the weighted mid, and the Stoikov (2018) micro-price, estimated on the previous session.
- **Quoting:** Avellaneda–Stoikov reservation price and spread, with σ in index points, γ = 1e-4 and k = 1.549. One lot each side, requoted every second from 09:30 to 16:00 ET, and flat at the close.
- **Fills:** queue position is tracked order by order from the MBO stream. A quote fills when the volume traded at or through it exceeds the queue ahead. Fees are 0.024 pt per contract per side, about $1.20.

## What I got wrong first

An earlier version of this project reported a profitable micro-price strategy. It was wrong, for five reasons:

1. **Estimator bug.** `np.resize` silently scrambled the micro-price transition matrix and biased it upward, so the strategy kept buying. Fixing this alone turned +16,079 pt into −2,156 pt on the test day.
2. **Holding after the close.** 77% of the old micro-price PnL came from inventory held from 16:00 to 19:00 while the strategy wasn't quoting.
3. **Queue double count.** Traded volume left the queue twice, which overstated the spread earned by about 60%.
4. **Inventory control switched off.** σ was in log-return units, which made the A-S inventory skew about zero, so inventory drifted to the caps.
5. **One session.** The headline came from a single trending day. Over 21 days, even the buggy version wasn't significant (t = 0.73).

The analysis behind these numbers is in `diagnostics/`, with write-ups in `diagnostics/notes/`. The pre-fix code is in `legacy/`.

## Running

```bash
python es_mbo_to_parquet.py        # MBO -> features/<date>.parquet
python as.py                       # backtest -> backtest_daily_results.csv, backtest_summary.csv (~23 min)
python diagnostics/as_results.py   # per-strategy t-stats
```

The data (Databento GLBX.MDP3 MBO) isn't included.

## Limitations

- **No latency:** orders are live the instant they are placed.
- **The 2026 books are unvalidated:** there is no mbp-1 data for those sessions, and they are built from the most-traded contract each day.
- **Old estimators remain:** `fvest.py` and `mm_sim.py` still contain the broken micro-price estimators, but `as.py` doesn't use them.

## Attribution

The order book, the analysis and the backtest are mine: `orderbook.py`/`mm_sim.py`, `es_mbo_to_parquet.py`, `analysis.py` and `as.py`.

The following were written with AI assistance:
- the mbp-1 validation harness (`recon_*.py`) and `fetch_mbp1.py`
- the plotting helpers
- the 2026 data scripts (`split_es_cl_mbo.py`, `es_front_month.py`)
- everything in `diagnostics/`
- the October 2026 fixes to `as.py`
