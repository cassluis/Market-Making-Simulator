"""Stoikov micro-price vs mid vs weighted-mid, across forecast horizons.

Why this file exists rather than calling mm_sim.Stoikov directly: that class is
Stoikov's original equity-tick code and hardcodes a 1-cent tick in TWO places --
`prep_data_sym` (ticksize=0.01, whose spread filter drops 100% of ES rows) and
`estimate` (K = +/-0.01, +/-0.005, the set of possible mid-price moves). ES ticks
at 0.25, so both must be scaled or the estimator is silently wrong by 25x.

Everything else is mm_sim's logic unchanged. Passing ticksize=0.25 reproduces the
g6_max stored in results/fairvalue_by_day.csv, which confirms the parameterisation
matches what generated those numbers.

Usage:  python microprice_horizons.py
"""

import os
import gc
import sys
import glob

import numpy as np
import pandas as pd
from scipy.linalg import block_diag

sys.path.insert(0, r'F:/market_making')
from analysis import load_day, compute_G6

FEATURE_DIR = r'F:/market_making/features'
OUT = r'F:/market_making/results/microprice_horizons.csv'

N_IMB, N_SPREAD, DT = 10, 1, 1
TICK = 0.25                                  # ES
HORIZONS = [1, 5, 10, 25, 50, 100, 250, 500, 1000]
MS_PER_ROW = 1.947                           # impact_by_day.csv: 100 rows = 194.7 ms


def prep_data_sym(T, n_imb, dt, n_spread, ticksize):
    """mm_sim.Stoikov.prep_data_sym with the tick size passed in."""
    T['spread'] = np.round((T['ask'] - T['bid']) / ticksize) * ticksize
    T['mid'] = (T['bid'] + T['ask']) / 2
    T = T.loc[(T.spread <= n_spread * ticksize) & (T.spread > 0)]
    T['imb'] = T['bs'] / (T['bs'] + T['as'])
    T['imb_bucket'] = pd.qcut(T['imb'], n_imb, labels=False)
    T['next_mid'] = T['mid'].shift(-dt)
    T['next_spread'] = T['spread'].shift(-dt)
    T['next_time'] = T['time'].shift(-dt)
    T['next_imb_bucket'] = T['imb_bucket'].shift(-dt)
    T['dM'] = np.round((T['next_mid'] - T['mid']) / ticksize * 2) * ticksize / 2
    T = T.loc[(T.dM <= ticksize * 1.1) & (T.dM >= -ticksize * 1.1)]
    T2 = T.copy(deep=True)
    T2['imb_bucket'] = n_imb - 1 - T2['imb_bucket']
    T2['next_imb_bucket'] = n_imb - 1 - T2['next_imb_bucket']
    T2['dM'] = -T2['dM']
    T2['mid'] = -T2['mid']
    T3 = pd.concat([T, T2])
    T3.index = pd.RangeIndex(len(T3.index))
    return T3


def estimate(T, n_imb, n_spread, ticksize):
    """mm_sim.Stoikov.estimate with K scaled to the instrument's tick."""
    no_move = T[T['dM'] == 0]
    no_move_counts = no_move.pivot_table(
        index=['next_imb_bucket'], columns=['spread', 'imb_bucket'],
        values='time', fill_value=0, aggfunc='count').unstack()
    Q_counts = np.resize(np.array(no_move_counts[0:(n_imb * n_imb)]), (n_imb, n_imb))
    for i in range(1, n_spread):
        Qi = np.resize(np.array(no_move_counts[(i * n_imb * n_imb):(i + 1) * (n_imb * n_imb)]),
                       (n_imb, n_imb))
        Q_counts = block_diag(Q_counts, Qi)

    move_counts = T[T['dM'] != 0].pivot_table(
        index=['dM'], columns=['spread', 'imb_bucket'],
        values='time', fill_value=0, aggfunc='count').unstack()
    R_counts = np.resize(np.array(move_counts), (n_imb * n_spread, 4))
    T1 = np.concatenate((Q_counts, R_counts), axis=1).astype(float)
    for i in range(n_imb * n_spread):
        T1[i] = T1[i] / T1[i].sum()
    Q = T1[:, 0:(n_imb * n_spread)]
    R1 = T1[:, (n_imb * n_spread):]

    # the four possible mid moves: -/+ one tick and -/+ half a tick
    K = np.array([-ticksize, -ticksize / 2, ticksize / 2, ticksize])

    move_counts = T[T['dM'] != 0].pivot_table(
        index=['spread', 'imb_bucket'], columns=['next_spread', 'next_imb_bucket'],
        values='time', fill_value=0, aggfunc='count')
    R2_counts = np.resize(np.array(move_counts), (n_imb * n_spread, n_imb * n_spread))
    T2 = np.concatenate((Q_counts, R2_counts), axis=1).astype(float)
    for i in range(n_imb * n_spread):
        T2[i] = T2[i] / T2[i].sum()
    R2 = T2[:, (n_imb * n_spread):]

    eye = np.eye(n_imb * n_spread)
    G1 = np.dot(np.dot(np.linalg.inv(eye - Q), R1), K)
    B = np.dot(np.linalg.inv(eye - Q), R2)
    return G1, B


def main():
    rows = []
    for p in sorted(g for g in glob.glob(os.path.join(FEATURE_DIR, '*.parquet'))
                    if '_trades' not in g):
        day = os.path.basename(p).split('.')[0]
        rth = load_day(p)
        if rth is None:
            continue
        T = rth.rename(columns={'bid_px': 'bid', 'ask_px': 'ask',
                                'bid_sz': 'bs', 'ask_sz': 'as', 'ts': 'time'})
        try:
            T3 = prep_data_sym(T, N_IMB, DT, N_SPREAD, TICK)
            G1, B = estimate(T3, N_IMB, N_SPREAD, TICK)
            G6 = compute_G6(G1, B)
            real = T3.iloc[:len(T3) // 2].copy()
            real['micro'] = real['mid'] + G6[real.imb_bucket.astype(int).values]
            for h in HORIZONS:
                fut = real.mid.shift(-h)
                v = fut.notna()
                rows.append({'day': day, 'h': h,
                             'mid': ((fut - real.mid)[v] ** 2).mean(),
                             'wmid': ((fut - real.wmid)[v] ** 2).mean(),
                             'micro': ((fut - real.micro)[v] ** 2).mean(),
                             'g6_max': float(np.max(np.abs(G6)))})
            print(f'  {day}: g6_max {np.max(np.abs(G6)):.6f}  '
                  f'implied_lambda {np.max(np.abs(G6)) / (TICK / 2):.3f}', flush=True)
        except Exception as exc:
            print(f'  {day}: {type(exc).__name__}: {exc}', flush=True)
        del rth, T
        gc.collect()

    d = pd.DataFrame(rows)
    d.to_csv(OUT, index=False)
    n = d.day.nunique()
    g = d.groupby('h').apply(lambda x: pd.Series({
        'approx_ms': x.name * MS_PER_ROW,
        'micro<mid': f'{(x.micro < x["mid"]).sum()}/{n}',
        'wmid<mid': f'{(x.wmid < x["mid"]).sum()}/{n}',
        'micro<wmid': f'{(x.micro < x.wmid).sum()}/{n}',
        'micro_MSEred%': (1 - x.micro / x['mid']).mean() * 100,
        'wmid_MSEred%': (1 - x.wmid / x['mid']).mean() * 100,
    }), include_groups=False)
    print(f'\nEstimator vs future mid, {n} sessions, ES tick 0.25')
    print(g.round(2).to_string())
    print(f'\nwrote {OUT}')


if __name__ == '__main__':
    main()
