#!/usr/bin/env python3
"""Pool as_diag.py's daily outputs across the Jan-Feb 2025 sweep.

Reads diag_out/summary_<date>.csv for every test date in as.py's `dates` list
(missing days are reported and skipped). Writes only to ./diag_out:

  multiday_summary.csv    one row per (config, fair value, cap): daily-PnL mean / SE /
                          t / share positive, mean daily fill metrics, mean PnL split
  multiday_markouts.csv   pooled per-fill markouts, SEs clustered by day, and the
                          microprice-minus-mid / -wmid differences (see day_sums)
  multiday_carry_post.csv base microprice PnL per day with its carry_post part
  multiday_daily.csv      the concatenated daily summaries
  daily_pnl_all.png       daily and cumulative PnL by strategy, config 'all'

PnL is in ES index points (x$50 for dollars). Markout sign convention follows
as_diag.summarise: sign*(mid(t+h) - mid at fill), so negative = adverse selection.
"""
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt            # noqa: E402
import numpy as np                          # noqa: E402
import pandas as pd                         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'diag_out')

# as.py main()'s (calibration, test) pairs
DATES = [('20250106', '20250107'), ('20250107', '20250108'), ('20250108', '20250110'),
         ('20250110', '20250113'), ('20250113', '20250114'), ('20250114', '20250115'),
         ('20250115', '20250116'), ('20250116', '20250117'), ('20250117', '20250121'),
         ('20250121', '20250122'), ('20250122', '20250123'), ('20250123', '20250124'),
         ('20250124', '20250127'), ('20250127', '20250128'), ('20250128', '20250129'),
         ('20250129', '20250130'), ('20250130', '20250131'), ('20250131', '20250203'),
         ('20250203', '20250204'), ('20250204', '20250205'), ('20250205', '20250206')]
CONFIGS = ['base', 'all']
FVS = ['mid', 'wmid', 'microprice']
CAPS = [100, 500]
USD = 50
FEE_PTS = 1.20 / 50            # as_diag's per-contract, per-side fee in index points

SURFACE, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
SERIES = {'mid': '#2a78d6', 'wmid': '#eb6834', 'microprice': '#1baf7a'}   # slots 1-3


def mean_se(x):
    x = np.asarray(x, float)
    n = len(x)
    m = x.mean() if n else np.nan
    se = x.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return m, se, m / se if se else np.nan


def load_daily():
    frames, missing = [], []
    for _, d in DATES:
        p = os.path.join(OUT, f'summary_{d}.csv')
        if not os.path.exists(p):
            missing.append(d)
            continue
        s = pd.read_csv(p)
        s.insert(0, 'date', d)
        s['strat'] = np.arange(len(s))           # row order == Strat.i in as_diag
        frames.append(s)
    if missing:
        print(f'missing summaries (skipped): {missing}')
    return pd.concat(frames, ignore_index=True), missing


def daily_table(daily):
    rows = []
    for c in CONFIGS:
        for cap in CAPS:
            for fv in FVS:
                g = daily[(daily['config'] == c) & (daily['fair value'] == fv)
                          & (daily['max inventory'] == cap)]
                pm, ps, pt = mean_se(g['pnl'])
                fm, fs, ft = mean_se(g['pnl_fees'])
                rows.append({
                    'config': c, 'fair value': fv, 'fv series': g['fv series'].iloc[0],
                    'cap': cap, 'days': len(g),
                    'pnl mean': pm, 'pnl se': ps, 'pnl t': pt,
                    'pnl_fees mean': fm, 'pnl_fees se': fs, 'pnl_fees t': ft,
                    'pnl_fees mean $': fm * USD,
                    'share pnl>0': (g['pnl'] > 0).mean(),
                    'share pnl_fees>0': (g['pnl_fees'] > 0).mean(),
                    'fills/day': g['trade count'].mean(),
                    'spread/fill': g['spread/fill'].mean(),
                    'markout 1s': g['markout 1s'].mean(),
                    'markout 10s': g['markout 10s'].mean(),
                    'markout 60s': g['markout 60s'].mean(),
                    'realized spread 60s': g['realized spread 60s'].mean(),
                    'spread': g['spread'].mean(), 'carry_pre': g['carry_pre'].mean(),
                    'carry_post': g['carry_post'].mean(), 'liq': g['liq'].mean(),
                    'total pnl': g['pnl'].sum(), 'total pnl_fees': g['pnl_fees'].sum(),
                })
    return pd.DataFrame(rows)


# summary column holding each metric's per-day mean over that day's fills
METRICS = {'spread': 'spread/fill', 'markout 1s': 'markout 1s', 'markout 10s': 'markout 10s',
           'markout 60s': 'markout 60s', 'realized 60s': 'realized spread 60s'}
KEYS = list(METRICS) + ['realized 60s net']          # net = realized 60s - fee per fill


def day_sums(daily):
    """Per-day fill count and per-metric fill sums for base/all at caps 100/500.

    as_diag's per-day fill metrics are plain means over that day's fills (a fill-level
    recomputation from fills_<date>.parquet and the features mids matched them to 1e-16),
    so mean * count rebuilds each day's sum exactly. A pooled per-fill mean and its
    day-clustered SE depend on the fills only through these sums and counts, so pooling
    them is the fill-level answer without re-reading the features files.
    """
    d = daily[daily['config'].isin(CONFIGS) & daily['max inventory'].isin(CAPS)].copy()
    d['n'] = d['trade count']
    for k, col in METRICS.items():
        d[k] = (d[col] * d['n']).where(d['n'] > 0, 0.0)
    d['realized 60s net'] = d['realized 60s'] - FEE_PTS * d['n']
    return d[['date', 'config', 'fair value', 'max inventory', 'n', 'taker fills'] + KEYS]


def pooled(g, k):
    """Fill-weighted mean of k, SE clustered by day (CR1 small-sample factor G/(G-1))."""
    g = g[g['n'] > 0]
    n = g['n'].sum()
    m = g[k].sum() / n
    u = ((g[k] - g['n'] * m) / n).to_numpy()
    G = len(u)
    return m, np.sqrt(G / (G - 1) * (u ** 2).sum()), int(n), G


def pooled_diff(a, b, k):
    """Pooled mean(a) - mean(b), clustered by day: both fill sets share the day clusters."""
    ma, _, na, _ = pooled(a, k)
    mb, _, nb, _ = pooled(b, k)
    ua = pd.Series(((a[k] - a['n'] * ma) / na).to_numpy(), index=a['date'])
    ub = pd.Series(((b[k] - b['n'] * mb) / nb).to_numpy(), index=b['date'])
    u = ua.sub(ub, fill_value=0.0).to_numpy()           # a zero-fill day contributes 0
    G = len(set(a.loc[a['n'] > 0, 'date']) | set(b.loc[b['n'] > 0, 'date']))
    return ma - mb, np.sqrt(G / (G - 1) * (u ** 2).sum())


def markout_table(ds, daily):
    rows = []
    for c in CONFIGS:
        for cap in CAPS:
            sel = (ds['config'] == c) & (ds['max inventory'] == cap)
            groups = {fv: ds[sel & (ds['fair value'] == fv)] for fv in FVS}
            means = {fv: daily[(daily['config'] == c) & (daily['max inventory'] == cap)
                               & (daily['fair value'] == fv)].set_index('date')
                     for fv in FVS}
            for fv, g in groups.items():
                row = {'config': c, 'cap': cap, 'fair value': fv, 'compare': ''}
                for k in KEYS:
                    m, se, n, G = pooled(g, k)
                    row[k], row[k + ' se'], row[k + ' t'] = m, se, m / se
                row['n fills'], row['days'] = n, G
                rows.append(row)
            mp = groups['microprice']
            for ref in ('mid', 'wmid'):
                g = groups[ref]
                row = {'config': c, 'cap': cap, 'fair value': 'microprice',
                       'compare': f'minus {ref}'}
                for k in KEYS:
                    m, se = pooled_diff(mp, g, k)
                    row[k], row[k + ' se'], row[k + ' t'] = m, se, m / se
                row['n fills'] = int(mp['n'].sum() + g['n'].sum())
                row['days'] = mp['date'].nunique()
                # equal-weight-by-day view: days on which microprice's day mean is higher
                dd = (means['microprice'][['markout 60s', 'realized spread 60s']]
                      - means[ref][['markout 60s', 'realized spread 60s']]).dropna()
                row['days mp better markout 60s'] = int((dd['markout 60s'] > 0).sum())
                row['days mp better realized 60s'] = int((dd['realized spread 60s'] > 0).sum())
                rows.append(row)
    return pd.DataFrame(rows)


def carry_post_table(daily):
    b = daily[(daily['config'] == 'base') & (daily['fair value'] == 'microprice')]
    t = b[['date', 'max inventory', 'pnl', 'spread', 'carry_pre', 'carry_post', 'liq',
           'inv at close', 'final inv marked']].copy()
    t['pnl ex carry_post'] = t['pnl'] - t['carry_post']
    t['carry_post share'] = np.where(t['pnl'] != 0, t['carry_post'] / t['pnl'], np.nan)
    return t.sort_values(['max inventory', 'date']).reset_index(drop=True)


def chart(daily, cap, path):
    a = daily[(daily['config'] == 'all') & (daily['max inventory'] == cap)]
    wide = a.pivot(index='date', columns='fair value', values='pnl')[FVS]
    fees = a.pivot(index='date', columns='fair value', values='pnl_fees')[FVS]
    days = pd.to_datetime(wide.index)
    x = np.arange(len(wide))

    plt.rcParams.update({'font.size': 10, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2,
                         'xtick.color': INK2, 'ytick.color': INK2, 'text.color': INK})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.6), sharex=True,
                                   gridspec_kw={'height_ratios': [1.15, 1]}, facecolor=SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        ax.spines[['top', 'right']].set_visible(False)
        ax.axhline(0, color=INK2, lw=0.8, zorder=1)
        ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))

    w = 0.26
    for k, fv in enumerate(FVS):
        ax1.bar(x + (k - 1) * w, wide[fv], width=w * 0.9, color=SERIES[fv], label=fv,
                edgecolor=SURFACE, linewidth=0.6, zorder=2)
    ax1.set_ylabel('daily PnL (index points)')
    ax1.set_title(f'Daily PnL by fair value, all fixes (config all, cap {cap}), '
                  f'{days[0]:%d %b} to {days[-1]:%d %b %Y}', loc='left', fontsize=12)
    ax1.legend(loc='upper left', bbox_to_anchor=(1.01, 1), frameon=False)

    cum, cum_f = wide.cumsum(), fees.cumsum()
    notes = []
    for fv in FVS:
        ax2.plot(x, cum[fv], color=SERIES[fv], lw=2, solid_joinstyle='round',
                 solid_capstyle='round', zorder=3)
        ax2.plot(x[-1], cum[fv].iloc[-1], 'o', ms=8, color=SERIES[fv], mec=SURFACE, mew=2,
                 zorder=4)
        m, se, t = mean_se(wide[fv])
        notes.append((cum[fv].iloc[-1],
                      f'{fv}: {cum[fv].iloc[-1]:+,.0f} pts\n'
                      f'  {cum_f[fv].iloc[-1]:+,.0f} after fees\n'
                      f'  {m:+,.0f}/day, t = {t:+.1f}'))
    ax2.set_ylabel('cumulative PnL (index points)')
    lo, hi = ax2.get_ylim()
    gap = 0.2 * (hi - lo)
    placed = []
    for y, text in sorted(notes):
        y_lab = max(y, placed[-1] + gap) if placed else y
        placed.append(y_lab)
        ax2.annotate(text, xy=(x[-1], y), xytext=(1.03, y_lab),
                     textcoords=('axes fraction', 'data'),
                     va='center', color=INK, fontsize=9, annotation_clip=False,
                     arrowprops=dict(arrowstyle='-', color=GRID, lw=0.8))
    ax2.set_xticks(x)
    ax2.set_xticklabels([f'{d:%d %b}' for d in days], rotation=60, ha='right', fontsize=8.5)
    ax2.set_xlim(-0.6, len(x) - 0.4)
    ax2.set_xlabel('session (test date)')
    fig.tight_layout()
    fig.subplots_adjust(right=0.8)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    print(f'wrote {path}')


def main():
    daily, missing = load_daily()
    dates = sorted(daily['date'].unique())
    print(f'{len(dates)} days loaded')
    daily.to_csv(os.path.join(OUT, 'multiday_daily.csv'), index=False)

    tab = daily_table(daily)
    tab.to_csv(os.path.join(OUT, 'multiday_summary.csv'), index=False)

    ds = day_sums(daily)
    mk = markout_table(ds, daily)
    mk.to_csv(os.path.join(OUT, 'multiday_markouts.csv'), index=False)

    cp = carry_post_table(daily)
    cp.to_csv(os.path.join(OUT, 'multiday_carry_post.csv'), index=False)

    chart(daily, 500, os.path.join(OUT, 'daily_pnl_all.png'))

    pd.set_option('display.width', 250)
    pd.set_option('display.max_columns', 40)
    print('\n== daily PnL across days ==')
    print(tab[['config', 'fair value', 'cap', 'days', 'pnl mean', 'pnl se', 'pnl t',
               'pnl_fees mean', 'pnl_fees t', 'share pnl>0', 'share pnl_fees>0', 'fills/day',
               'spread/fill', 'markout 1s', 'markout 10s', 'markout 60s',
               'realized spread 60s']].round(3).to_string(index=False))
    print(tab[['config', 'fair value', 'cap', 'spread', 'carry_pre', 'carry_post', 'liq',
               'total pnl', 'total pnl_fees']].round(1).to_string(index=False))
    print('\n== pooled per-fill markouts (day-clustered SE) ==')
    cols = ['config', 'cap', 'fair value', 'compare', 'n fills', 'days']
    for k in KEYS:
        cols += [k, k + ' t']
    cols += ['days mp better markout 60s', 'days mp better realized 60s']
    print(f'taker fills in pooled set: {int(ds["taker fills"].sum())}')
    print(mk[cols].round(4).to_string(index=False))
    print('\n== base microprice: carry_post ==')
    print(cp[cp['max inventory'].isin(CAPS)].round(2).to_string(index=False))
    for cap in CAPS:
        c = cp[cp['max inventory'] == cap]
        print(f'cap {cap}: total pnl {c["pnl"].sum():+,.1f}, carry_post {c["carry_post"].sum():+,.1f} '
              f'({c["carry_post"].sum() / c["pnl"].sum():.0%}), ex carry_post t = '
              f'{mean_se(c["pnl ex carry_post"])[2]:+.2f}')


if __name__ == '__main__':
    main()
