"""Micro-price vs mid vs weighted mid as forecasts of the future mid -- corrected estimator.

Replaces microprice_horizons.py / microprice_horizons_ns4.py, whose estimators (copied
from mm_sim.Stoikov / fvest.Stoikov) use np.resize on mis-shaped pivot tables, so the
R1 / R2 matrices -- and therefore G6 -- are scrambled. Those results are invalid.

What is kept from microprice_horizons.py
  - HORIZONS = [1, 5, 10, 25, 50, 100, 250, 500, 1000] book events
  - target = mid h events ahead; loss = squared (and absolute) error, averaged per day
  - evaluated rows = RTH rows whose current spread is exactly one tick (N_SPREAD = 1)
  - 10 imbalance buckets, G iterated 6 terms (G6)

What changes (all deliberate)
  - estimator: as.estimate_G6 (correct shapes), applied causally exactly as as.main():
    bins from fvest.get_imbalance_bins(calibration day), G6 fitted on the calibration
    day, mp = mid + G6[bucket] on one-tick rows, mp = mid elsewhere
  - out of sample: calibrate on day d, test on day d+1, using as.py's 21 2025 pairs.
    The original fitted and scored on the SAME day.
  - the target is h events ahead in the raw RTH event stream. The original shifted
    within its filtered table (one-tick rows that also passed a |next-row move| <= 1 tick
    filter, which looks ahead), so its "h" was h filtered rows and part of the sample was
    selected on the future.
  - a secondary "all rows" scope scores every RTH row (mp = mid off one-tick spreads).

Outputs
  diagnostics/diag_out/microprice_horizons_fixed.csv        per (scope, horizon) summary
  diagnostics/diag_out/microprice_horizons_fixed_daily.csv  per (day, scope, horizon)

Run:  cd /private/tmp && python3 -B /Volumes/T9/Market_making/diagnostics/microprice_horizons_fixed.py
"""

import gc
import importlib
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = '/Volumes/T9/Market_making'
sys.path.insert(0, ROOT)

AS = importlib.import_module('as')          # 'as' is a keyword, so import by name
import orderbook                            # noqa: E402
from fvest import get_imbalance_bins        # noqa: E402

FEATURES = os.path.join(ROOT, 'features')
OUT_DIR = os.path.join(ROOT, 'diagnostics', 'diag_out')
OUT = os.path.join(OUT_DIR, 'microprice_horizons_fixed.csv')
OUT_DAILY = os.path.join(OUT_DIR, 'microprice_horizons_fixed_daily.csv')

TICK = AS.config.ticksize
N_IMB, STEPS = 10, 6
HORIZONS = [1, 5, 10, 25, 50, 100, 250, 500, 1000]

# as.py main() date list, 2025 sessions only
DATES = [
    ("20250106", "20250107"), ("20250107", "20250108"), ("20250108", "20250110"),
    ("20250110", "20250113"), ("20250113", "20250114"), ("20250114", "20250115"),
    ("20250115", "20250116"), ("20250116", "20250117"), ("20250117", "20250121"),
    ("20250121", "20250122"), ("20250122", "20250123"), ("20250123", "20250124"),
    ("20250124", "20250127"), ("20250127", "20250128"), ("20250128", "20250129"),
    ("20250129", "20250130"), ("20250130", "20250131"), ("20250131", "20250203"),
    ("20250203", "20250204"), ("20250204", "20250205"), ("20250205", "20250206"),
]


def fair_values(lob, bins, G6):
    """as.main()'s construction of mid / wmid / mp, on the event stream (no 1 s sampling)."""
    lob = lob.sort_values('time', kind='stable').reset_index(drop=True)
    imbalance = lob['bs'] / (lob['bs'] + lob['as'])
    wmid = (imbalance * lob['ask'] + (1 - imbalance) * lob['bid']).to_numpy()
    mid = lob['mid'].to_numpy()
    imb_bucket = pd.cut(imbalance, bins=bins, labels=False, include_lowest=True).to_numpy()
    one_tick_spread = np.round((lob['ask'] - lob['bid']).to_numpy() / TICK) == 1
    one_tick = one_tick_spread & ~np.isnan(imb_bucket)
    bucket = np.where(one_tick, imb_bucket, 0).astype(int)
    mp = mid + np.where(one_tick, G6[bucket], 0.0)
    return lob['time'].to_numpy(np.int64), mid, wmid, mp, one_tick_spread


def main():
    t0 = time.perf_counter()
    rows = []
    for cal, test in DATES:
        lob_cal = orderbook.load(os.path.join(FEATURES, f'{cal}.parquet'))
        bins = get_imbalance_bins(lob_cal, N_IMB)
        G6 = AS.estimate_G6(lob_cal, bins, N_IMB, STEPS, TICK)
        del lob_cal

        lob = orderbook.load(os.path.join(FEATURES, f'{test}.parquet'))
        ts, mid, wmid, mp, one_tick = fair_values(lob, bins, G6)
        del lob
        n = len(mid)
        print(f'{test} (cal {cal}): {n:,} RTH events, one-tick {one_tick.mean():.1%}, '
              f'G6 [{G6.min():+.4f}, {G6.max():+.4f}] = {np.abs(G6).max() / (TICK / 2):.2f} '
              f'of half-tick', flush=True)

        for h in HORIZONS:
            fut = np.full(n, np.nan)
            fut[:n - h] = mid[h:]
            dt_ms = np.full(n, np.nan)
            dt_ms[:n - h] = (ts[h:] - ts[:n - h]) / 1e6
            base = ~np.isnan(fut) & ~np.isnan(wmid)
            for scope, m in (('one_tick', base & one_tick), ('all_rows', base)):
                e_mid, e_wmid, e_mp = mid[m] - fut[m], wmid[m] - fut[m], mp[m] - fut[m]
                rows.append({
                    'test_date': test, 'cal_date': cal, 'scope': scope, 'h': h,
                    'n_obs': int(m.sum()),
                    'median_ms': float(np.median(dt_ms[m])),
                    'mean_ms': float(np.mean(dt_ms[m])),
                    'mse_mid': np.mean(e_mid ** 2), 'mse_wmid': np.mean(e_wmid ** 2),
                    'mse_micro': np.mean(e_mp ** 2),
                    'mae_mid': np.mean(np.abs(e_mid)), 'mae_wmid': np.mean(np.abs(e_wmid)),
                    'mae_micro': np.mean(np.abs(e_mp)),
                    'g6_max_abs': float(np.abs(G6).max()),
                })
        del ts, mid, wmid, mp, one_tick
        gc.collect()

    d = pd.DataFrame(rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    d.to_csv(OUT_DAILY, index=False)

    def tstat(x):
        x = np.asarray(x, float)
        return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))

    def summarise(x):
        dm, dw = x.mse_micro - x.mse_mid, x.mse_micro - x.mse_wmid
        return pd.Series({
            'n_days': len(x),
            'median_ms': x.median_ms.median(),
            'mean_ms': x.mean_ms.mean(),
            'mse_mid': x.mse_mid.mean(), 'mse_wmid': x.mse_wmid.mean(),
            'mse_micro': x.mse_micro.mean(),
            'ratio_micro_mid': x.mse_micro.mean() / x.mse_mid.mean(),
            'ratio_micro_wmid': x.mse_micro.mean() / x.mse_wmid.mean(),
            'ratio_wmid_mid': x.mse_wmid.mean() / x.mse_mid.mean(),
            'days_micro_beats_mid': int((dm < 0).sum()),
            'days_micro_beats_wmid': int((dw < 0).sum()),
            'days_wmid_beats_mid': int((x.mse_wmid < x.mse_mid).sum()),
            't_micro_minus_mid': tstat(dm),
            't_micro_minus_wmid': tstat(dw),
            't_wmid_minus_mid': tstat(x.mse_wmid - x.mse_mid),
            'mean_pct_red_vs_mid': (1 - x.mse_micro / x.mse_mid).mean() * 100,
            'mean_pct_red_vs_wmid': (1 - x.mse_micro / x.mse_wmid).mean() * 100,
            'mae_mid': x.mae_mid.mean(), 'mae_wmid': x.mae_wmid.mean(),
            'mae_micro': x.mae_micro.mean(),
            'days_micro_beats_wmid_mae': int((x.mae_micro < x.mae_wmid).sum()),
            't_mae_micro_minus_wmid': tstat(x.mae_micro - x.mae_wmid),
        })

    s = (d.groupby(['scope', 'h']).apply(summarise, include_groups=False)
          .reset_index().sort_values(['scope', 'h'], ascending=[False, True]))
    s.to_csv(OUT, index=False)

    pd.set_option('display.width', 250)
    for scope, g in s.groupby('scope', sort=False):
        print(f'\n=== {scope}: {int(g.n_days.iloc[0])} out-of-sample days, mean daily MSE (pt^2) ===')
        print(g[['h', 'median_ms', 'mse_mid', 'mse_wmid', 'mse_micro', 'ratio_micro_mid',
                 'ratio_micro_wmid', 'days_micro_beats_mid', 'days_micro_beats_wmid',
                 't_micro_minus_mid', 't_micro_minus_wmid']].to_string(index=False,
                                                                       float_format='%.5g'))
    print(f'\nwrote {OUT}\nwrote {OUT_DAILY}\n{time.perf_counter() - t0:.0f}s')


if __name__ == '__main__':
    main()
