#!/usr/bin/env python3
"""Per-strategy statistics from as.py's backtest_daily_results.csv.

For each (period, fair value, max inventory): days, mean PnL per day before and
after fees (index points, x$50 for dollars), t-stat over days, share of
positive days after fees, and trades per day. Periods: 2025 (Jan-Feb, ESH5),
2026 (Aug-Sep, front month by daily volume), and both together.

Usage:  python3 -B as_results.py            -> diag_out/as_results.csv
"""
import os

import numpy as np
import pandas as pd

ROOT = '/Volumes/T9/Market_making'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diag_out')


def stats(g):
    n = len(g)
    out = {'days': n}
    for col, name in (('pnl', 'pnl'), ('pnl after fees', 'net')):
        m, s = g[col].mean(), g[col].std(ddof=1)
        out[f'{name}/day'] = m
        out[f'{name} t'] = m / (s / np.sqrt(n)) if n > 1 and s > 0 else np.nan
    out['positive days (net)'] = (g['pnl after fees'] > 0).mean()
    out['trades/day'] = g['trade count'].mean()
    return pd.Series(out)


def main():
    d = pd.read_csv(f'{ROOT}/backtest_daily_results.csv', dtype={'date': str})
    d['period'] = np.where(d['date'].str.startswith('2025'), '2025 Jan-Feb', '2026 Aug-Sep')
    both = d.assign(period='all')
    rows = (pd.concat([d, both])
            .groupby(['period', 'fair value', 'max inventory'])
            .apply(stats, include_groups=False)
            .reset_index())
    os.makedirs(OUT, exist_ok=True)
    rows.to_csv(os.path.join(OUT, 'as_results.csv'), index=False)
    pd.set_option('display.width', 200)
    print(rows.round(3).to_string(index=False))


if __name__ == '__main__':
    main()
