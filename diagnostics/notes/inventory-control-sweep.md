# Inventory control: does a working A-S skew rescue the strategy?

**Question.** With σ in index points instead of log returns, the Avellaneda–Stoikov inventory skew actually works. Does that remove the long bias, and does it make the strategy profitable?

**Answer.** It removes the bias, but it doesn't help profitability. Without the inventory noise, the result goes from "no edge" to a clearly **negative** edge.

## Method

- **Base setup:** `as_diag.py --inv`, pooled with `inv_multiday.py`, over the 21 sessions of 2025. Every variant uses config `all`, the mid and weighted-mid fair values, and caps 100 and 500.
- **ctrl:** the old setting (γ = 0.1, k = 1.5, σ in log units). It reproduces the multi-day sweep exactly.
- **as:** A-S with σ_px = σ_log × mid.
- **skew:** the same as `as`, but without A-S's σ²γT spread widening. Only the reservation-price shift changes.
- **γ values:** 1e-5, 2.5e-5, 5e-5, 1e-4 and 2.5e-4. For each γ, k is chosen so the zero-inventory spread stays at 1.291 pt, so only the inventory term changes.
- **Size of the skew:** 0.005–0.12 pt per contract at the median sample, about 5× that at the open, and near zero by 15:55.
- **Caps:** no controlled strategy held more than 26 contracts, so caps 100 and 500 give identical results. Cap 500 is shown.

## Results (cap 500, index points per day, t over 21 days)

| Variant | γ | Fair value | PnL/day (t) | After fees (t) | Buys | Sells | Days long / short | Mean abs(inv) | Realized 60 s per fill (t) |
|---|---|---|---|---|---|---|---|---|---|
| ctrl | 0.1 | mid | −250 (−0.99) | −266 (−1.04) | 7,010 | 6,504 | 18 / 2 | 29.5 | −0.028 (−1.1) |
| ctrl | 0.1 | wmid | −375 (−1.09) | −398 (−1.15) | 10,131 | 9,510 | 17 / 3 | 36.9 | −0.041 (−1.7) |
| as | 1e-5 | mid | −72.3 (−1.96) | −91.4 (−2.36) | 8,355 | 8,332 | 11 / 8 | 2.64 | −0.037 (−2.0) |
| as | 1e-5 | wmid | −57.9 (−1.51) | −80.0 (−2.01) | 9,672 | 9,661 | 11 / 8 | 3.60 | −0.021 (−1.2) |
| as | 2.5e-5 | mid | −44.2 (−1.93) | −63.7 (−2.56) | 8,538 | 8,517 | 10 / 8 | 1.65 | −0.037 (−2.7) |
| as | 2.5e-5 | wmid | −35.9 (−1.35) | −57.2 (−2.04) | 9,323 | 9,314 | 11 / 8 | 2.26 | −0.030 (−1.9) |
| as | 5e-5 | mid | −33.7 (−2.10) | −52.7 (−2.96) | 8,327 | 8,303 | 12 / 7 | 1.18 | −0.044 (−3.7) |
| as | 5e-5 | wmid | −29.6 (−1.72) | −49.6 (−2.65) | 8,765 | 8,760 | 11 / 9 | 1.49 | −0.039 (−2.7) |
| as | 1e-4 | mid | −28.5 (−2.20) | −45.4 (−3.16) | 7,383 | 7,359 | 11 / 4 | 0.85 | −0.054 (−3.8) |
| as | 1e-4 | wmid | −23.9 (−1.99) | −41.3 (−3.09) | 7,615 | 7,613 | 9 / 8 | 1.12 | −0.045 (−3.8) |
| as | 2.5e-4 | mid | −23.1 (−2.94) | −34.6 (−4.03) | 5,036 | 5,026 | 8 / 9 | 0.51 | −0.052 (−3.3) |
| as | 2.5e-4 | wmid | −17.5 (−1.98) | −29.3 (−3.12) | 5,163 | 5,165 | 8 / 9 | 0.62 | −0.054 (−3.5) |

Days long and short don't add up to 21, because the remaining days end flat. The `skew` rows are in `diag_out/inv_multiday_summary.csv`. They lose more as γ grows (down to −109 pt/day after fees, t −4.9), because without the spread widening a large skew pushes quotes across the touch, causing 78–308 taker fills a day.

## Conclusions

**The long bias is gone.**
- Even the weakest γ evens out buys and sells to within 0.3%.
- Days ending long or short go from 18 / 2 to roughly balanced: 8–13 long and 4–9 short (sign-test p ≥ 0.096).
- Mean absolute inventory falls from about 30–37 contracts to 0.4–3.6.
- A lean of under one contract remains, from the tape's tilt.

**The edge is negative.**
- Removing the inventory swings cuts the daily PnL standard error 7–30×, from about 250–340 pt to 8–40 pt.
- That exposes a consistent loss: all 20 controlled strategies lose before and after fees, at −17 to −76 pt/day gross and −29 to −109 pt/day net (t after fees −2.0 to −4.9).
- Per fill, the quotes earn about 0.08 pt of spread but give back 0.10–0.14 pt within 60 s, so the realized spread is −0.02 to −0.06 pt.

**γ only changes the size of the loss.** The largest γ loses least mainly because it trades about 40% less.

**Caveat.** The 20 variants are correlated, and the t-stats aren't adjusted for multiple comparisons. But every variant, and every per-fill measure, has the same sign.

`as.py` now uses the `as`, γ = 1e-4 setting.
