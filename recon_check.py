"""Validate the MBO order-book reconstruction against Databento's official mbp-1.

For every ES session in ES_MBO/, replay the MBO stream through mm_sim.OrderBook
and compare the resulting top-of-book against the mbp-1 feed for the same day.

Alignment
---------
Primary ("flast"): MDP3 delivers events in packets and the final record of an
event carries the F_LAST flag (128). Both feeds set it on the same event, so the
book state at each F_LAST record is directly comparable. Joined on
(ts_event, sequence). This is also exactly where es_mbo_to_parquet.py snapshots
the book, so it measures the accuracy of the features the strategy actually uses.

Secondary ("seq"): the older compare_bbo.py approach -- take the last state per
MDP3 `sequence`. A sequence can span an F_LAST boundary (a packet's trailing
records share the previous sequence number), so this alignment reports spurious
mismatches. Kept to quantify that artifact, not as a measure of the book.

Every mutation goes through the user's own OrderBook.handle(), so this tests the
real reconstruction code rather than a reimplementation of it.

Usage:
    python recon_check.py                  # every session with mbp-1 available
    python recon_check.py 20250106         # one session
    python recon_check.py --limit 3000000  # smoke test, first N MBO records
    python recon_check.py --no-seq         # skip the secondary alignment
"""

import os
import gc
import sys
import glob
import time
import json

import numpy as np
import pandas as pd
import databento as db

sys.path.insert(0, r'F:/market_making')
from mm_sim import OrderBook

MBO_DIR = r'F:/market_making/ES_MBO'
MBP1_DIR = r'F:/market_making/ES_MBP1'
OUT_DIR = r'F:/market_making/results'
LEGACY_MBP1_DIR = r'C:/Users/cassc/Downloads/GLBX-20260822-5SNJX4LUJ4'

INSTRUMENT_ID = 5002              # ESH5 -- the only id in the ES.c.0 MBO files
UNDEF_PRICE = 9223372036854775807
F_LAST = 128

# RTH = 09:30-16:00 America/New_York. Jan/Feb 2025 is EST (UTC-5) throughout,
# so the window is a fixed 14:30-21:00 UTC -- no tz table needed.
RTH_START_UTC_NS = (14 * 3600 + 30 * 60) * 1_000_000_000
RTH_END_UTC_NS = (21 * 3600) * 1_000_000_000

COLS = ['ts', 'seq', 'bpx', 'bsz', 'bct', 'apx', 'asz', 'act']


# --------------------------------------------------------------------------- #
# growable column store
# --------------------------------------------------------------------------- #
class Buf:
    """Pre-allocated, doubling numpy columns for one alignment's book states."""

    DT = (('ts', np.int64), ('seq', np.uint32),
          ('bpx', np.int64), ('bsz', np.int32), ('bct', np.int32),
          ('apx', np.int64), ('asz', np.int32), ('act', np.int32))

    def __init__(self, cap=1 << 20):
        self.n = 0
        self.cap = cap
        self.a = {k: np.empty(cap, dtype=d) for k, d in self.DT}

    def _grow(self):
        self.cap *= 2
        for k, _ in self.DT:
            b = np.empty(self.cap, dtype=self.a[k].dtype)
            b[:self.n] = self.a[k][:self.n]
            self.a[k] = b

    def push(self, ts, seq, bpx, bsz, bct, apx, asz, act):
        if self.n == self.cap:
            self._grow()
        i = self.n
        a = self.a
        a['ts'][i] = ts
        a['seq'][i] = seq
        a['bpx'][i] = bpx
        a['bsz'][i] = bsz
        a['bct'][i] = bct
        a['apx'][i] = apx
        a['asz'][i] = asz
        a['act'][i] = act
        self.n = i + 1

    def frame(self):
        return pd.DataFrame({k: self.a[k][:self.n] for k, _ in self.DT})


# --------------------------------------------------------------------------- #
# feeds
# --------------------------------------------------------------------------- #
def top(book, side):
    """(price, total size, order count) at the best level, in mbp-1 encoding."""
    b = book.bids if side == 'B' else book.asks
    if not b:
        return UNDEF_PRICE, 0, 0
    px, orders = b.peekitem(0)
    return px, sum(orders.values()), len(orders)


def replay_mbo(path, limit=None, want_seq=True):
    """One pass: emit book state at every F_LAST, and at every sequence change."""
    book = OrderBook()
    flast, seqbuf = Buf(), Buf()
    prev = None
    n = 0
    for rec in db.DBNStore.from_file(path):
        if limit and n >= limit:
            break
        n += 1
        if rec.instrument_id != INSTRUMENT_ID:
            continue
        s = rec.sequence
        if want_seq and prev is not None and s != prev[1]:
            seqbuf.push(*prev, *top(book, 'B'), *top(book, 'A'))
        book.handle(rec)
        if rec.flags & F_LAST:
            flast.push(rec.ts_event, s, *top(book, 'B'), *top(book, 'A'))
        prev = (rec.ts_event, s)
    if want_seq and prev is not None:
        seqbuf.push(*prev, *top(book, 'B'), *top(book, 'A'))
    return book, flast, seqbuf, n


def load_mbp1(path, max_ts=None, want_seq=True):
    """One pass over mbp-1: same two alignments, from the official book state."""
    flast, seqbuf = Buf(), Buf()
    prev = None
    for rec in db.DBNStore.from_file(path):
        if rec.instrument_id != INSTRUMENT_ID:
            continue
        if max_ts is not None and rec.ts_event > max_ts:
            break
        lv = rec.levels[0]
        state = (lv.bid_px, lv.bid_sz, lv.bid_ct, lv.ask_px, lv.ask_sz, lv.ask_ct)
        if want_seq and prev is not None and rec.sequence != prev[1]:
            seqbuf.push(*prev)
        if rec.flags & F_LAST:
            flast.push(rec.ts_event, rec.sequence, *state)
        prev = (rec.ts_event, rec.sequence, *state)
    if want_seq and prev is not None:
        seqbuf.push(*prev)
    return flast, seqbuf


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #
def agree(mine, theirs, keys):
    """Join two state frames on `keys` and return (joined, exact, price_ok)."""
    a = mine.drop_duplicates(keys, keep='last')
    b = theirs.drop_duplicates(keys, keep='last')
    j = a.merge(b, on=keys, suffixes=('_m', '_t'), how='inner')
    px = (j.bpx_m == j.bpx_t) & (j.apx_m == j.apx_t)
    ex = px & (j.bsz_m == j.bsz_t) & (j.asz_m == j.asz_t) \
            & (j.bct_m == j.bct_t) & (j.act_m == j.act_t)
    return j, ex.to_numpy(), px.to_numpy(), len(a), len(b)


def compare(book, my_flast, my_seq, th_flast, th_seq, day, nrec, want_seq):
    res = {'day': day, 'mbo_records': nrec,
           'unhandled': int(book.unhandled),
           'unknown_actions': json.dumps({str(k): v for k, v in book.unknown.items()}),
           'trades': len(book.trades), 'fills': len(book.fills)}

    j, ex, px, na, nb = agree(my_flast.frame(), th_flast.frame(), ['ts', 'seq'])
    n = len(j)
    res.update({'events_replay': na, 'events_mbp1': nb, 'compared': n,
                'unmatched_mbp1': nb - n})
    if n:
        res['exact_pct'] = float(ex.mean() * 100)
        res['price_pct'] = float(px.mean() * 100)
        res['mismatches'] = int((~ex).sum())
        tod = j.ts.to_numpy() % 86_400_000_000_000
        rth = (tod >= RTH_START_UTC_NS) & (tod < RTH_END_UTC_NS)
        res['rth_compared'] = int(rth.sum())
        res['rth_exact_pct'] = float(ex[rth].mean() * 100) if rth.any() else float('nan')
        bad = np.flatnonzero(~ex)
        res['first_bad_ts'] = (str(np.datetime64(int(j.ts.to_numpy()[bad[0]]), 'ns'))
                               if len(bad) else '')
        samples = [(str(np.datetime64(int(j.ts.iloc[i]), 'ns')), int(j.seq.iloc[i]),
                    tuple(int(j[f'{c}_m'].iloc[i]) for c in COLS[2:]),
                    tuple(int(j[f'{c}_t'].iloc[i]) for c in COLS[2:]))
                   for i in bad[:10]]
    else:
        samples = []
    del j, ex, px

    if want_seq:
        j, ex, px, na, nb = agree(my_seq.frame(), th_seq.frame(), ['seq'])
        if len(j):
            res['seq_align_exact_pct'] = float(ex.mean() * 100)
            res['seq_align_mismatches'] = int((~ex).sum())
            res['seq_align_compared'] = len(j)
    return res, samples


def fmt_px(p):
    return 'None' if p == UNDEF_PRICE else f'{p / 1e9:.2f}'


# --------------------------------------------------------------------------- #
def day_of(path):
    for part in os.path.basename(path).split('-'):
        d = part.split('.')[0]
        if len(d) == 8 and d.isdigit():
            return d
    return os.path.basename(path)


def main(argv):
    limit = None
    if '--limit' in argv:
        i = argv.index('--limit')
        limit = int(argv[i + 1])
        del argv[i:i + 2]
    want_seq = '--no-seq' not in argv
    argv = [a for a in argv if not a.startswith('--')]
    only = set(argv) or None

    mbo = {day_of(p): p for p in sorted(glob.glob(os.path.join(MBO_DIR, '*.mbo.dbn.zst')))}
    mbp1 = {}
    for d in (MBP1_DIR, LEGACY_MBP1_DIR):
        for p in sorted(glob.glob(os.path.join(d, '*.mbp-1.dbn.zst'))):
            mbp1.setdefault(day_of(p), p)

    days = [d for d in sorted(mbo) if d in mbp1 and (only is None or d in only)]
    missing = [d for d in sorted(mbo) if d not in mbp1 and (only is None or d in only)]
    print(f'{len(days)} session(s) with both feeds; {len(missing)} awaiting mbp-1')
    if missing:
        print(f'  missing: {" ".join(missing)}')
    print()

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    hdr = (f'{"day":<10}{"mbo recs":>13}{"events":>12}{"exact%":>12}{"bad":>7}'
           f'{"RTH exact%":>12}{"unhandled":>11}{"seq-align%":>12}{"secs":>8}')
    print(hdr)
    print('-' * len(hdr))

    for d in days:
        t0 = time.time()
        book, mf, msq, nrec = replay_mbo(mbo[d], limit, want_seq)
        max_ts = int(mf.a['ts'][:mf.n].max()) if (limit and mf.n) else None
        tf, tsq = load_mbp1(mbp1[d], max_ts, want_seq)
        res, samples = compare(book, mf, msq, tf, tsq, d, nrec, want_seq)
        res['secs'] = round(time.time() - t0, 1)
        rows.append(res)
        print(f'{d:<10}{res["mbo_records"]:>13,}{res["compared"]:>12,}'
              f'{res.get("exact_pct", float("nan")):>12.6f}'
              f'{res.get("mismatches", 0):>7,}'
              f'{res.get("rth_exact_pct", float("nan")):>12.6f}'
              f'{res["unhandled"]:>11,}'
              f'{res.get("seq_align_exact_pct", float("nan")):>12.4f}'
              f'{res["secs"]:>8.1f}')
        sys.stdout.flush()

        if samples:
            print(f'  first {len(samples)} mismatching events:')
            for ts, seq, mv, tv in samples:
                for tag, v in (('mine', mv), ('mbp1', tv)):
                    print(f'    {ts if tag == "mine" else "":<32}'
                          f'{seq if tag == "mine" else "":>11}{tag:>6}'
                          f'{fmt_px(v[0]):>10}{v[1]:>7}{v[2]:>6} |'
                          f'{fmt_px(v[3]):>10}{v[4]:>7}{v[5]:>6}')
        del book, mf, msq, tf, tsq
        gc.collect()

    if not rows:
        return

    df = pd.DataFrame(rows)
    out = os.path.join(OUT_DIR, 'recon_accuracy.csv')
    df.to_csv(out, index=False)

    print('\n=== summary across sessions (F_LAST alignment) ===')
    print(f'  sessions compared      : {len(df)}')
    print(f'  book events compared   : {df.compared.sum():,}')
    print(f'  total mismatches       : {df.mismatches.sum():,}')
    print(f'  exact top-of-book      : {df.exact_pct.mean():.6f}%  '
          f'(min {df.exact_pct.min():.6f}%)')
    print(f'  RTH exact              : {df.rth_exact_pct.mean():.6f}%  '
          f'(min {df.rth_exact_pct.min():.6f}%)')
    print(f'  unmatched mbp-1 events : {df.unmatched_mbp1.sum():,}')
    print(f'  total unhandled events : {df.unhandled.sum():,}')
    print(f'  sessions 100% exact    : {(df.mismatches == 0).sum()} / {len(df)}')
    if 'seq_align_exact_pct' in df:
        print(f'\n  for reference, sequence-level alignment (compare_bbo.py) reports '
              f'{df.seq_align_exact_pct.mean():.4f}% -- the gap is a packet-boundary '
              f'artifact of that join, not a book error.')
    print(f'\nwrote {out}')


if __name__ == '__main__':
    main(sys.argv[1:])
