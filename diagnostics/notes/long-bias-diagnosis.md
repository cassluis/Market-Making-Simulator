# Why every strategy ends the day long: the cause is the tape, not the simulator

Run 2026-10-11. Scripts are in the session folder next to as_diag.py: `mirror_test.py` and `market_asym.py`.
Outputs are in `diag_out/`: `mirror_check.csv`, `mirror_fills_<date>_mirror.parquet`, `mirror_summary_*`, `mirror_counts_*`, `market_asym.csv`, and `logs/`.
Nothing was written under /Volumes/T9.

## 1. The simulator is exactly bid/ask symmetric (mirror test)
- I reflected each day's real MBO tape through C = 12,000: price p → C − p, and side B ↔ A. Fair values were reflected the same way (mid' = C − mid, wmid' = C − wmid).
- I then ran `as_diag.simulate()` on it unchanged. The shim replaces only `as_diag.db`. Volatility is kept as is, since it enters only through vol²γT ≈ 1e-6.
- The test covered 8 strategies: {base, all} × {mid, wmid} × cap {100, 500}.
- On 20250121 and 20250123, the identity pass through the same shim matches the sweep's fills_<date>.parquet exactly.
- **168/168 (day, strategy) pairs are exact mirrors.** Every fill has the same ts, the opposite sign and price C − px, and inv@16 flips sign.

| strategy | buys | sells | long days | mirror buys | mirror sells | mirror short days |
|---|---|---|---|---|---|---|
| all/mid/500 | 7,010 | 6,504 | 18 | 6,504 | 7,010 | 18 |
| all/wmid/500 | 10,131 | 9,510 | 17 | 9,510 | 10,131 | 17 |
| base/mid/500 | 7,428 | 6,865 | 18 | 6,865 | 7,428 | 18 |

- Pooling the original and mirrored tapes gives a symmetric tape, and on it buys = sells exactly.
- If quotes() rounding, the T/C/M queue bookkeeping or OrderBook were asymmetric, the mirrored run would still lean long. It leans short instead.
- Other checks:
  - The fair-value mid equals the sim book mid at quote time (mean difference 2e-5 pt).
  - The MBO has no Add/Modify records with side N.
  - Buys and sells fill at the same edge vs mid (0.0922 vs 0.0927 pt).

## 2. The tape hits the bid level more often than the ask level (simulator-free check)
- For each RTH second I took the mid strategy's zero-inventory quotes q_b = floor((mid − 0.645)/.25)·.25 and q_a = ceil(...).
- Using only features/<date>_trades.parquet, I counted the seconds in which the next second's trades print at or beyond the bid level (≤ q_b) or the ask level (≥ q_a).
  - Bid-level seconds: 10,460. Ask-level seconds: 9,676. Ratio 1.081, more on 18/21 days, day-level t = 2.84. On up days it holds 12/14 times.
  - The per-day difference correlates +0.93 with the sim's buys − sells.
- Given that the tape reached the level, the sim fills 0.670 (bid) vs 0.672 (ask) of the time. So the queue model adds nothing.
- Aggressor volume is balanced: sell/buy = 0.999.
- 1-second mid changes, pooled over 21 days (drops/rises):
  - Moves ≤0.25 pt: 0.95–0.96 (more up).
  - Moves of 0.75–1.5 pt: 1.06–1.26 (more down).
  - Price gained +589 pt in sub-1-pt seconds and lost −472 pt in ≥1-pt seconds.
  - So in Jan–Feb 2025 RTH, ES climbs in small steps and falls in fast 1-pt-scale drops at the 1-second scale.
- The strategy requotes every second about 0.75–1 pt outside the mid, so it fills only on those fast moves, and those moves lean down. The result is more buys, even on up days.

## 3. Why the imbalance accumulates instead of mean-reverting
- `AS.quotes` uses σ = std of 1-s **log** returns (≈4.8e-5). The reservation-price shift q·σ²·γ·T_t is therefore about 2e-6 pt per contract, and 139 contracts shift it by only 3e-4 pt.
- The Avellaneda-Stoikov inventory control is effectively off, so inventory follows a random walk whose drift is the hit-rate asymmetry.
- With σ in price units (σ·mid), the same γ = 0.1 gives about 68 pt per contract. So the fix needs a recalibration, not just a units change.

**Conclusion:** the long bias is a genuine feature of the tape: fast ≥0.75-pt down-moves outnumber fast up-moves. A strategy with no inventory control, quoting about 3 ticks outside the touch, registers those moves. There is no simulator asymmetry.
