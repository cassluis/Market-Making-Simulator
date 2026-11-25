# A-S inventory control with sigma in price units: 21-day sweep

Run 2026-10-11. Nothing was written under /Volumes/T9.
- Code (session folder): `as_diag.py --inv` (new switchable variant) and `inv_multiday.py` (pooling, reuses multiday.py's `mean_se` and `pooled`).
- Outputs (`diag_out/`): `inv_summary_<date>.csv`, `inv_fills_<date>.parquet`, `inv_paths_<date>.npz`, `inv_multiday_summary.csv`, `inv_multiday_daily.csv`, `inv_port_check.csv`, `logs/inv_*.log`.

## Method
- Every strategy uses config `all` (queue, priority, 16:00 close, taker, causal fixes). Fair values are mid and wmid, caps are 100 and 500, and the dates are multiday.DATES (21 days).
- **ctrl** is as.py's γ = 0.1, k = 1.5 with σ in log units. It reproduces the earlier sweep's `all` rows exactly (zero diff in pnl, fills, inv@16:00 and realized 60 s on all 21 days).
- **as** calls `AS.quotes` with σ_px = σ_log × mid (the 1 s, 60 s rolling std, ≈0.3 pt/√s).
- **skew** is the same as `as` minus the σ²γT_t spread widening, so only the reservation-price shift changes.
- γ ∈ {1e-5, 2.5e-5, 5e-5, 1e-4, 2.5e-4}. Each γ gets k = γ / expm1(γ·1.2908/2), which keeps the zero-inventory spread (2/γ)ln(1+γ/k) at as.py's 1.291 pt.
- σ_px²·T_t per RTH sample: median ≈ 470 pt² (daily medians range 155–1,455), time average ≈ 900 pt², ≈2,700 at 09:35, ≈2,070 at 10:00 and ≈35 at 15:55.
- So the skew per contract is γ × that: 0.005–0.12 pt at the median sample, 0.009–0.23 pt on time average, and 0.02–0.5 pt at 10:00.
- Every controlled variant has max |inv| ≤ 26, so cap 100 and cap 500 are identical for them. Only cap 500 is shown below.

## Results, cap 500 (PnL in index points per day, ×$50; t over 21 days)
| variant | γ | fv | skew/ct (median) | PnL/day (t) | after fees (t) | buys | sells | days long/short @16:00 | mean abs(inv) | realized 60 s/fill (t) | net of fee (t) | fills/day |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ctrl | 0.1 | mid | ~1e-6 | −250 (−0.99) | −266 (−1.04) | 7,010 | 6,504 | 18/2 | 29.5 | −.028 (−1.1) | −.052 (−2.0) | 644 |
| ctrl | 0.1 | wmid | ~1e-6 | −375 (−1.09) | −398 (−1.15) | 10,131 | 9,510 | 17/3 | 36.9 | −.041 (−1.7) | −.065 (−2.7) | 935 |
| as | 1e-5 | mid | .005 | −72.3 (−1.96) | −91.4 (−2.36) | 8,355 | 8,332 | 11/8 | 2.64 | −.037 (−2.0) | −.061 (−3.3) | 795 |
| as | 1e-5 | wmid | .005 | −57.9 (−1.51) | −80.0 (−2.01) | 9,672 | 9,661 | 11/8 | 3.60 | −.021 (−1.2) | −.045 (−2.5) | 921 |
| as | 2.5e-5 | mid | .012 | −44.2 (−1.93) | −63.7 (−2.56) | 8,538 | 8,517 | 10/8 | 1.65 | −.037 (−2.7) | −.061 (−4.5) | 812 |
| as | 2.5e-5 | wmid | .012 | −35.9 (−1.35) | −57.2 (−2.04) | 9,323 | 9,314 | 11/8 | 2.26 | −.030 (−1.9) | −.054 (−3.4) | 887 |
| as | 5e-5 | mid | .023 | −33.7 (−2.10) | −52.7 (−2.96) | 8,327 | 8,303 | 12/7 | 1.18 | −.044 (−3.7) | −.068 (−5.8) | 792 |
| as | 5e-5 | wmid | .023 | −29.6 (−1.72) | −49.6 (−2.65) | 8,765 | 8,760 | 11/9 | 1.49 | −.039 (−2.7) | −.063 (−4.3) | 835 |
| as | 1e-4 | mid | .047 | −28.5 (−2.20) | −45.4 (−3.16) | 7,383 | 7,359 | 11/4 | 0.85 | −.054 (−3.8) | −.078 (−5.6) | 702 |
| as | 1e-4 | wmid | .047 | −23.9 (−1.99) | −41.3 (−3.09) | 7,615 | 7,613 | 9/8 | 1.12 | −.045 (−3.8) | −.069 (−5.9) | 725 |
| as | 2.5e-4 | mid | .117 | −23.1 (−2.94) | −34.6 (−4.03) | 5,036 | 5,026 | 8/9 | 0.51 | −.052 (−3.3) | −.076 (−4.9) | 479 |
| as | 2.5e-4 | wmid | .117 | −17.5 (−1.98) | −29.3 (−3.12) | 5,163 | 5,165 | 8/9 | 0.62 | −.054 (−3.5) | −.078 (−5.0) | 492 |
| skew | 1e-5 | mid | .005 | −69.7 (−2.08) | −89.7 (−2.51) | 8,788 | 8,768 | 10/8 | 2.32 | −.041 (−2.5) | −.065 (−3.9) | 836 |
| skew | 1e-5 | wmid | .005 | −64.4 (−1.63) | −88.0 (−2.12) | 10,296 | 10,286 | 11/9 | 3.60 | −.030 (−1.7) | −.054 (−3.1) | 980 |
| skew | 2.5e-5 | mid | .012 | −47.3 (−2.22) | −68.5 (−2.90) | 9,263 | 9,240 | 10/9 | 1.49 | −.043 (−3.0) | −.067 (−4.6) | 881 |
| skew | 2.5e-5 | wmid | .012 | −44.5 (−1.71) | −69.2 (−2.47) | 10,778 | 10,771 | 11/9 | 2.20 | −.038 (−2.7) | −.062 (−4.4) | 1,026 |
| skew | 5e-5 | mid | .023 | −38.6 (−2.30) | −60.5 (−3.09) | 9,565 | 9,542 | 13/5 | 0.97 | −.048 (−3.6) | −.072 (−5.4) | 910 |
| skew | 5e-5 | wmid | .023 | −42.9 (−2.29) | −68.9 (−3.18) | 11,360 | 11,353 | 10/8 | 1.53 | −.051 (−4.0) | −.075 (−5.8) | 1,082 |
| skew | 1e-4 | mid | .047 | −41.0 (−2.68) | −63.9 (−3.43) | 10,034 | 10,015 | 10/5 | 0.75 | −.049 (−4.5) | −.073 (−6.7) | 955 |
| skew | 1e-4 | wmid | .047 | −50.0 (−2.99) | −78.6 (−3.84) | 12,488 | 12,488 | 8/8 | 1.08 | −.055 (−5.3) | −.079 (−7.6) | 1,189 |
| skew | 2.5e-4 | mid | .117 | −62.0 (−4.52) | −86.5 (−4.76) | 10,716 | 10,706 | 9/9 | 0.38 | −.064 (−8.5) | −.088 (−11.6) | 1,020 |
| skew | 2.5e-4 | wmid | .117 | −76.0 (−4.44) | −108.6 (−4.87) | 14,260 | 14,260 | 9/8 | 0.60 | −.063 (−7.6) | −.087 (−10.4) | 1,358 |

Days long/short don't sum to 21 because the rest end flat. The realized spread is pooled per fill with day-clustered SE, as in multiday.py.

## Other observations
- **Bias.**
  - Buys − sells per day falls from +24 (ctrl mid, t 2.1) to between −0.1 and +1.1 (|t| ≤ 1.7).
  - The sign-test p for long vs short days falls from 0.0004 (ctrl mid) to 0.096–1.0.
  - Mean inv@16:00 falls from +23.6 to between +0.0 and +1.3 contracts.
- **Residual tilt.**
  - Every controlled variant is still long more of the time than short: for example 60% vs 26% at as/1e-5/mid, and 24% vs 15% at as/2.5e-4/mid.
  - Mean inventory is +0.14 to +2.1 contracts.
  - This is the tape's tilt pushing against a linear restoring force, now about 1 contract instead of 20–30.
  - At 15:55 the skew has almost decayed (T_t → 0), but mean inventory there is still within ±0.8.
- **PnL variance.**
  - The daily PnL SE falls from 253–343 to 8–40 pt.
  - Only 10–38% of days are positive after fees, versus 48–57% for ctrl.
- **Where the PnL goes.**
  - Spread capture stays about 0.08 pt per fill.
  - The 60 s markout is −0.10 to −0.14 for `as`, about the same as ctrl's −0.12.
  - Per fill, the control does not reduce adverse selection, and at larger γ the realized spread gets slightly worse.
  - The large γ = 2.5e-4 `as` variant loses least per day mostly because it fills about 40% less than the weak-γ variants (the σ²γT_t widening at the open).
- **skew vs as.**
  - Without the widening, a large skew pushes the quote across the touch, causing 78–308 taker fills per day.
  - This is why `skew` loses more as γ grows. Spread widening is not what removes the bias: `skew` removes it equally well.

## Verdict
**Bias.** Proper inventory control removes the end-of-day long bias.
- With σ in price units, even the weakest γ (≈0.005 pt per contract at the median sample) brings buys and sells level, with a gap of 0.1–0.3%.
- Close-of-day inventory goes from long on 17–18/21 days to roughly balanced: 8–13 long vs 4–9 short, sign-test p ≥ 0.096.
- Mean |inventory| falls from about 30–37 contracts to about 0.4–3.6.
- The tape's sell-side tilt is still visible as a sub-1-contract long lean, but it no longer builds into a position.

**Edge.** It does not change the no-edge conclusion; it sharpens it into a negative edge.
- Removing the inventory-carry noise cuts daily PnL SE by about 7–30x.
- Every controlled strategy (20 of 20) loses money before and after fees: about −17 to −76 pt/day gross and −29 to −109 pt/day net, which is about −$1,500 to −$5,400/day.
- After fees the t-stats run from −2.0 to −4.9.
- The 60 s realized spread is −0.02 to −0.06 pt per fill (net of fee −0.045 to −0.09, t −2.5 to −11.6).
- The control reduces how much the strategy loses, from −250/day mostly in inventory carry to as little as −29/day, but no γ makes it profitable.
- The quotes about 3 ticks outside the touch are adversely selected by more than the spread they earn.
- Caveat: the 20 variants are correlated and the t-stats are not adjusted for multiple comparisons. But the sign is the same in all 20 and in every per-fill measure.
