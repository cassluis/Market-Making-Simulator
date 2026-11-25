#!/usr/bin/env python3
"""ES price vs each strategy's inventory on one day, from as_diag.py output.

Two stacked panels on a shared time axis (never two y-scales on one panel):
  top     ES mid, 09:30-19:00 ET, with the unquoted 16:00-19:00 stretch shaded
  bottom  inventory of the as.py strategies at one cap, flat-extended after
          16:00 to the inventory as.py finally marks at 19:00
"""
import argparse
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402
import numpy as np                          # noqa: E402
import pandas as pd                         # noqa: E402

ROOT = '/Volumes/T9/Market_making'
HERE = os.path.dirname(os.path.abspath(__file__))
TZ = 'America/New_York'

SURFACE, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
SERIES = {'mid': '#2a78d6', 'wmid': '#eb6834', 'microprice': '#1baf7a'}   # slots 1-3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20250203')
    ap.add_argument('--cap', type=int, default=500)
    ap.add_argument('--config', default='base')
    args = ap.parse_args()

    out_dir = os.path.join(HERE, 'diag_out')
    paths = np.load(os.path.join(out_dir, f'paths_{args.date}.npz'))
    summary = pd.read_csv(os.path.join(out_dir, f'summary_{args.date}.csv'))
    labels = list(paths['labels'])
    t = pd.to_datetime(paths['sample_ts'], utc=True).tz_convert(TZ).tz_localize(None)

    full = pd.read_parquet(f'{ROOT}/features/{args.date}.parquet',
                           columns=['ts', 'bid_px', 'ask_px'])
    ft = pd.to_datetime(full['ts'], unit='ns', utc=True).dt.tz_convert(TZ).dt.tz_localize(None)
    px = pd.Series((full['bid_px'].to_numpy() + full['ask_px'].to_numpy()) / 2e9, index=ft)
    px = px[(px.index >= t[0]) & (px.index <= t[0].normalize() + pd.Timedelta(hours=19))]
    px = px.resample('10s').last().dropna()
    close_t, end_t = t[-1], px.index[-1]
    flattens = args.config in ('c', 'all')          # these configs exit at 16:00
    name = {'base': 'as.py as written', 'all': 'all fixes'}.get(args.config, f'config {args.config}')

    plt.rcParams.update({'font.size': 10, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2,
                         'xtick.color': INK2, 'ytick.color': INK2, 'text.color': INK})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.2), sharex=True,
                                   gridspec_kw={'height_ratios': [1, 1.25]},
                                   facecolor=SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.spines[['top', 'right']].set_visible(False)
        ax.axvspan(close_t, end_t, color='#f0efec', lw=0, zorder=0)

    ax1.plot(px.index, px.values, color=INK2, lw=1.4)
    ax1.set_ylabel('ES mid (index points)')
    ax1.set_title(f'{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}: price, and inventory held '
                  f'by each strategy ({name}, cap {args.cap})', loc='left', fontsize=12)
    ax1.text(close_t + (end_t - close_t) / 2, ax1.get_ylim()[0],
             'flat from 16:00' if flattens else 'not quoting,\nstill holding',
             ha='center', va='bottom', color=INK2, fontsize=9)

    notes = []
    for fv, color in SERIES.items():
        i = labels.index(f'{args.config}|{fv}|{args.cap}')
        inv = paths['inv'][i]
        n = int(np.sum(~np.isnan(inv)))
        row = summary[(summary['config'] == args.config) & (summary['fair value'] == fv)
                      & (summary['max inventory'] == args.cap)].iloc[0]
        ax2.plot(t[:n], inv[:n], color=color, lw=2, label=fv)
        if not flattens:
            ax2.plot([close_t, end_t], [inv[n - 1], row['final inv marked']], color=color, lw=2,
                     ls=(0, (2, 2)))
        carry = row['carry_pre'] + row['carry_post']
        notes.append((0 if flattens else row['final inv marked'],
                      f"{fv}: pnl {row['pnl']:+,.0f} pts\n  holding inventory {carry:+,.0f}\n"
                      + (f"  closed {row['inv at close']:+d} at 16:00" if flattens
                         else f"  after 16:00 {row['carry_post']:+,.0f}")))

    # stack the end labels bottom-up so none overlap, each at least `gap` apart
    lo, hi = ax2.get_ylim()
    gap = 0.17 * (hi - lo)
    placed = []
    for y, text in sorted(notes):
        y_lab = max(y, placed[-1] + gap) if placed else y
        placed.append(y_lab)
        ax2.annotate(text, xy=(close_t if flattens else end_t, y), xytext=(end_t + pd.Timedelta(minutes=8), y_lab),
                     textcoords='data', va='center', color=INK, fontsize=9,
                     annotation_clip=False)

    ax2.axhline(0, color=INK2, lw=0.8)
    ax2.set_ylabel('inventory (contracts)')
    ax2.legend(loc='lower right', frameon=False, ncol=3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax2.set_xlabel('time (ET)')
    fig.tight_layout()
    fig.subplots_adjust(right=0.74)
    out = os.path.join(out_dir, f'inventory_{args.date}_{args.config}_{args.cap}.png')
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
