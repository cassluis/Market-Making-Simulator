"""Characterise the residual top-of-book mismatches for one session.

recon_check.py answers "how accurate is the reconstruction". This answers "what
kind of wrong is the remainder" -- specifically whether the mismatching packets
are transient (the very next packet agrees again, so the book self-heals and
never drifts) or persistent (real book corruption that accumulates).

Also cross-tabulates mismatching packets against the actions they contain, since
OrderBook.handle() only *logs* T/F records rather than reducing resting size.

Usage:  python recon_diagnose.py 20250106
"""

import sys
import glob
import os
from collections import Counter

import numpy as np
import databento as db

sys.path.insert(0, r'F:/market_making')
from recon_check import (Buf, dedupe_last, top, load_mbp1, day_of,
                         INSTRUMENT_ID, MBO_DIR, MBP1_DIR, LEGACY_MBP1_DIR,
                         UNDEF_PRICE)
from mm_sim import OrderBook


def replay_with_actions(path):
    """Replay, recording per-packet state plus the set of actions in that packet."""
    book = OrderBook()
    buf = Buf()
    acts = []
    cur = set()
    prev_seq = prev_ts = None
    for rec in db.DBNStore.from_file(path):
        if rec.instrument_id != INSTRUMENT_ID:
            continue
        s = rec.sequence
        if prev_seq is not None and s != prev_seq:
            buf.push(prev_seq, prev_ts, *top(book, 'B'), *top(book, 'A'))
            acts.append(''.join(sorted(cur)))
            cur = set()
        book.handle(rec)
        cur.add(str(rec.action))
        prev_seq, prev_ts = s, rec.ts_event
    if prev_seq is not None:
        buf.push(prev_seq, prev_ts, *top(book, 'B'), *top(book, 'A'))
        acts.append(''.join(sorted(cur)))
    return book, buf, np.array(acts, dtype=object)


def main(day):
    mbo = glob.glob(os.path.join(MBO_DIR, f'*{day}*.mbo.dbn.zst'))[0]
    mbp1 = None
    for d in (MBP1_DIR, LEGACY_MBP1_DIR):
        g = glob.glob(os.path.join(d, f'*{day}*.mbp-1.dbn.zst'))
        if g:
            mbp1 = g[0]
            break
    print(f'MBO : {mbo}\nMBP1: {mbp1}\n')

    book, mine, acts = replay_with_actions(mbo)
    theirs = load_mbp1(mbp1)

    m, t = mine.cols(), theirs.cols()
    ms, mi = dedupe_last(m['seq'])
    ts_, ti = dedupe_last(t['seq'])
    common, ia, ib = np.intersect1d(ms, ts_, assume_unique=True, return_indices=True)
    mi, ti = mi[ia], ti[ib]

    exact = ((m['bpx'][mi] == t['bpx'][ti]) & (m['apx'][mi] == t['apx'][ti]) &
             (m['bsz'][mi] == t['bsz'][ti]) & (m['asz'][mi] == t['asz'][ti]) &
             (m['bct'][mi] == t['bct'][ti]) & (m['act'][mi] == t['act'][ti]))
    n = len(exact)
    bad = ~exact
    nbad = int(bad.sum())
    print(f'packets compared : {n:,}')
    print(f'mismatching      : {nbad:,}  ({nbad / n:.6%})\n')

    # --- run lengths: is a mismatch a one-packet blip or a sustained drift? --
    runs = Counter()
    i = 0
    idx = np.flatnonzero(bad)
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        runs[j - i + 1] += 1
        i = j + 1
    print('=== consecutive-mismatch run lengths ===')
    tot_runs = sum(runs.values())
    for L in sorted(runs)[:12]:
        print(f'  {L:>4} packet(s) : {runs[L]:>8,} runs  ({runs[L] / tot_runs:6.2%})')
    if len(runs) > 12:
        print(f'  longest run    : {max(runs)} packets')
    print(f'  distinct runs  : {tot_runs:,}  '
          f'(mean length {nbad / tot_runs:.2f} packets)')
    print(f'  => book recovers within 1 packet in '
          f'{runs.get(1, 0) / tot_runs:.2%} of episodes\n')

    # --- does the book drift? compare error rate over the day in 10 slices ---
    print('=== mismatch rate through the session (10 equal slices) ===')
    for k, part in enumerate(np.array_split(bad, 10)):
        ts0 = np.datetime64(int(t['ts'][ti][k * n // 10]), 'ns')
        print(f'  slice {k}  {str(ts0)[:19]}  {part.mean():.6%}')
    print()

    # --- which actions appear in mismatching packets? ---
    a = acts[mi]
    tab = Counter(a[bad])
    base = Counter(a)
    print('=== actions present in the packet (mismatching vs all) ===')
    print(f'  {"actions":<16}{"bad":>10}{"all":>12}{"bad rate":>12}')
    for k, v in tab.most_common(10):
        print(f'  {k:<16}{v:>10,}{base[k]:>12,}{v / base[k]:>12.4%}')
    print()

    # --- how far off, in ticks, when the price is wrong? ---
    TICK = 250_000_000
    bp_ok = m['bpx'][mi] == t['bpx'][ti]
    ap_ok = m['apx'][mi] == t['apx'][ti]
    for lbl, ok, mine_px, their_px in (
            ('bid', bp_ok, m['bpx'][mi], t['bpx'][ti]),
            ('ask', ap_ok, m['apx'][mi], t['apx'][ti])):
        w = ~ok & (mine_px != UNDEF_PRICE) & (their_px != UNDEF_PRICE)
        if w.any():
            d = (mine_px[w].astype(np.int64) - their_px[w].astype(np.int64)) // TICK
            print(f'=== {lbl} price error, ticks (n={int(w.sum()):,}) ===')
            for tick, c in sorted(Counter(d.tolist()).items())[:12]:
                print(f'  {tick:>+4} ticks : {c:>8,}')
            print()

    print('=== book health ===')
    print(f'  unhandled events : {book.unhandled:,}')
    print(f'  unknown actions  : {dict(book.unknown)}')
    print(f'  trades logged    : {len(book.trades):,}')
    print(f'  fills logged     : {len(book.fills):,}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '20250106')
