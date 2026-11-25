#!/usr/bin/env python3
"""Simulator-free check: does the ES tape itself hit a mid-centred bid more often
than the matching ask?

For every RTH second t the mid strategy (as.quotes at zero inventory) quotes
  q_b = floor((mid_t - h)/tick)*tick,  q_a = ceil((mid_t + h)/tick)*tick,
  h = log(1 + gamma/k)/gamma  (the vol^2*gamma*T_t term is ~1e-7 pt, ignored)
with mid_t sampled exactly as as.py does (last book mid in (t-1s, t]). Using only
features/<date>_trades.parquet, count the seconds in which the next second's
trades reach the bid (any print <= q_b) or the ask (any print >= q_a), the
volume printed there, and plain 1 s mid moves of >= 1 pt each way.

Compares those per-day counts with the sweep's fills (all|mid|500, before 16:00).
Writes diag_out/market_asym.csv.
"""
import os
import sys

import numpy as np
import pandas as pd

import as_diag

ROOT = as_diag.ROOT
sys.path.insert(0, ROOT)
import orderbook                                    # noqa: E402

CFG, TICK = as_diag.CFG, as_diag.TICK
H = np.log(1 + CFG.gamma / CFG.k) / CFG.gamma
DATES = [d for _, d in __import__('multiday').DATES]


def day(d):
    lob = orderbook.load(f'{ROOT}/features/{d}.parquet')
    s = lob[['time', 'mid']].copy()
    s['time'] = pd.to_datetime(s['time'], unit='ns').dt.ceil(CFG.sample_freq)
    s = s.groupby('time')['mid'].last()
    st = s.index.to_numpy('datetime64[ns]').astype(np.int64)
    mid = s.to_numpy()
    qb = np.floor((mid - H) / TICK) * TICK
    qa = np.ceil((mid + H) / TICK) * TICK

    tr = pd.read_parquet(f'{ROOT}/features/{d}_trades.parquet')
    tr = tr[(tr['ts'] > st[0]) & (tr['ts'] <= st[-1])]
    k = np.searchsorted(st, tr['ts'].to_numpy(), side='left') - 1   # quote live in (st[k], st[k+1]]
    px = tr['price'].to_numpy() / 1e9
    sz = tr['size'].to_numpy()
    at_b, at_a = px <= qb[k], px >= qa[k]
    n = len(st)
    hit_b = np.bincount(k[at_b], minlength=n) > 0
    hit_a = np.bincount(k[at_a], minlength=n) > 0
    vol_b = np.bincount(k[at_b], weights=sz[at_b], minlength=n)
    vol_a = np.bincount(k[at_a], weights=sz[at_a], minlength=n)
    dm = np.diff(mid)
    sell = tr['side'].to_numpy() == 'A'
    return {
        'date': d, 'rth_move': mid[-1] - mid[0],
        'sec_hit_bid': int(hit_b.sum()), 'sec_hit_ask': int(hit_a.sum()),
        'vol_at_bid': int(vol_b.sum()), 'vol_at_ask': int(vol_a.sum()),
        'drops_1pt': int((dm <= -1).sum()), 'rises_1pt': int((dm >= 1).sum()),
        'drops_2pt': int((dm <= -2).sum()), 'rises_2pt': int((dm >= 2).sum()),
        'sell_aggr_vol': int(sz[sell].sum()), 'buy_aggr_vol': int(sz[~sell].sum()),
    }


def sim_fills(d, label='all|mid|500'):
    ss = pd.read_csv(os.path.join(as_diag.OUT, f'summary_{d}.csv'))
    lab = ss['config'] + '|' + ss['fair value'] + '|' + ss['max inventory'].astype(str)
    i = int(np.flatnonzero(lab == label)[0])
    f = pd.read_parquet(os.path.join(as_diag.OUT, f'fills_{d}.parquet'))
    f = f[(f['s'] == i) & f['pre']]
    return int((f['sign'] > 0).sum()), int((f['sign'] < 0).sum())


def main():
    rows = []
    for d in DATES:
        r = day(d)
        r['sim_buys'], r['sim_sells'] = sim_fills(d)
        rows.append(r)
        print(r, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(as_diag.OUT, 'market_asym.csv'), index=False)
    pd.set_option('display.width', 250)
    print(df.to_string(index=False))
    tot = df.drop(columns=['date']).sum()
    print('\ntotals\n' + tot.to_string())
    for a, b in (('sec_hit_bid', 'sec_hit_ask'), ('vol_at_bid', 'vol_at_ask'),
                 ('drops_1pt', 'rises_1pt'), ('drops_2pt', 'rises_2pt'), ('sim_buys', 'sim_sells')):
        x = df[a] - df[b]
        print(f'{a:>12s} - {b:<12s} total {x.sum():+8.0f}  ratio {df[a].sum() / df[b].sum():.3f}'
              f'  days >0: {(x > 0).sum()}/{len(x)}  corr with sim buys-sells '
              f'{np.corrcoef(x, df.sim_buys - df.sim_sells)[0, 1]:+.2f}')


if __name__ == '__main__':
    main()
