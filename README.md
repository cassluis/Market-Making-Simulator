# Market Making Simulator

L3 order book reconstruction from CME MDP3 message data, plus the price impact and fair
value work built on top of it. ES front month, 28 sessions, 6 Jan to 6 Feb 2025.

## Reconstruction

286,286,660 MBO messages replayed through mm_sim.OrderBook and checked against the official
Databento mbp-1 feed. 100.000000% top of book agreement, zero mismatches across 153.8M
events, zero unhandled messages. Per session numbers are in results/recon_accuracy.csv.

Join on the F_LAST flag, not the MDP3 sequence number. Trailing records in a packet carry
the previous sequence, so joining on sequence reports a false 0.009% error rate.

## Findings

Both of these use the 23 sessions with a full RTH block.

Impact is contemporaneous, not predictive. Depth scaled OFI explains 60% of mid price
variation at 200ms buckets and under 0.1% one bucket ahead.

The weighted mid only helps at certain horizons. It cuts MSE by 21% at 25 events (roughly
50ms), but at 1 event it is 5x worse than the plain mid, and there the micro price beats it
on all 23 sessions.

## Attribution

The order book itself is mine: mm_sim.py

The validation harness that checks it against mbp-1 was written with AI assistance: recon_check.py
