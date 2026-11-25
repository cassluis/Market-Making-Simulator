# Forecasting the mid: micro-price vs mid vs weighted mid

**Question.** Does a correctly estimated Stoikov micro-price predict the future mid better than the plain mid, or the imbalance-weighted mid?

**Answer.**
- **Against the plain mid:** yes, by up to 23% lower MSE at about 30 ms.
- **Against the weighted mid:** only at the very shortest horizons, under about 10 ms. From about 85 ms onwards the weighted mid is marginally better.

The earlier study (`microprice_horizons.py`, not in this repo) used estimators that `np.resize` scrambled, so its results are invalid. This is the corrected version, `microprice_horizons_fixed.py`.

## Method

- **Estimator:** `as.estimate_G6`, applied exactly as in `as.py`.
  - imbalance buckets from the calibration day (10 buckets)
  - G\* with 6 terms
  - micro-price = mid + G\*(bucket) on one-tick spreads, and = mid elsewhere
- **Out of sample:** calibrated on day d and tested on day d+1, over the 21 test sessions of 2025.
- **Target:** the mid h book events ahead, with h ∈ {1, 5, 10, 25, 50, 100, 250, 500, 1000}.
- **Rows scored:** regular-hours rows with a one-tick spread, about 167M per horizon. A second scope scores every regular-hours row.
- **Statistics:**
  - MSE is the mean of the daily MSEs.
  - "Days" counts the days the micro-price has the lower MSE.
  - t is a day-level paired t on the MSE difference (n = 21).
- **Size of G\*:** the maximum |G\*| is 0.58–0.72 of a half-tick across calibration days. Stoikov reports about 0.8 for BAC.

## Results (one-tick rows)

| h (events) | Median / mean ms | MSE mid | MSE wmid | MSE micro | Micro/mid | Micro/wmid | Days micro < mid | Days micro < wmid | t vs mid | t vs wmid |
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

MSE is in pt². Events arrive in bursts, so the median time is far below the mean at short horizons. For reference, the weighted mid's MSE relative to the mid is 12.0, 1.57, 0.971, 0.775, 0.802, 0.866, 0.931, 0.961 and 0.979 across the same horizons.

## Robustness

- **MAE instead of MSE:** the micro-price beats the weighted mid at every horizon.
  - 1–10 events: 20–21 of 21 days, t −5.5 to −8.8.
  - 25 events and beyond: 13–16 of 21 days, t −2.3 to −3.9.
  - So its small MSE loss at long horizons comes from a few large misses: G\* is shrunk relative to the weighted mid's full half-tick swing.
- **All rows:** the micro-price is at least as good as the weighted mid at every horizon.
  - Significantly better up to 25 events.
  - Tied from 50 events on (ratio 0.9965–0.9998, |t| ≤ 1.8).

## Conclusion

The micro-price beats the plain mid at every horizon from 10 events (about 7 ms) upwards. The MSE reduction peaks at 23% at 25 events and shrinks to 2% by about 2 s. At 1–5 events the plain mid wins, because the mid almost never moves on that timescale, so any adjustment adds error.

The weighted mid gets almost the same gains over the mid, though. The micro-price beats it only below about 10 ms, ties at about 30 ms, and is marginally worse (under 1% MSE) from about 85 ms to 2 s.

Once forecasts reach tens of milliseconds, the micro-price adds nothing beyond the imbalance itself. That matches the backtest, where the corrected micro-price and the weighted mid trade almost identically.
