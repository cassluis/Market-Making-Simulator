# Avellenada-Stoikov market-making pricing model

import pandas as pd
import numpy as np
import numba
import orderbook
from dataclasses import dataclass
from fvest import Stoikov

@dataclass
class Config:
    path: str = '/volumes/t9/market_making/features/20250106.parquet'
    vol_window: str = '60s'
    sample_freq: str = '1s'
    gamma: float = 0.1
    k: float = 1.5
    ticksize: float = 0.25
    starting_capital: float = 1_000_000
    max_inventory: int = 100

@numba.njit
def quotes(gamma, k, ticksize, T_t, mid, inventory, volatility):
    r_t = mid - (inventory) * (volatility ** 2) * (gamma) * T_t
    spread_t = (volatility ** 2) * gamma * T_t + (2 / gamma) * np.log(1 + gamma / k)

    bid = np.floor((r_t - spread_t / 2) / ticksize) * ticksize
    ask = np.ceil((r_t + spread_t / 2) / ticksize) * ticksize

    return bid, ask

@numba.njit
def run_backtest(times, sample_times, bids, asks, mids, volatilities, starting_capital, max_inventory, gamma, k, ticksize):
    inventory = 0
    cash = starting_capital

    sample_idx = 0

    for i in range(len(times)):
        while sample_idx + 1 < len(sample_times) and sample_times[sample_idx + 1] <= times[i]:
            sample_idx += 1

            volatility = volatilities[sample_idx]

            quote = False

            if not np.isnan(volatility):
                T_t = (times[-1] - times[sample_idx]) / 1e9

                bid, ask = quotes(gamma, k, ticksize, T_t, mids[sample_idx], inventory, volatility)

                quote = True

        ### Impliment a new algorithm that takes into account the current state of the orderbook to calculate fills.
        
        if quote:
            if asks[i] <= bid and inventory < max_inventory:
                cash -= bid
                inventory += 1

            if bids[i] >= ask and inventory > -max_inventory:
                cash += ask
                inventory -= 1

    if inventory > 0:
        cash += inventory * asks[-1]
    else:
        cash += inventory * bids[-1]

    inventory = 0

    return inventory, cash

def main(): 
    config = Config()
    lob = orderbook.load(config.path)

    T, _, _ = Stoikov.prep_data_sym(lob, 10, 1, 1)

    G1, B = Stoikov.estimate(T)

    G6 = Stoikov.getG6(G1, B)

    T['mp'] = T['mid'] + G6[T['imb_bucket'].astype(int)]

    lob['time'] = pd.to_datetime(lob['time'], unit="ns")
    lob = lob.sort_values('time').reset_index(drop=True)

    lob['imbalance'] = lob['bs'] / (lob['bs'] + lob['as'])
    lob['wmid'] = lob['imbalance'] * lob['ask'] + (1 - lob['imbalance']) * lob['bid']
    lob['mp'] = T['mp'] 

    lob_sample = lob[['time', 'mid', 'wmid', 'mp']].copy()
    lob_sample['time'] = lob_sample['time'].dt.floor(config.sample_freq)
    lob_sample = lob_sample.groupby('time')[['mid', 'wmid']].last().reset_index()

    lob_sample['log_return'] = np.log(lob_sample['mid'] / lob_sample['mid'].shift(1))
    lob_sample['volatility'] = lob_sample.rolling(config.vol_window, on='time')['log_return'].std()

    times = lob['time'].to_numpy(dtype='datetime64[ns]').astype(np.int64)
    sample_times = lob_sample['time'].to_numpy(dtype='datetime64[ns]').astype(np.int64)
    bids = lob['bid'].to_numpy()
    asks = lob['ask'].to_numpy()
    sample_mids = lob_sample['mid'].to_numpy()
    volatilities = lob_sample['volatility'].to_numpy()
    wmids = lob_sample['wmid'].to_numpy()
    mp = lob_sample['mp'].to_numpy()

    for inv in [10, 20, 50, 100, 500, 1000]:
        inventory, cash = run_backtest(times, sample_times, bids, asks, mp, volatilities, config.starting_capital, inv, config.gamma, config.k, config.ticksize)

        pnl = cash - config.starting_capital

        print(f'pnl with max inventory {inv}: {pnl}')

if __name__ == '__main__':
    main()