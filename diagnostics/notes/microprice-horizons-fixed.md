# Micro-price vs mid vs weighted mid, corrected estimator (run 2026-11-22)

- Script: `diagnostics/microprice_horizons_fixed.py`, run with `python3 -B` from /private/tmp. It took 289 s.
- Outputs:
  - `diag_out/microprice_horizons_fixed.csv`: summary per (scope, horizon)
  - `diag_out/microprice_horizons_fixed_daily.csv`: per day
- The old `microprice_horizons.py` / `_ns4.py` results and `results/microprice_*.csv` are invalid. They used the `np.resize` estimators that scramble R1/R2.

## Design
- **Estimator:** `as.estimate_G6`, applied as in `as.main()`:
  - imbalance bins from `fvest.get_imbalance_bins` on the calibration day, 10 buckets
  - G6 with 6 terms
  - `mp = mid + G6[bucket]` on one-tick spreads, `mp = mid` elsewhere
- **Sample:** out of sample. Calibrate on day d, test on day d+1, using as.py's 21 pairs from 2025 (test days 20250107–20250206). The original fitted and scored on the same day.
- **Target:** the mid h book events ahead in the raw RTH event stream.
  - h ∈ {1, 5, 10, 25, 50, 100, 250, 500, 1000}, the same horizons as the original.
  - The original shifted within a filtered table. Its rows had to pass a filter on the next row's move, which looks ahead.
- **Primary scope:** rows whose current spread is one tick, as in the original (N_SPREAD = 1). There are about 167M rows per horizon, and 92–98% of RTH rows qualify.
- **Secondary scope:** `all_rows`, which scores every RTH row.
- **Statistics:**
  - MSE is the mean of the daily MSEs.
  - "Days beats" counts days where micro has the lower MSE.
  - t is the day-level paired t on the MSE difference (micro − baseline), with n = 21.
- **G6:** the maximum |G6| is 0.58–0.72 of the half-tick across calibration days. Stoikov reports about 0.8 for BAC.

## Results: one-tick rows (primary)
| h (events) | median / mean ms | MSE mid | MSE wmid | MSE micro | micro/mid | micro/wmid | days micro<mid | days micro<wmid | t vs mid | t vs wmid |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.07 / 3 | .000357 | .004284 | .003200 | 8.97 | 0.747 | 0/21 | 21/21 | +41.8 | −13.3 |
| 5 | 0.9 / 16 | .003158 | .004958 | .004304 | 1.363 | 0.868 | 0/21 | 21/21 | +13.5 | −10.9 |
| 10 | 7 / 32 | .006516 | .006328 | .005983 | 0.918 | 0.945 | 16/21 | 21/21 | −4.3 | −9.1 |
| 25 | 32 / 79 | .013608 | .010549 | .010539 | 0.774 | 0.999 | 21/21 | 13/21 | −21.6 | −0.8 |
| 50 | 85 / 157 | .020299 | .016280 | .016386 | 0.807 | 1.0065 | 21/21 | 0/21 | −47.6 | +8.3 |
| 100 | 193 / 311 | .030327 | .026254 | .026393 | 0.870 | 1.0053 | 21/21 | 0/21 | −68.2 | +8.8 |
| 250 | 532 / 774 | .059364 | .055265 | .055408 | 0.933 | 1.0026 | 21/21 | 0/21 | −64.6 | +8.6 |
| 500 | 1099 / 1547 | .1075 | .10332 | .10348 | 0.963 | 1.0015 | 21/21 | 0/21 | −64.6 | +8.0 |
| 1000 | 2261 / 3091 | .20351 | .19930 | .19946 | 0.980 | 1.0008 | 21/21 | 0/21 | −60.6 | +7.0 |

MSE is in pt². The ms columns give the median and the mean wall-clock time of h events. Events are bursty, so the median is far below the mean at short h.

Weighted mid vs mid, for reference: wmid/mid is 12.0, 1.57, 0.971, 0.775, 0.802, 0.866, 0.931, 0.961, 0.979.

## Robustness
- **MAE:** micro beats wmid at every horizon on one-tick rows.
  - h = 1–10: 20–21/21 days, t −5.5 to −8.8.
  - h ≥ 25: 13–16/21 days, t −2.3 to −3.9.
  - So micro's small MSE loss at long h comes from a few large misses. G6 is shrunk relative to wmid's full ±half-tick swing.
- **All rows:** micro ≤ wmid at every horizon.
  - Significant at h ≤ 25: ratio 0.73 / 0.84 / 0.92 / 0.98, with t −17 / −16 / −15 / −6.8.
  - A tie from 50 events on: ratio 0.9965–0.9998, |t| ≤ 1.8, 8–11/21 days.
  - On the wider spreads micro equals mid, and wmid does worse there.

## Verdict
The corrected micro-price beats the plain mid at 7 of 9 horizons, every one from 10 events (median about 7 ms) up.
- It wins on 16/21 days at 10 events and on 21/21 days from 25 events on.
- The MSE cut is 8% at 10 events, peaks at 23% at 25 events (about 32 ms median, t −21.6), then shrinks to 13% at 100 events (about 0.2 s) and 2% at 1000 events (about 2.3 s).

At 1 and 5 events (under 1 ms median) the plain mid wins. The future mid almost never moves on that timescale, so any adjustment adds error.

Against the imbalance-weighted mid, the micro-price wins only at the three shortest horizons:
- 25% lower MSE at 1 event, 13% at 5 and 5.5% at 10, on 21/21 days each.
- It is tied at 25 events: ratio 0.999, 13/21 days, t −0.8.
- From 50 events (about 85 ms) on it is marginally worse: MSE 0.08–0.65% higher, 0/21 days, t +7 to +9.

The weighted mid itself gets almost the same gains over the mid (wmid/mid is 0.775 at 25 events). So once forecasts reach tens of milliseconds, the micro-price adds nothing beyond imbalance. Its only edge is at horizons shorter than a few milliseconds, where both lose to the plain mid anyway.

## Essay-ready wording
"Across 21 out-of-sample ES sessions (calibrate on one day, test on the next) and nine horizons, a correctly estimated Stoikov micro-price cut the mid's squared forecast error by up to 23% (at ~30 ms), but an imbalance-weighted mid did almost exactly as well: the micro-price beat it only at the three shortest horizons (under ~10 ms), tied at ~30 ms, and was marginally worse (by <1% MSE) from ~85 ms to ~2 s."

The essay's old wording, "23 sessions … did not beat a simple imbalance-weighted mid", came from the broken, in-sample run. The corrected numbers are 21 sessions, out of sample, and the claim holds only from about 30 ms up.
