"""
Batch-replay every MBO file in ES_MBO into per-day parquet feature files.

Resumable: skips any day whose output already exists, so you can stop it (Ctrl-C)
and restart without losing work.

Usage:  python build_features.py
"""

import os
import glob
import time
import traceback

import pandas as pd
import databento as db

from mm_sim import OrderBook, F_LAST      # your class and flag constant

MBO_DIR = r"F:/market_making/ES_MBO"
OUT_DIR = r"F:/market_making/features"

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
    


if __name__ == "__main__":
    main()