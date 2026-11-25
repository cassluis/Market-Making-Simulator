# Multi-day sweep: is there an edge once the bugs are fixed?

**Question.** The original backtest looked profitable on one session. Across all 21 test sessions of Jan–Feb 2025, does any fair value have an edge, before or after the fixes?

**Method.**
- `as_diag.py` ran every (calibration day, test day) pair in `as.py`'s date list, then `multiday.py` pooled the results.
- Two configs are compared: `base` (the pre-fix code) and `all` (every fix).
- Daily PnL is tested across days. Per-fill markouts are pooled over all fills, with standard errors clustered by day.
- Outputs: `diag_out/multiday_summary.csv`, `multiday_markouts.csv`, `multiday_carry_post.csv`, `multiday_daily.csv` and `daily_pnl_all.png`.

## Daily PnL (index points per day, t over 21 days)

| Config | Cap | Mid | Weighted mid | Micro-price |
|---|---|---|---|---|
| base | 100 | −204 (−0.74) | −284 (−0.74) | +553 (+0.78) |
| base | 500 | −199 (−0.61) | −364 (−0.85) | +1,128 (+0.73) |
| all | 100 | −249 (−1.11) | −302 (−0.99) | −237 (−0.76) |
| all | 500 | −250 (−0.99) | −375 (−1.09) | −291 (−0.84) |

- **No cell is significant.** After fees the t-stats are slightly more negative. The share of positive days is 48–62% everywhere.
- **The daily noise is inventory, not trading.** Under `all` the daily standard error is about 250–350 pt, almost all of it inventory P&L before 16:00. Spread earned is only about 60–77 pt a day.

## Per fill (config `all`, cap 500)

| Fair value | Spread earned | Markout 1 s | Markout 10 s | Markout 60 s | Realized at 60 s | Net of fees (t) |
|---|---|---|---|---|---|---|
| Mid | 0.0925 | −0.1248 | −0.1352 | −0.1205 | −0.0280 | −0.0520 (−2.0) |
| Weighted mid | 0.0818 | −0.1161 | −0.1202 | −0.1223 | −0.0405 | −0.0645 (−2.7) |
| Micro-price | 0.0817 | −0.1156 | −0.1193 | −0.1188 | −0.0371 | −0.0611 (−2.6) |

- **Micro-price vs mid.** Its 1 s and 10 s markouts are better (+0.009 and +0.016 pt, t ≈ 3.0 and 2.4), but it earns 0.011 pt less at the fill (t −9.3). By 60 s the difference is gone.
- **Micro-price vs weighted mid.** No measurable difference: every gap is under 0.004 pt.

## Where the old micro-price profit came from

In `base`, the micro-price strategy's profit came mostly from inventory held after the close.

| Cap | Total PnL | From holding after 16:00 | t without it |
|---|---|---|---|
| 500 | +23,692 pt | +18,276 (77%) | +0.21 |
| 100 | +11,623 pt | +4,911 (42%) | +0.51 |

It ended the day long on 20 of 21 days, holding +224 contracts on average at cap 500. Its daily PnL had a 0.84 correlation with the day's 09:30–16:00 ES move, so it was mostly a leveraged long position.

## Conclusion

No fair value has an edge. Once the bugs are fixed, every strategy loses money per fill after fees, and the corrected micro-price behaves like the weighted mid.

One question remained open: even fixed strategies ended the day long on 17–18 of 21 days. That is answered in [`long-bias-diagnosis.md`](long-bias-diagnosis.md).

**Data notes.**
- The 2025-02-06 MBO file ends at 15:59:59, so that day is flattened one second early.
- No quote ever crossed the market, so fix `f` never triggered.
