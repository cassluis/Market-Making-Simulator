"""
Batch-replay every MBO file in ES_MBO into per-day parquet feature files.

Resumable: skips any day whose output already exists, so you can stop it (Ctrl-C)
and restart without losing work.

Usage:  python build_features.py
"""

import os
import glob
import traceback

import pandas as pd
import databento as db

from orderbook import OrderBook, F_LAST      # your class and flag constant

MBO_DIR = r"/volumes/t9/market_making/ES_MBO"
OUT_DIR = r"/volumes/t9/market_making/features"

BOOK_COLS  = ['ts', 'bid_px', 'ask_px', 'bid_sz', 'ask_sz']
TRADE_COLS = ['ts', 'price', 'size', 'side', 'bid', 'ask']


def day_from_path(path):
    """glbx-mdp3-20250106.mbo.dbn.zst -> 20250106"""
    name = os.path.basename(path)
    for part in name.split('-'):
        digits = part.split('.')[0]
        if len(digits) == 8 and digits.isdigit():
            return digits
    return name.split('.')[0]


def replay_file(path):
    """Replay one MBO file. Returns (book, rows)."""
    book = OrderBook()
    rows = []
    for rec in db.DBNStore.from_file(path):
        book.handle(rec)
        if rec.flags & F_LAST:
            bb, ba = book.best_bid, book.best_ask
            if bb is not None and ba is not None:
                rows.append((rec.ts_event, bb, ba,
                             sum(book.bids[bb].values()),
                             sum(book.asks[ba].values())))
    return book, rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    paths = sorted(glob.glob(os.path.join(MBO_DIR, "*.mbo.dbn.zst")))

    for path in paths:
        day = day_from_path(path)
        out_path = os.path.join(OUT_DIR, f"{day}.parquet")

        if os.path.exists(out_path):
            print(f"Skipping {day}: already exists")
            continue

        try:
            print(f"Processing {day}...")

            _, rows = replay_file(path)

            df = pd.DataFrame(rows, columns=BOOK_COLS)
            df.to_parquet(out_path, index=False)

            print(f"Saved {out_path}")

        except Exception:
            print(f"Failed {day}")
            traceback.print_exc()

if __name__ == "__main__":
    main()