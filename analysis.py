"""
Run the Stage 2 (OFI / price impact) and Stage 3 (fair value) analysis across
every day of feature data, one day at a time.

Per-day results print as they complete and are collected into tables at the end,
so you can see the spread across sessions rather than only a pooled number.

Usage:  python analyse_all.py
"""

import os
import glob

import numpy as np
import pandas as pd
import statsmodels.api as sm

import mm_sim
from mm_sim import Stoikov

FEATURE_DIR = r"F:/market_making/features"
OUT_DIR     = r"F:/market_making/results"

TICK     = 0.25
N_IMB    = 10
N_SPREAD = 1
DT       = 1
HORIZON  = 100                        # rows ahead for the fair-value comparison
BUCKETS  = [100, 500, 1000, 5000]
LAMBDAS  = [0.5, 0.65, 0.75, 0.85, 1.0, 1.15]

# Stoikov.estimate() reads these as module globals in mm_sim.
mm_sim.n_imb    = N_IMB
mm_sim.n_spread = N_SPREAD


def compute_G6(G1, B):
    """Iterate G1 forward six price moves — plot_Gstar without the plot."""
    G = np.array(G1, dtype=float)
    term = G.copy()
    for _ in range(5):
        term = np.dot(B, term)
        G = G + term
    return G


# --------------------------------------------------------------------------- #
# Loading and feature construction
# --------------------------------------------------------------------------- #
def load_day(path):
    """Load one day's book states, trim to RTH, add derived columns."""
    df = pd.read_parquet(path)

    ny = pd.to_datetime(df.ts, unit='ns', utc=True).dt.tz_convert('America/New_York')
    mask = (ny.dt.time >= pd.Timestamp('9:30').time()) & \
           (ny.dt.time <  pd.Timestamp('16:00').time())
    rth = df[mask].copy()
    if len(rth) < 10_000:
        return None                   # holiday / half session

    rth[['bid_px', 'ask_px']] /= 1e9
    rth['mid']       = (rth.bid_px + rth.ask_px) / 2
    rth['spread']    = rth.ask_px - rth.bid_px
    rth['imbalance'] = rth.bid_sz / (rth.bid_sz + rth.ask_sz)
    rth['wmid']      = rth.bid_px * (1 - rth.imbalance) + rth.ask_px * rth.imbalance

    bp1, ap1 = rth.bid_px.shift(1), rth.ask_px.shift(1)
    bq1, aq1 = rth.bid_sz.shift(1), rth.ask_sz.shift(1)
    bid = np.where(rth.bid_px >= bp1, rth.bid_sz, 0) - np.where(rth.bid_px <= bp1, bq1, 0)
    ask = np.where(rth.ask_px <= ap1, rth.ask_sz, 0) - np.where(rth.ask_px >= ap1, aq1, 0)
    rth['e'] = bid - ask
    rth.iloc[0, rth.columns.get_loc('e')] = 0

    return rth.reset_index(drop=True)


def bucket(df, n):
    g = df.assign(bucket=np.arange(len(df)) // n).groupby('bucket')
    return pd.DataFrame({
        'ofi':   g.e.sum(),
        'depth': (g.bid_sz.mean() + g.ask_sz.mean()) / 2,
        'dmid':  g.mid.last() - g.mid.first(),
        'span':  g.ts.last() - g.ts.first(),
        'n':     g.size(),
    })


# --------------------------------------------------------------------------- #
# Stage 2
# --------------------------------------------------------------------------- #
def stage2(rth, day):
    rows = []
    for n in BUCKETS:
        b = bucket(rth, n)
        b = b[(b.depth > 0) & (b.n == n)].dropna()
        if len(b) < 100:
            continue
        m = sm.OLS(b.dmid, sm.add_constant(b.ofi / b.depth)).fit()
        rows.append({'day': day, 'n': n,
                     'ms': b.span.median() / 1e6,
                     'r2': m.rsquared,
                     'alpha': m.params.iloc[0],
                     'beta': m.params.iloc[1],
                     'obs': len(b)})
    return rows


def stage2_horizon(rth, day, n=500, horizons=(0, 1, 2, 5, 10, 20)):
    b = bucket(rth, n)
    b = b[(b.depth > 0) & (b.n == n)].dropna()
    if len(b) < 200:
        return []
    x = sm.add_constant(b.ofi / b.depth)
    rows = []
    for h in horizons:
        y = b.dmid.shift(-h)
        v = y.notna()
        m = sm.OLS(y[v], x[v]).fit()
        rows.append({'day': day, 'h': h,
                     'ms': h * b.span.median() / 1e6,
                     'r2': m.rsquared,
                     'beta': m.params.iloc[1],
                     't': m.tvalues.iloc[1]})
    return rows


# --------------------------------------------------------------------------- #
# Stage 3
# --------------------------------------------------------------------------- #
def stage3(rth, day):
    fut = rth.mid.shift(-HORIZON)
    res = {'day': day,
           'mse_mid':  ((fut - rth.mid)  ** 2).mean(),
           'mse_wmid': ((fut - rth.wmid) ** 2).mean()}

    errs = {lam: ((fut - (rth.mid + lam * (rth.wmid - rth.mid))) ** 2).mean()
            for lam in LAMBDAS}
    res['lambda_star']     = min(errs, key=errs.get)
    res['mse_lambda_star'] = errs[res['lambda_star']]

    T = rth.rename(columns={'bid_px': 'bid', 'ask_px': 'ask',
                            'bid_sz': 'bs',  'ask_sz': 'as',
                            'ts': 'time'})
    try:
        T3, _ = Stoikov.prep_data_sym(T, N_IMB, DT, N_SPREAD)
        G1, B = Stoikov.estimate(T3)[:2]
        G6 = compute_G6(G1, B)

        real = T3.iloc[:len(T3) // 2].copy()
        real['micro'] = real['mid'] + G6[real.imb_bucket.astype(int).values]
        rfut = real.mid.shift(-HORIZON)

        res['mse_micro']      = ((rfut - real.micro) ** 2).mean()
        res['g6_max']         = float(np.max(np.abs(G6)))
        res['implied_lambda'] = res['g6_max'] / (TICK / 2)
    except Exception as exc:
        print(f"  {day}: micro-price failed ({type(exc).__name__}: {exc})")

    return res


# --------------------------------------------------------------------------- #
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = sorted(p for p in glob.glob(os.path.join(FEATURE_DIR, "*.parquet"))
                   if '_trades' not in p)
    print(f"{len(paths)} days found\n")

    impact, horizon, fairvalue = [], [], []

    for path in paths:
        day = os.path.basename(path).split('.')[0]
        rth = load_day(path)
        if rth is None:
            print(f"{day}: too few RTH rows, skipping")
            continue

        impact  += stage2(rth, day)
        horizon += stage2_horizon(rth, day)
        fv = stage3(rth, day)
        fairvalue.append(fv)

        base = [r for r in impact if r['day'] == day and r['n'] == BUCKETS[0]]
        r2 = base[0]['r2'] if base else float('nan')
        print(f"{day}: rows={len(rth):>9,}  R2(n={BUCKETS[0]})={r2:.3f}  "
              f"lambda*={fv['lambda_star']}  "
              f"implied={fv.get('implied_lambda', float('nan')):.2f}")

    imp = pd.DataFrame(impact)
    hor = pd.DataFrame(horizon)
    fvd = pd.DataFrame(fairvalue)

    imp.to_csv(os.path.join(OUT_DIR, 'impact_by_day.csv'),    index=False)
    hor.to_csv(os.path.join(OUT_DIR, 'horizon_by_day.csv'),   index=False)
    fvd.to_csv(os.path.join(OUT_DIR, 'fairvalue_by_day.csv'), index=False)

    print("\n=== Stage 2: impact by bucket size (across days) ===")
    print(imp.groupby('n')[['ms', 'r2', 'beta', 'obs']].agg(['mean', 'std']).round(4))

    print("\n=== Stage 2: predictive decay (mean across days) ===")
    print(hor.groupby('h')[['ms', 'r2', 'beta', 't']].mean().round(6))

    print("\n=== Stage 3: fair value by day ===")
    print(fvd.round(6).to_string(index=False))

    print("\nlambda* distribution across days:")
    print(fvd.lambda_star.value_counts().sort_index())
    if 'implied_lambda' in fvd.columns:
        print(f"implied lambda from micro-price: "
              f"mean {fvd.implied_lambda.mean():.3f}  sd {fvd.implied_lambda.std():.3f}")
    if 'mse_micro' in fvd.columns:
        wins = (fvd.mse_micro < fvd.mse_wmid).sum()
        print(f"micro beats wmid on {wins} of {int(fvd.mse_micro.notna().sum())} days")


if __name__ == "__main__":
    main()