# Why every strategy ended the day long: the tape, not the simulator

**Question.** Even after every fix, the mid and weighted-mid strategies ended 17–18 of 21 days long, although ES rose on only 14. A passive market maker should lean short on up days. Is something in the simulator asymmetric?

**Answer.** No. The simulator is exactly bid/ask symmetric. The long bias is a feature of the market: in these sessions, fast down-moves outnumbered fast up-moves. With inventory control effectively switched off, that tilt accumulated.

## 1. The mirror test (`mirror_test.py`)

**Method.**
- Each day's real MBO tape was reflected through C = 12,000: every price p becomes C − p, and bids and asks swap.
- The fair values were reflected the same way, and the unchanged `as_diag.simulate()` was run on the result.
- It covered 8 strategies: {base, all} × {mid, weighted mid} × cap {100, 500}.

**Result.** **All 168 (day, strategy) pairs are exact mirrors.** Every fill has the same timestamp, the opposite sign and price C − p, and the 16:00 inventory flips sign. If the quoting, the queue logic or the order book were asymmetric, the mirrored run would still lean long. It leans short instead.

| Strategy | Buys | Sells | Days long | Mirror buys | Mirror sells | Mirror days short |
|---|---|---|---|---|---|---|
| all / mid / 500 | 7,010 | 6,504 | 18 | 6,504 | 7,010 | 18 |
| all / wmid / 500 | 10,131 | 9,510 | 17 | 9,510 | 10,131 | 17 |
| base / mid / 500 | 7,428 | 6,865 | 18 | 6,865 | 7,428 | 18 |

**Other checks.**
- The fair-value mid equals the simulated book's mid at quote time (mean difference 2e-5 pt).
- Buys and sells fill at the same edge against the mid (0.0922 vs 0.0927 pt).

## 2. The tape itself (`market_asym.py`, no simulator)

**Method.** For each regular-hours second, take the mid strategy's zero-inventory quotes and count how often the next second's trades reach the bid level versus the ask level.

**Results.**
- **Bid level reached:** 10,460 seconds. **Ask level reached:** 9,676 seconds.
- That's a ratio of 1.081: more often at the bid on 18 of 21 days (day-level t = 2.84), including 12 of 14 up days.
- The daily difference correlates +0.93 with the simulator's buys minus sells.
- When the tape reaches a level, the simulator fills 0.670 (bid) vs 0.672 (ask) of the time. So the queue model adds no bias.

**The mechanism.**
- **Aggressor volume** is balanced (sell/buy = 0.999).
- **Small one-second moves** (≤ 0.25 pt) are slightly more often up.
- **Large ones** (0.75–1.5 pt) are 6–26% more often down.
- In these sessions ES climbed in small steps and fell in fast drops. Quotes sitting about 3 ticks from the mid, requoted every second, fill only on the fast moves, so they buy more often.

## 3. Why the imbalance accumulated

The pre-fix quoting used σ as the standard deviation of one-second **log** returns (about 4.8e-5). That makes the A-S reservation-price shift q·σ²·γ·T about 2e-6 pt per contract: inventory control was effectively off.

Inventory therefore random-walked with a drift equal to the hit-rate asymmetry. With σ in price units, the same γ = 0.1 would give about 68 pt per contract, so the fix needs a recalibration as well as a units change. See [`inventory-control-sweep.md`](inventory-control-sweep.md).
