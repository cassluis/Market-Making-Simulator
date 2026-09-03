"""Dump the raw MBO and mbp-1 records around one mismatching sequence.

Answers *why* a packet disagrees, by showing every record on both feeds for that
sequence side by side, plus the replayed book state after each MBO record.

Usage:  python recon_trace.py 20250106 149529 [267426 ...]
"""

import sys
import glob
import os

import databento as db

sys.path.insert(0, r'F:/market_making')
from recon_check import (top, day_of, INSTRUMENT_ID, MBO_DIR, MBP1_DIR,
                         LEGACY_MBP1_DIR, UNDEF_PRICE)
from mm_sim import OrderBook


def px(p):
    return 'None' if p in (None, UNDEF_PRICE) else f'{p / 1e9:.2f}'


def main(day, seqs):
    seqs = sorted(int(s) for s in seqs)
    lo, hi = seqs[0], seqs[-1]
    mbo = glob.glob(os.path.join(MBO_DIR, f'*{day}*.mbo.dbn.zst'))[0]
    mbp1 = next(g[0] for d in (MBP1_DIR, LEGACY_MBP1_DIR)
                for g in [glob.glob(os.path.join(d, f'*{day}*.mbp-1.dbn.zst'))] if g)

    # official side first -- cheap, and we only keep the window
    off = {}
    for rec in db.DBNStore.from_file(mbp1):
        if rec.instrument_id != INSTRUMENT_ID or not (lo <= rec.sequence <= hi + 2):
            continue
        lv = rec.levels[0]
        off.setdefault(rec.sequence, []).append(
            (rec.ts_event, str(rec.action), str(rec.side), rec.price, rec.size,
             rec.depth, lv.bid_px, lv.bid_sz, lv.bid_ct, lv.ask_px, lv.ask_sz, lv.ask_ct))

    book = OrderBook()
    want = set(seqs)
    trace = {}
    for rec in db.DBNStore.from_file(mbo):
        if rec.instrument_id != INSTRUMENT_ID:
            continue
        s = rec.sequence
        if s > hi + 2:
            break
        book.handle(rec)
        if s in want or (s - 1) in want:
            trace.setdefault(s, []).append(
                (rec.ts_event, str(rec.action), str(rec.side), rec.order_id,
                 rec.price, rec.size, rec.flags, top(book, 'B') + top(book, 'A')))

    for s in seqs:
        print(f'\n{"=" * 100}\nsequence {s}\n{"=" * 100}')
        print('  --- MBO records (replayed book state after each) ---')
        print(f'  {"action":<8}{"side":<6}{"order_id":>14}{"price":>10}{"sz":>6}'
              f'{"flags":>7}   -> {"bid":>9}{"bsz":>6}{"bct":>5} |{"ask":>9}{"bsz":>6}{"act":>5}')
        for ts, a, sd, oid, p, sz, fl, st in trace.get(s, []):
            print(f'  {a:<8}{sd:<6}{oid:>14}{px(p):>10}{sz:>6}{int(fl):>7}   -> '
                  f'{px(st[0]):>9}{st[1]:>6}{st[2]:>5} |{px(st[3]):>9}{st[4]:>6}{st[5]:>5}')

        print('  --- mbp-1 records (official book state carried by each) ---')
        print(f'  {"action":<8}{"side":<6}{"price":>10}{"sz":>6}{"depth":>7}'
              f'          {"bid":>9}{"bsz":>6}{"bct":>5} |{"ask":>9}{"asz":>6}{"act":>5}')
        for ts, a, sd, p, sz, dep, bp, bs, bc, ap, asz, ac in off.get(s, []):
            print(f'  {a:<8}{sd:<6}{px(p):>10}{sz:>6}{dep:>7}          '
                  f'{px(bp):>9}{bs:>6}{bc:>5} |{px(ap):>9}{asz:>6}{ac:>5}')

        nxt = trace.get(s + 1)
        if nxt:
            st = nxt[-1][-1]
            print(f'  --- replay state after the NEXT packet ({s + 1}) ---')
            print(f'  {px(st[0]):>9}{st[1]:>6}{st[2]:>5} |{px(st[3]):>9}{st[4]:>6}{st[5]:>5}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
