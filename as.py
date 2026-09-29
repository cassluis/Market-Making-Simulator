# Avellenada-Stoikov market-making pricing model

from dataclasses import dataclass
import pandas as pd
import numpy as np
import databento as db
import numba
import time
import orderbook
from fvest import Stoikov

@dataclass
class config:
    vol_window: str = '60s'
    sample_freq: str = '1s'
    gamma: float = 0.1
    k: float = 1.5
    ticksize: float = 0.25
    starting_capital: float = 1_000_000

@numba.njit
def quotes(gamma, k, ticksize, T_t, mid, inventory, volatility):
    r_t = mid - inventory * volatility**2 * gamma * T_t
    spread_t = volatility**2 * gamma * T_t + (2 / gamma) * np.log(1 + gamma / k)

    bid = np.floor((r_t - spread_t / 2) / ticksize) * ticksize
    ask = np.ceil((r_t + spread_t / 2) / ticksize) * ticksize

    return bid, ask

def run_backtest(path, sample_times, fair_values, volatilities, starting_capital, inventory_limits, gamma, k, ticksize):
    book = orderbook.OrderBook()
    store = db.DBNStore.from_file(path)

    strategies = []
        
    for max_inventory in inventory_limits:
        for name, fv in fair_values.items():
            strategies.append({
                'max_inventory': max_inventory,
                'fair_value': name,
                'fv': fv,
                'inventory': 0,
                'cash': starting_capital,
                'bid_order': None,
                'ask_order': None,
                'num_trades': 0,
                'pnl_history': [],
                'inventory pnl': 0,
                'previous bid': 0,
                'previous ask': 0
            })

    sample_idx = 0
    final_time = sample_times[-1]

    for rec in store:
        ts = rec.ts_event
 
        if rec.action in ('C', 'M'):
            for s in strategies:
                for order, side_book in (
                    (s['bid_order'], book.bids),
                    (s['ask_order'], book.asks)
                ):
                    if order is None or rec.order_id not in order['ahead_ids']:
                        continue

                    old_size = side_book.get(order['price'], {}).get(rec.order_id, 0)

                    if rec.action == 'C':
                        removed = old_size
                        order['ahead_ids'].discard(rec.order_id)

                    elif rec.price != order['price']:
                        removed = old_size
                        order['ahead_ids'].discard(rec.order_id)

                    else:
                        removed = max(0, old_size - rec.size)

                        if rec.size <= 0:
                            order['ahead_ids'].discard(rec.order_id)

                    order['queue_ahead'] -= removed

        book.handle(rec)

        new_sample = False

        while sample_idx + 1 < len(sample_times) and sample_times[sample_idx + 1] <= ts:
            sample_idx += 1
            new_sample = True

        if new_sample:
            volatility = volatilities[sample_idx]

            if not np.isnan(volatility):
                T_t = (final_time - sample_times[sample_idx]) / 1e9

                for s in strategies:
                    if s['inventory'] > 0:
                        s['inventory pnl'] += s['inventory'] * ((book.best_bid - s['previous bid']) / 1e9)
                    if s['inventory'] < 0:
                        s['inventory pnl'] += s['inventory'] * ((book.best_ask - s['previous ask']) / 1e9)

                    s['previous bid'], s['previous ask'] = book.best_bid, book.best_ask

                    bid, ask = quotes(
                        gamma,
                        k,
                        ticksize,
                        T_t,
                        s['fv'][sample_idx],
                        s['inventory'],
                        volatility
                    )

                    bid = int(round(bid * 1e9))
                    ask = int(round(ask * 1e9))

                    s['bid_order'] = None
                    s['ask_order'] = None

                    if s['inventory'] < s['max_inventory']:
                        resting = book.bids.get(bid, {})
                        s['bid_order'] = {
                            'price': bid,
                            'size': 1,
                            'queue_ahead': sum(resting.values()),
                            'ahead_ids': set(resting.keys())
                        }
                        
                    if s['inventory'] > -s['max_inventory']:
                        resting = book.asks.get(ask, {})
                        s['ask_order'] = {
                            'price': ask,
                            'size': 1,
                            'queue_ahead': sum(resting.values()),
                            'ahead_ids': set(resting.keys())
                        }

                    if s['inventory'] > 0:
                        equity = s['cash'] + s['inventory'] * book.best_bid / 1e9
                    elif s['inventory'] < 0:
                        equity = s['cash'] + s['inventory'] * book.best_ask / 1e9
                    else:
                        equity = s['cash']

                    s['pnl_history'].append(equity)
        if rec.action == 'T':
            trade_price = rec.price
            trade_size = rec.size

            for s in strategies:
                bid_order = s['bid_order']
                ask_order = s['ask_order']

                if bid_order is not None and trade_price == bid_order['price']:
                    opportunity = trade_size - bid_order['queue_ahead']

                    if opportunity > 0:
                        fill_size = min(opportunity, bid_order['size'])

                        s['cash'] -= fill_size * bid_order['price'] / 1e9
                        s['inventory'] += fill_size
                        s['num_trades'] += fill_size

                        bid_order['size'] -= fill_size
                        bid_order['queue_ahead'] = 0

                        if bid_order['size'] == 0:
                            s['bid_order'] = None
                    else:
                        bid_order['queue_ahead'] -= trade_size

                if ask_order is not None and trade_price == ask_order['price']:
                    opportunity = trade_size - ask_order['queue_ahead']

                    if opportunity > 0:
                        fill_size = min(opportunity, ask_order['size'])

                        s['cash'] += fill_size * ask_order['price'] / 1e9
                        s['inventory'] -= fill_size
                        s['num_trades'] += fill_size

                        ask_order['size'] -= fill_size
                        ask_order['queue_ahead'] = 0

                        if ask_order['size'] == 0:
                            s['ask_order'] = None
                    else:
                        ask_order['queue_ahead'] -= trade_size

    results = []

    for s in strategies:
        if s['inventory'] > 0:
            s['cash'] += s['inventory'] * (book.best_bid / 1e9)
        elif s['inventory'] < 0:
            s['cash'] += s['inventory'] * (book.best_ask / 1e9)


        pnl = s['cash'] - starting_capital

        pnl_changes = np.diff(s['pnl_history'])
        pnl_volatility = np.std(pnl_changes)

        returns = pnl_changes / s['pnl_history'][:-1]
        return_volatility = np.std(returns)

        results.append({
            'max inventory': s['max_inventory'],
            'fair value': s['fair_value'],
            'pnl': pnl,
            'pnl_std': return_volatility,
            'trade count': s['num_trades'],
            'pnl volatility': pnl_volatility,
            'return volatility': return_volatility,
            'inventory pnl': s['inventory pnl']
        })

    return pd.DataFrame(results)

def main():
    start = time.perf_counter()

    all_results = []

    dates = [
        ('20250131', '20250203')
    ]

    inventory_limits = [10, 25, 50, 100, 250, 500]

    for cal_date, date in dates:
        print(f'\nRunning {date} using calibration {cal_date}...')

        cal_path = f'/volumes/t9/market_making/features/{cal_date}.parquet'
        path = f'/volumes/t9/market_making/features/{date}.parquet'
        mbopath = f'/volumes/t9/market_making/es_mbo/glbx-mdp3-{date}.mbo.dbn.zst'

        lob_cal = orderbook.load(cal_path)
        lob = orderbook.load(path)

        T_cal, _, _ = Stoikov.prep_data_sym(lob_cal, 10, 1, 1)
        T, _, _ = Stoikov.prep_data_sym(lob, 10, 1, 1)

        G1, B = Stoikov.estimate(T_cal)
        G6 = Stoikov.getG6(G1, B)

        T['mp'] = T['mid'] + G6[T['imb_bucket'].astype(int)]

        lob['time'] = pd.to_datetime(lob['time'], unit='ns')
        T['time'] = pd.to_datetime(T['time'], unit='ns')

        lob = lob.sort_values('time').reset_index(drop=True)
        T = T.sort_values('time').reset_index(drop=True)

        lob = pd.merge_asof(
            lob,
            T[['time', 'mp']],
            on='time',
            direction='backward'
        )

        lob['imbalance'] = lob['bs'] / (lob['bs'] + lob['as'])
        lob['wmid'] = lob['imbalance'] * lob['ask'] + (1 - lob['imbalance']) * lob['bid']

        lob_sample = lob[['time', 'mid', 'wmid', 'mp']].copy()

        lob_sample['time'] = lob_sample['time'].dt.floor(config.sample_freq)

        lob_sample = (
            lob_sample
            .groupby('time')[['mid', 'wmid', 'mp']]
            .last()
            .reset_index()
        )

        lob_sample['log_return'] = np.log(
            lob_sample['mid'] / lob_sample['mid'].shift(1)
        )

        lob_sample['volatility'] = lob_sample.rolling(
            config.vol_window,
            on='time'
        )['log_return'].std()

        sample_times = lob_sample['time'].to_numpy(
            dtype='datetime64[ns]'
        ).astype(np.int64)

        volatilities = lob_sample['volatility'].to_numpy()

        fair_values = {
            'mid': lob_sample['mid'].to_numpy(),
            'wmid': lob_sample['wmid'].to_numpy(),
            'microprice': lob_sample['mp'].to_numpy()
        }

        results = run_backtest(
            mbopath,
            sample_times,
            fair_values,
            volatilities,
            config.starting_capital,
            inventory_limits,
            config.gamma,
            config.k,
            config.ticksize
        )

        results['date'] = date
        results['calibration date'] = cal_date

        all_results.append(results)

        print(results[['max inventory', 'fair value', 'pnl', 'pnl_std', 'trade count', 'pnl volatility', 'return volatility', 'inventory pnl']])

    results = pd.concat(all_results, ignore_index=True)

    summary = (
        results
        .groupby(['max inventory', 'fair value'])
        .agg(
            total_pnl=('pnl', 'sum'),
            mean_pnl=('pnl', 'mean'),
            pnl_std=('pnl', 'std'),
            total_trades=('trade count', 'sum'),
            mean_trades=('trade count', 'mean'),
            total_inventory_pnl=('inventory pnl', 'sum'),
            mean_inventory_pnl=('inventory pnl', 'mean')
        )
        .reset_index())
    
    summary['gross sharpe'] = summary['mean_pnl'] / summary['pnl_std']

    print(summary)

    print(f'\nTotal runtime: {time.perf_counter() - start:.2f}s')

if __name__ == '__main__':
    main()