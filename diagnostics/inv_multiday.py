#!/usr/bin/env python3
"""Pool as_diag.py --inv daily outputs across the Jan-Feb 2025 sweep.

Reads diag_out/inv_summary_<date>.csv and inv_paths_<date>.npz for every test date
in multiday.DATES and pools them the way multiday.py does (daily-PnL mean / t over
days; per-fill realized spread pooled over fills with day-clustered SEs).
Writes only to ./diag_out:

  inv_multiday_summary.csv   one row per (variant, gamma, fair value, cap)
  inv_multiday_daily.csv     the concatenated daily summaries (+ inventory at 15:30)
  inv_port_check.csv         ctrl rows vs the sweep's summary_<date>.csv ('all' rows)

PnL is in ES index points (x$50 for dollars).
"""
import math
import os

import numpy as np
import pandas as pd

import multiday
from multiday import DATES, FEE_PTS, mean_se, pooled

OUT = multiday.OUT
CHECK_AT = ('15:30', '15:55')                # ET times to sample inventory at
SKEW_AT = ('09:35', '10:00', '12:00', '15:00', '15:55')


def sign_p(k, n):
    """Two-sided exact binomial p for k of n at p = 1/2."""
    if n == 0:
        return np.nan
    tail = sum(math.comb(n, i) for i in range(min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def et_index(ts_ns, hhmm):
    """First index whose timestamp is at or after hhmm New York time."""
    t = pd.to_datetime(ts_ns, unit='ns', utc=True).tz_convert('America/New_York')
    target = t[0].normalize() + pd.Timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
    return min(int(np.searchsorted(t.asi8, target.value)), len(t) - 1)


def load():
    frames, skew_rows, missing = [], [], []
    for _, d in DATES:
        p = os.path.join(OUT, f'inv_summary_{d}.csv')
        if not os.path.exists(p):
            missing.append(d)
            continue
        s = pd.read_csv(p)
        s.insert(0, 'date', d)
        z = np.load(os.path.join(OUT, f'inv_paths_{d}.npz'))
        inv, ts = z['inv'], z['sample_ts']
        for hhmm in CHECK_AT:
            s[f'inv {hhmm}'] = inv[:, et_index(ts, hhmm)]
        # sigma_px^2 * T_t per sample (skew per contract = gamma * this)
        st, s2t = z['sample_times'], z['sig2T']
        ok = ~np.isnan(s2t)
        row = {'date': d, 'median': np.median(s2t[ok]), 'mean': s2t[ok].mean(),
               'p90': np.percentile(s2t[ok], 90),
               'median log units': np.median(s2t[ok]) / z['mid'].mean() ** 2}
        for hhmm in SKEW_AT:
            row[hhmm] = s2t[et_index(st, hhmm)]
        skew_rows.append(row)
        frames.append(s)
    if missing:
        print(f'missing inv summaries (skipped): {missing}')
    return pd.concat(frames, ignore_index=True), pd.DataFrame(skew_rows), missing


def port_check(daily):
    """ctrl must reproduce config 'all' of the sweep exactly."""
    rows = []
    for d in daily['date'].unique():
        p = os.path.join(OUT, f'summary_{d}.csv')
        if not os.path.exists(p):
            continue
        sw = pd.read_csv(p)
        sw = sw[(sw['config'] == 'all') & sw['fair value'].isin(['mid', 'wmid'])
                & sw['max inventory'].isin([100, 500])]
        c = daily[(daily['date'] == d) & (daily['variant'] == 'ctrl')]
        m = c.merge(sw, on=['fair value', 'max inventory'], suffixes=('', ' sweep'))
        for col in ('pnl', 'trade count', 'inv at close', 'realized spread 60s'):
            m[f'd {col}'] = m[col] - m[f'{col} sweep']
        rows.append(m[['date', 'fair value', 'max inventory'] + [c_ for c_ in m if c_.startswith('d ')]])
    return pd.concat(rows, ignore_index=True)


def table(daily):
    d = daily.copy()
    d['n'] = d['trade count']
    d['realized 60s'] = (d['realized spread 60s'] * d['n']).where(d['n'] > 0, 0.0)
    d['realized 60s net'] = d['realized 60s'] - FEE_PTS * d['n']
    d['markout 60s'] = (d['markout 60s'] * d['n']).where(d['n'] > 0, 0.0)
    rows = []
    keys = ['variant', 'gamma', 'fair value', 'max inventory']
    for (v, g, fv, cap), x in d.groupby(keys, sort=False):
        pm, ps, pt = mean_se(x['pnl'])
        fm, fs, ft = mean_se(x['pnl_fees'])
        bs = x['buys'] - x['sells']
        bm, _, bt = mean_se(bs)
        im, _, it = mean_se(x['inv at close'])
        long_, short = int((x['inv at close'] > 0).sum()), int((x['inv at close'] < 0).sum())
        row = {'variant': v, 'gamma': g, 'k': x['k'].iloc[0], 'fair value': fv, 'cap': cap,
               'days': len(x),
               'pnl mean': pm, 'pnl se': ps, 'pnl t': pt,
               'pnl_fees mean': fm, 'pnl_fees se': fs, 'pnl_fees t': ft,
               'share pnl>0': (x['pnl'] > 0).mean(), 'share pnl_fees>0': (x['pnl_fees'] > 0).mean(),
               'buys': int(x['buys'].sum()), 'sells': int(x['sells'].sum()),
               'buys-sells/day': bm, 'buys-sells t': bt,
               'days long': long_, 'days short': short,
               'days flat': int((x['inv at close'] == 0).sum()),
               'sign p': sign_p(long_, long_ + short),
               'inv at close mean': im, 'inv at close t': it,
               'mean |inv|': x['mean |inv|'].mean(), 'mean inv': x['mean inv'].mean(),
               'share time long': x['share time long'].mean(),
               'share time short': x['share time short'].mean(),
               'max |inv|': int(x['max |inv|'].max()), 'time at cap': x['time at cap'].mean(),
               'fills/day': x['n'].mean(), 'taker fills/day': x['taker fills'].mean(),
               'spread/day': x['spread'].mean(), 'carry_pre/day': x['carry_pre'].mean(),
               'carry_post/day': x['carry_post'].mean(), 'liq/day': x['liq'].mean()}
        for hhmm in CHECK_AT:
            q = x[f'inv {hhmm}']
            row[f'days long {hhmm}'] = int((q > 0).sum())
            row[f'days short {hhmm}'] = int((q < 0).sum())
            row[f'mean inv {hhmm}'] = q.mean()
        for k in ('realized 60s', 'realized 60s net', 'markout 60s'):
            m, se, n, G = pooled(x, k)
            row[k], row[k + ' t'] = m, m / se
        sp = x[x['n'] > 0]
        row['spread/fill'] = sp['spread'].sum() / sp['n'].sum()
        rows.append(row)
    return pd.DataFrame(rows)


def fmt_g(g):
    return f'{g:.1e}'.replace('e-0', 'e-')


def show(tab, skew, cap):
    t = tab[tab['cap'] == cap]
    med = skew['median'].median()
    print(f'\n### cap {cap}')
    hdr = ('| variant | γ | fv | skew/contract (median) | PnL/day (t) | after fees (t) | buys | sells '
           '| days long/short @16:00 | inv@16:00 mean (t) | mean abs(inv) | realized 60s (t) | net of fee (t) '
           '| fills/day | taker/day |')
    print(hdr)
    print('|' + '---|' * (hdr.count('|') - 1))
    for _, r in t.iterrows():
        if r['variant'] == 'ctrl':        # as.py: sigma in log units
            sk = f"{r['gamma'] * skew['median log units'].median():.0e}"
        else:
            sk = f"{r['gamma'] * med:.3f}"
        print(f"| {r['variant']} | {fmt_g(r['gamma'])} | {r['fair value']} | {sk} "
              f"| {r['pnl mean']:+.1f} ({r['pnl t']:+.2f}) | {r['pnl_fees mean']:+.1f} ({r['pnl_fees t']:+.2f}) "
              f"| {r['buys']:,} | {r['sells']:,} | {r['days long']}/{r['days short']} "
              f"| {r['inv at close mean']:+.1f} ({r['inv at close t']:+.2f}) | {r['mean |inv|']:.2f} "
              f"| {r['realized 60s']:+.4f} ({r['realized 60s t']:+.2f}) "
              f"| {r['realized 60s net']:+.4f} ({r['realized 60s net t']:+.2f}) "
              f"| {r['fills/day']:.0f} | {r['taker fills/day']:.1f} |")


def main():
    daily, skew, missing = load()
    print(f"{daily['date'].nunique()} days loaded")
    daily.to_csv(os.path.join(OUT, 'inv_multiday_daily.csv'), index=False)

    pc = port_check(daily)
    pc.to_csv(os.path.join(OUT, 'inv_port_check.csv'), index=False)
    dcols = [c for c in pc if c.startswith('d ')]
    print('port check (ctrl - sweep all), max |diff|:', pc[dcols].abs().max().to_dict(),
          f'over {pc["date"].nunique()} days')

    print('\nsigma_px^2 * T_t per RTH sample, pt^2 (skew per contract = gamma x this):')
    print(skew.drop(columns='date').describe().loc[['mean', '50%', 'min', 'max']].round(0).to_string())

    tab = table(daily)
    tab.to_csv(os.path.join(OUT, 'inv_multiday_summary.csv'), index=False)

    pd.set_option('display.width', 250)
    pd.set_option('display.max_columns', 60)
    for cap in sorted(tab['cap'].unique(), reverse=True):
        show(tab, skew, cap)
    print('\n### inventory before the close, PnL split, cap 500')
    t = tab[tab['cap'] == 500]
    cols = ['variant', 'gamma', 'fair value', 'buys-sells/day', 'buys-sells t', 'sign p',
            'mean inv', 'share time long', 'share time short', 'max |inv|', 'time at cap',
            'days long 15:30', 'days short 15:30', 'mean inv 15:30',
            'days long 15:55', 'days short 15:55', 'mean inv 15:55',
            'spread/fill', 'markout 60s', 'spread/day', 'carry_pre/day', 'liq/day']
    print(t[cols].round(4).to_string(index=False))


if __name__ == '__main__':
    main()
