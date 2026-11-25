# as_diag multi-day sweep — Jan–Feb 2025 (21 sessions)

Run 2026-10-11. All 21 (cal, test) pairs from as.py ran OK (20250203 reused from the earlier run of the same as_diag.py).
Outputs in the session folder's `diag_out/`: summary/fills/paths per day, plus
`multiday_summary.csv`, `multiday_markouts.csv`, `multiday_carry_post.csv`, `multiday_daily.csv`, `daily_pnl_all.png`.
Script: `multiday.py` (next to as_diag.py). Nothing written under /Volumes/T9.

Caveats
- T9 was unmounted part-way through aggregation. Pooled per-fill markouts are therefore built from per-day
  fill sums (mean × trade count). This is algebraically identical to the fill-level pool with day clusters,
  and was checked digit-for-digit against a fill-level run on 2 days.
- 20250206 MBO ends 15:59:59, so the 16:00 sample never fires. On that day base is marked at ~16:00
  (carry_post = 0), and `all` flattens at the touch 1 s early.
- No taker fills occurred in any config on any day (fix f never triggered).

## Daily PnL, points/day (t over 21 days)
| config | cap | mid | wmid | microprice |
|---|---|---|---|---|
| base | 100 | −204 (−0.74) | −284 (−0.74) | +553 (+0.78) |
| base | 500 | −199 (−0.61) | −364 (−0.85) | +1,128 (+0.73) |
| all  | 100 | −249 (−1.11) | −302 (−0.99) | −237 (−0.76) |
| all  | 500 | −250 (−0.99) | −375 (−1.09) | −291 (−0.84) |

After fees, the t-stats are slightly more negative. The share of positive days is 0.48–0.62 everywhere.

## Pooled per-fill (all, cap 500; day-clustered t)
| | spread | mk 1s | mk 10s | mk 60s | realized 60s | net of fee |
|---|---|---|---|---|---|---|
| mid | .0925 | −.1248 | −.1352 | −.1205 | −.0280 (−1.1) | −.0520 (−2.0) |
| wmid | .0818 | −.1161 | −.1202 | −.1223 | −.0405 (−1.7) | −.0645 (−2.7) |
| mp_de | .0817 | −.1156 | −.1193 | −.1188 | −.0371 (−1.6) | −.0611 (−2.6) |
| mp − mid | −.0108 (−9.3) | +.0092 (+3.0) | +.0159 (+2.4) | +.0017 (+0.2) | −.0090 (−0.8) | |
| mp − wmid | −.0001 (−0.4) | +.0005 (+1.0) | +.0009 (+0.5) | +.0036 (+1.1) | +.0035 (+1.1) | |

## base microprice carry_post
- cap 500: +18,276 of +23,692 total (77%). Excluding carry_post: t = +0.21.
- cap 100: +4,911 of +11,623 (42%). Excluding carry_post: t = +0.51.
- The per-day share swings from −299% to +277%. On 20250203 it is 83%.
- Base microprice is long at close on 20/21 days (mean +224 at cap 500). Its correlation with the daily RTH move is 0.84.

## Open issue (not investigated)
Even mid and wmid in config `all` end the day long on 17–18 of 21 days (for `all`/mid/500, 7,010 buys vs 6,504 sells), while ES rose on only 14 of 21.
A passive market maker should lean short on up days, so something asymmetric remains: as.quotes rounding, or the bid/ask queue/fill logic.
