import numpy as np
import pandas as pd
import statsmodels.api as sm

from scipy.linalg import block_diag
from orderbook import load

n_imb, n_spread = 10, 1
imb = np.arange(n_imb)
pd.options.mode.chained_assignment = None


class Stoikov():

    def prep_data_sym(T, n_imb, dt, n_spread, imb_bins=None, filter_moves=True):
        ticksize = 0.25
        T['spread'] = np.round((T['ask'] - T['bid']) / ticksize) * ticksize
        T['mid'] = (T['bid'] + T['ask']) / 2
        T['imb'] = T['bs'] / (T['bs'] + T['as'])

        if imb_bins is None:
            _, imb_bins = pd.qcut(T['imb'], q=n_imb, retbins=True, duplicates='drop')
            if len(imb_bins) - 1 != n_imb:
                raise ValueError(f'Could not create {n_imb} unique imbalance buckets.')
            imb_bins[0], imb_bins[-1] = -np.inf, np.inf

        T['imb_bucket'] = pd.cut(T['imb'], bins=imb_bins, labels=False, include_lowest=True)

        # Calculate future state BEFORE filtering rows
        T['next_mid'] = T['mid'].shift(-dt)
        T['next_spread'] = T['spread'].shift(-dt)
        T['next_time'] = T['time'].shift(-dt)
        T['next_imb_bucket'] = T['imb_bucket'].shift(-dt)
        T['dM'] = np.round((T['next_mid'] - T['mid']) / ticksize * 2) * ticksize / 2

        # Current-state filter
        T = T.loc[(T.spread <= n_spread * ticksize) & (T.spread > 0)].copy()
        T = T.dropna(subset=['next_mid', 'next_spread', 'next_imb_bucket'])

        # Only filter large moves during calibration
        if filter_moves:
            T = T.loc[(T.dM <= ticksize * 1.1) & (T.dM >= -ticksize * 1.1)]

        T2 = T.copy(deep=True)
        T2['imb_bucket'] = n_imb - 1 - T2['imb_bucket']
        T2['next_imb_bucket'] = n_imb - 1 - T2['next_imb_bucket']
        T2['dM'] = -T2['dM']
        T2['mid'] = -T2['mid']

        T3 = pd.concat([T, T2])
        T3.index = pd.RangeIndex(len(T3))

        return T, T3, ticksize


    def estimate(T):
        no_move = T[T['dM'] == 0]
        no_move_counts = no_move.pivot_table(index=['next_imb_bucket'], columns=['spread', 'imb_bucket'], values='time', fill_value=0, aggfunc='count').unstack()
        Q_counts = np.resize(np.array(no_move_counts[0:(n_imb * n_imb)]), (n_imb, n_imb))

        for i in range(1, n_spread):
            Qi = np.resize(np.array(no_move_counts[(i * n_imb * n_imb):((i + 1) * n_imb * n_imb)]), (n_imb, n_imb))
            Q_counts = block_diag(Q_counts, Qi)

        move_counts = T[T['dM'] != 0].pivot_table(index=['dM'], columns=['spread', 'imb_bucket'], values='time', fill_value=0, aggfunc='count').unstack()
        R_counts = np.resize(np.array(move_counts), (n_imb * n_spread, 4))
        T1 = np.concatenate((Q_counts, R_counts), axis=1).astype(float)

        for i in range(n_imb * n_spread):
            T1[i] = T1[i] / T1[i].sum()

        Q = T1[:, :(n_imb * n_spread)]
        R1 = T1[:, (n_imb * n_spread):]

        K = np.array([-0.25, -0.125, 0.125, 0.25])

        move_counts = T[T['dM'] != 0].pivot_table(index=['spread', 'imb_bucket'], columns=['next_spread', 'next_imb_bucket'], values='time', fill_value=0, aggfunc='count')
        R2_counts = np.resize(np.array(move_counts), (n_imb * n_spread, n_imb * n_spread))
        T2 = np.concatenate((Q_counts, R2_counts), axis=1).astype(float)

        for i in range(n_imb * n_spread):
            T2[i] = T2[i] / T2[i].sum()

        R2 = T2[:, (n_imb * n_spread):]
        B = np.dot(np.linalg.inv(np.eye(n_imb * n_spread) - Q), R2)
        G1 = np.dot(np.dot(np.linalg.inv(np.eye(n_imb * n_spread) - Q), R1), K)

        return G1, B


    def getG6(G1, B, steps=6):
        G2 = G1 + np.dot(B, G1)
        G_steps = G2

        for i in range(3, steps + 1):
            BB = np.dot(B, B)
            for _ in range(3, i):
                BB = np.dot(BB, B)
            G_steps += np.dot(BB, G1)

        return G_steps


    def prep_data_fixed(T, n_imb, n_spread, ms, imb_bins):
        ticksize = 0.25
        T = T.copy()

        # Current state
        T["spread"] = np.round((T["ask"] - T["bid"]) / ticksize) * ticksize
        T["mid"] = (T["bid"] + T["ask"]) / 2
        T["imb"] = T["bs"] / (T["bs"] + T["as"])
        T["imb_bucket"] = pd.cut(
            T["imb"],
            bins=imb_bins,
            labels=False,
            include_lowest=True
        )

        # Convert everything to numpy arrays BEFORE subsetting
        times = T["time"].to_numpy(dtype=np.int64)
        mids = T["mid"].to_numpy()
        spreads = T["spread"].to_numpy()
        imb_buckets = T["imb_bucket"].to_numpy()

        # Find first event >= t + ms
        future_idx = np.searchsorted(
            times,
            times + ms * 1_000_000,
            side="left"
        )

        # Only keep rows where a future event exists
        valid = future_idx < len(T)

        current_idx = np.flatnonzero(valid)
        future_idx = future_idx[valid]

        # Subset AFTER calculating the future indices
        T = T.iloc[current_idx].copy()

        # Assign future values using the ORIGINAL arrays
        T["next_mid"] = mids[future_idx]
        T["next_spread"] = spreads[future_idx]
        T["next_imb_bucket"] = imb_buckets[future_idx]

        # Future mid movement
        T["dM"] = (
            np.round(
                (T["next_mid"] - T["mid"]) / ticksize * 2
            ) * ticksize / 2
        )

        # Same current-state spread filter as before
        T = T.loc[
            (T["spread"] <= n_spread * ticksize) &
            (T["spread"] > 0)
        ].copy()

        # Remove rows with invalid states
        T = T.dropna(
            subset=[
                "next_mid",
                "next_spread",
                "next_imb_bucket",
                "imb_bucket"
            ]
        ).copy()

        return T, ticksize
    
def get_imbalance_bins(T, n_imb):
    imb = T['bs'] / (T['bs'] + T['as'])
    imb = imb.dropna()

    _, bins = pd.qcut(imb, q=n_imb, retbins=True, duplicates='drop')

    if len(bins) - 1 != n_imb:
        raise ValueError(f'Could not create {n_imb} unique imbalance buckets. Only {len(bins) - 1} were possible.')

    bins[0], bins[-1] = -np.inf, np.inf
    return bins


def main():
    n_imb, n_spread = 10, 1
    ticksize = 0.25

    for d1, d2 in [(20250106, 20250107)]:

        calibration_df = load(f"/volumes/t9/market_making/features/{d1}.parquet")
        test_df = load(f"/volumes/t9/market_making/features/{d2}.parquet")

        imb_bins = get_imbalance_bins(calibration_df, n_imb)

        for ms in [1, 5, 10, 50, 100, 500, 1000]:

            # Calibration
            T_cal, _ = Stoikov.prep_data_fixed(
                calibration_df.copy(),
                n_imb,
                n_spread,
                ms,
                imb_bins
            )

            # Remove large moves during calibration
            T_cal = T_cal[
                (T_cal["dM"] <= ticksize * 1.1) &
                (T_cal["dM"] >= -ticksize * 1.1)
            ].copy()

            # Symmetry
            T2 = T_cal.copy()
            T2["imb_bucket"] = n_imb - 1 - T2["imb_bucket"]
            T2["next_imb_bucket"] = (
                n_imb - 1 - T2["next_imb_bucket"]
            )
            T2["dM"] = -T2["dM"]
            T2["mid"] = -T2["mid"]

            T_cal_sym = pd.concat([T_cal, T2])
            T_cal_sym.index = pd.RangeIndex(len(T_cal_sym))

            # Estimate Stoikov fair value
            G1, B = Stoikov.estimate(T_cal_sym)

            G6 = Stoikov.getG6(G1, B)

            # Test
            T, _ = Stoikov.prep_data_fixed(
                test_df.copy(),
                n_imb,
                n_spread,
                ms,
                imb_bins
            )

            T["mp_G6"] = T["mid"] + G6[T["imb_bucket"].astype(int)]

            T["mp"] = (
                T["mid"] +
                G6[T["imb_bucket"].astype(int)]
            )

            T["wmid"] = (
                T["ask"] * T["bs"] +
                T["bid"] * T["as"]
            ) / (T["bs"] + T["as"])

        err_mid = T["mid"] - T["next_mid"]
        err_g6 = T["mp_G6"] - T["next_mid"]
        err_wm = T["wmid"] - T["next_mid"]

        for name, err in [("mid", err_mid), ("mp", err_g6), ("wmid", err_wm)]:

            mae = err.abs().mean()
            mse = (err ** 2).mean()
            rmse = np.sqrt(mse)

            print(f'{name} | mae: {mae} | mse: {mse} | rmse: {rmse}')
        
if __name__ == "__main__":
    main()