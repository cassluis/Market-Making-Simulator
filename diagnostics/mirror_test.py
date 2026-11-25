#!/usr/bin/env python3
"""Mirror test for the long-inventory bias: is it the simulator or the tape?

Reflect one day's MBO tape through a price C (every price p -> C - p, bid <-> ask)
and run as_diag.simulate() -- unchanged -- on it. If the simulator treats bids and
asks alike, the mirrored run is the original run upside down: every buy becomes a
sell at C - px at the same timestamp, and inventory flips sign. Any asymmetry in
quotes(), the fill/queue logic or OrderBook shows up as a mismatch.

The identity pass goes through the same record shim, so it also checks the shim
against the sweep's diag_out/fills_<date>.parquet.

Fair values are reflected the same way (mid' = C - mid, wmid' = C - wmid: wmid is
exactly antisymmetric under bs <-> as). Volatility is kept as is; it only enters
via vol^2*gamma*T_t, ~1e-7 pt.

Usage (run from outside T9):
  python3 -B mirror_test.py --cal 20250122 --date 20250123 --mode id
  python3 -B mirror_test.py --cal 20250122 --date 20250123 --mode mirror
  python3 -B mirror_test.py --date 20250123 --compare
  python3 -B mirror_test.py --summary        # every mirrored day vs the sweep -> mirror_check.csv
"""
import argparse
import collections
import os
import time

import numpy as np
import pandas as pd
import databento as db
import databento_dbn as dbn

import as_diag

OUT = as_diag.OUT
C = 12_000.0                      # mirror price, on the tick grid: mirrored ES ~6,000
K = int(round(C * 1e9))
UNDEF = 2**63 - 1
CONFIGS = ['base', 'all']
FVS = ['mid', 'wmid']
CAPS = [100, 500]


class Rec:
    __slots__ = ('ts_event', 'action', 'order_id', 'price', 'size', 'side', 'flags')


def records(path, mirror, counts):
    flip = {'B': dbn.Side.ASK, 'A': dbn.Side.BID, 'N': dbn.Side.NONE}
    for rec in db.DBNStore.from_file(path):
        r = Rec()
        r.ts_event, r.action, r.order_id = rec.ts_event, rec.action, rec.order_id
        r.size, r.flags = rec.size, rec.flags
        sd, p = rec.side, rec.price
        counts[(rec.action.value, sd.value)] += 1
        if mirror:
            r.price = p if p == UNDEF else K - p
            r.side = flip[sd.value]
        else:
            r.price, r.side = p, sd
        yield r


class Shim:
    """Stands in for the databento module inside as_diag."""
    def __init__(self, mirror, counts):
        outer = self

        class DBNStore:
            @staticmethod
            def from_file(path):
                return records(path, outer.mirror, outer.counts)
        self.DBNStore, self.mirror, self.counts = DBNStore, mirror, counts


def make_strats(fvs, mirror):
    strats = []
    for c in CONFIGS:
        flags = as_diag.CONFIGS[c][0]
        for cap in CAPS:
            for fv in FVS:
                series = C - fvs[fv] if mirror else fvs[fv]
                strats.append(as_diag.Strat(len(strats), c, fv, fv, series, cap, flags))
    return strats


def run(args):
    t0 = time.time()
    sample_times, vols, fvs, _, _ = as_diag.build_fair_values(args.cal, args.date)
    print(f'fair values {time.time() - t0:.0f}s', flush=True)
    strats = make_strats(fvs, args.mode == 'mirror')
    counts = collections.Counter()
    as_diag.db = Shim(args.mode == 'mirror', counts)
    mbo = f'{as_diag.ROOT}/ES_MBO/glbx-mdp3-{args.date}.mbo.dbn.zst'
    fills, _ = as_diag.simulate(mbo, sample_times, vols, strats, args.limit)
    tag = f'{args.date}_{args.mode}' + (f'_limit{args.limit}' if args.limit else '')
    f = pd.DataFrame(fills)
    f['label'] = [f'{strats[i].config}|{strats[i].fv_name}|{strats[i].max_inv}' for i in f['s']]
    f.to_parquet(os.path.join(OUT, f'mirror_fills_{tag}.parquet'), index=False)
    rows = [{'label': f'{s.config}|{s.fv_name}|{s.max_inv}', 'inv_close': s.inv_close,
             'cash': s.cash, 'n_fills': s.n_fills} for s in strats]
    pd.DataFrame(rows).to_csv(os.path.join(OUT, f'mirror_summary_{tag}.csv'), index=False)
    pd.Series({f'{a}|{sd}': n for (a, sd), n in sorted(counts.items())}).to_csv(
        os.path.join(OUT, f'mirror_counts_{tag}.csv'))
    print(f'wrote {tag}  ({time.time() - t0:.0f}s)')


def sweep(date):
    """The sweep's own fills and summary (as_diag.simulate on the raw tape), relabelled."""
    sw = pd.read_parquet(os.path.join(OUT, f'fills_{date}.parquet'))
    ss = pd.read_csv(os.path.join(OUT, f'summary_{date}.csv'))
    lab = (ss['config'] + '|' + ss['fair value'] + '|' + ss['max inventory'].astype(str))
    sw['label'] = lab.to_numpy()[sw['s']]
    ss['label'] = lab
    return sw, ss.rename(columns={'inv at close': 'inv_close'})


def compare(args):
    sfx = f'_limit{args.limit}' if args.limit else ''
    fm = pd.read_parquet(os.path.join(OUT, f'mirror_fills_{args.date}_mirror{sfx}.parquet'))
    sm = pd.read_csv(os.path.join(OUT, f'mirror_summary_{args.date}_mirror{sfx}.csv'))
    id_path = os.path.join(OUT, f'mirror_fills_{args.date}_id{sfx}.parquet')
    if os.path.exists(id_path):
        fi = pd.read_parquet(id_path)
        si = pd.read_csv(os.path.join(OUT, f'mirror_summary_{args.date}_id{sfx}.csv'))
    else:                                   # no identity pass: the sweep is the reference
        fi, si = sweep(args.date)
        fi, si = fi[fi['label'].isin(sm['label'])], si[si['label'].isin(sm['label'])]
        si = si.set_index('label').loc[sm['label']].reset_index()

    # identity pass vs the sweep's own fills (same simulate, no shim)
    if not args.limit and os.path.exists(id_path):
        sw, _ = sweep(args.date)
        for L, g in fi.groupby('label'):
            h = sw[sw['label'] == L]
            same = (len(g) == len(h) and (g['ts'].to_numpy() == h['ts'].to_numpy()).all()
                    and (g['sign'].to_numpy() == h['sign'].to_numpy()).all()
                    and (g['px'].to_numpy() == h['px'].to_numpy()).all())
            print(f'identity == sweep  {L:16s} {same}')

    print(f'\n{"strategy":16s} {"buys":>6s} {"sells":>6s} {"inv@16":>7s} | mirror:'
          f' {"buys":>6s} {"sells":>6s} {"inv@16":>7s} | fill-by-fill mirror')
    for L in si['label']:
        a, b = fi[fi['label'] == L], fm[fm['label'] == L]
        ap, bp = a[a['pre']], b[b['pre']]
        exact = (len(a) == len(b) and (a['ts'].to_numpy() == b['ts'].to_numpy()).all()
                 and (a['sign'].to_numpy() == -b['sign'].to_numpy()).all()
                 and (a['px'].to_numpy() == K - b['px'].to_numpy()).all())
        ii = int(si.loc[si['label'] == L, 'inv_close'].iloc[0])
        im = int(sm.loc[sm['label'] == L, 'inv_close'].iloc[0])
        print(f'{L:16s} {(ap.sign > 0).sum():6d} {(ap.sign < 0).sum():6d} {ii:7d} |'
              f'         {(bp.sign > 0).sum():6d} {(bp.sign < 0).sum():6d} {im:7d} | {exact}')


def summary_all():
    """Every day with a mirrored run vs the sweep: is it an exact mirror, fill by fill?"""
    from multiday import DATES
    rows = []
    for _, d in DATES:
        p = os.path.join(OUT, f'mirror_fills_{d}_mirror.parquet')
        if not os.path.exists(p):
            print(f'{d}: no mirrored run')
            continue
        fm = pd.read_parquet(p)
        sm = pd.read_csv(os.path.join(OUT, f'mirror_summary_{d}_mirror.csv')).set_index('label')
        sw, ss = sweep(d)
        ss = ss.set_index('label')
        for L in sm.index:
            a, b = sw[sw['label'] == L], fm[fm['label'] == L]
            exact = bool(len(a) == len(b) and (a['ts'].to_numpy() == b['ts'].to_numpy()).all()
                         and (a['sign'].to_numpy() == -b['sign'].to_numpy()).all()
                         and (a['px'].to_numpy() == K - b['px'].to_numpy()).all()
                         and ss.loc[L, 'inv_close'] == -sm.loc[L, 'inv_close'])
            ap_, bp = a[a['pre']], b[b['pre']]
            rows.append({'date': d, 'label': L, 'exact_mirror': exact,
                         'buys': int((ap_['sign'] > 0).sum()), 'sells': int((ap_['sign'] < 0).sum()),
                         'inv_close': int(ss.loc[L, 'inv_close']),
                         'mirror_buys': int((bp['sign'] > 0).sum()),
                         'mirror_sells': int((bp['sign'] < 0).sum()),
                         'mirror_inv_close': int(sm.loc[L, 'inv_close'])})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'mirror_check.csv'), index=False)
    g = df.groupby('label').agg(days=('date', 'size'), exact=('exact_mirror', 'sum'),
                                buys=('buys', 'sum'), sells=('sells', 'sum'),
                                long_days=('inv_close', lambda x: int((x > 0).sum())),
                                m_buys=('mirror_buys', 'sum'), m_sells=('mirror_sells', 'sum'),
                                m_long_days=('mirror_inv_close', lambda x: int((x > 0).sum())),
                                m_short_days=('mirror_inv_close', lambda x: int((x < 0).sum())))
    print(g.to_string())
    print(f'\nexact mirror on {df["exact_mirror"].sum()}/{len(df)} (day, strategy) pairs')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cal')
    ap.add_argument('--date')
    ap.add_argument('--mode', choices=['id', 'mirror'])
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--compare', action='store_true')
    ap.add_argument('--summary', action='store_true', help='all days with a mirrored run')
    args = ap.parse_args()
    if args.summary:
        summary_all()
    elif args.compare:
        compare(args)
    else:
        run(args)


if __name__ == '__main__':
    main()
