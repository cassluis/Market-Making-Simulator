# Avellenada-Stoikov market-making pricing model

from dataclasses import dataclass
import pandas as pd
import numpy as np
import databento as db
import numba
import time
import orderbook
from fvest import get_imbalance_bins

@dataclass
class config:
    vol_window: str = '60s'
    sample_freq: str = '1s'
    gamma: float = 1e-4
    k: float = 1.549
    ticksize: float = 0.25
    starting_capital: float = 1_000_000
    fee: float = 0.024

@numba.njit
def quotes(gamma, k, ticksize, T_t, mid, inventory, volatility):
    r_t = mid - inventory * volatility**2 * gamma * T_t
    spread_t = volatility**2 * gamma * T_t + (2 / gamma) * np.log(1 + gamma / k)

    bid = np.floor((r_t - spread_t / 2) / ticksize) * ticksize
    ask = np.ceil((r_t + spread_t / 2) / ticksize) * ticksize

    return bid, ask

def estimate_G6(lob, bins, n_imb, steps, ticksize):
    one_tick = lob[np.round((lob['ask'] - lob['bid']) / ticksize) == 1]
    mid = ((one_tick['bid'] + one_tick['ask']) / 2).to_numpy()

    imb_bucket = pd.cut(
        one_tick['bs'] / (one_tick['bs'] + one_tick['as']),
        bins=bins,
        labels=False,
        include_lowest=True
    ).to_numpy()

    current, following = imb_bucket[:-1], imb_bucket[1:]
    move = np.round((mid[1:] - mid[:-1]) / ticksize)

    keep = ~np.isnan(current) & ~np.isnan(following) & (np.abs(move) <= 1)
    current = current[keep].astype(int)
    following = following[keep].astype(int)
    move = move[keep].astype(int)

    current = np.r_[current, n_imb - 1 - current]
    following = np.r_[following, n_imb - 1 - following]
    move = np.r_[move, -move]

    still = move == 0

    Q = np.zeros((n_imb, n_imb))
    R1 = np.zeros((n_imb, 2))
    R2 = np.zeros((n_imb, n_imb))

    np.add.at(Q, (current[still], following[still]), 1)
    np.add.at(R1, (current[~still], (move[~still] > 0).astype(int)), 1)
    np.add.at(R2, (current[~still], following[~still]), 1)

    total = Q.sum(1) + R1.sum(1)
    Q, R1, R2 = Q / total[:, None], R1 / total[:, None], R2 / total[:, None]

    N = np.linalg.inv(np.eye(n_imb) - Q)
    G1 = N @ R1 @ np.array([-ticksize, ticksize])
    B = N @ R2

    G6 = G1.copy()
    term = G1.copy()

    for _ in range(steps - 1):
        term = B @ term
        G6 += term

    return G6

def refresh(order, price, allowed, side_book):
    if not allowed:
        return None                      # cancel (e.g. inventory limit hit)
    if order is not None and order['price'] == price:
        return order                     # unchanged: keep queue position
    resting = side_book.get(price, {})
    return {
        'price': price,
        'size': 1,
        'queue_ahead': sum(resting.values()),
        'ahead_ids': set(resting.keys())
    }

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

                    elif rec.size > old_size:
                        removed = old_size
                        order['ahead_ids'].discard(rec.order_id)

                    else:
                        removed = max(0, old_size - rec.size)

                        if rec.size <= 0:
                            order['ahead_ids'].discard(rec.order_id)

                    order['queue_ahead'] = max(0, order['queue_ahead'] - removed)

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

                    if sample_idx == len(sample_times) - 1:
                        if s['inventory'] > 0:
                            s['cash'] += s['inventory'] * book.best_bid / 1e9
                        elif s['inventory'] < 0:
                            s['cash'] += s['inventory'] * book.best_ask / 1e9

                        s['inventory'] = 0
                        s['bid_order'] = None
                        s['ask_order'] = None
                        s['pnl_history'].append(s['cash'])
                        continue

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

                    can_buy = s['inventory'] < s['max_inventory']
                    can_sell = s['inventory'] > -s['max_inventory']

                    if can_buy and bid >= book.best_ask:
                        s['cash'] -= book.best_ask / 1e9
                        s['inventory'] += 1
                        s['num_trades'] += 1
                        s['bid_order'] = None
                    else:
                        s['bid_order'] = refresh(s['bid_order'], bid, can_buy, book.bids)

                    if can_sell and ask <= book.best_bid:
                        s['cash'] += book.best_bid / 1e9
                        s['inventory'] -= 1
                        s['num_trades'] += 1
                        s['ask_order'] = None
                    else:
                        s['ask_order'] = refresh(s['ask_order'], ask, can_sell, book.asks)

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

                if bid_order is not None and trade_price <= bid_order['price']:
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

                if ask_order is not None and trade_price >= ask_order['price']:
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
    ("20250106", "20250107"),
    ("20250107", "20250108"),
    ("20250108", "20250110"),
    ("20250110", "20250113"),
    ("20250113", "20250114"),
    ("20250114", "20250115"),
    ("20250115", "20250116"),
    ("20250116", "20250117"),
    ("20250117", "20250121"),
    ("20250121", "20250122"),
    ("20250122", "20250123"),
    ("20250123", "20250124"),
    ("20250124", "20250127"),
    ("20250127", "20250128"),
    ("20250128", "20250129"),
    ("20250129", "20250130"),
    ("20250130", "20250131"),
    ("20250131", "20250203"),
    ("20250203", "20250204"),
    ("20250204", "20250205"),
    ("20250205", "20250206"),
    ("20260831", "20260901"),
    ("20260901", "20260902"),
    ("20260902", "20260903"),
    ("20260903", "20260904"),
    ("20260904", "20260908"),
    ("20260908", "20260909"),
    ("20260909", "20260910"),
    ("20260910", "20260911"),
    ("20260911", "20260914"),
    ("20260914", "20260915"),
    ("20260915", "20260916"),
    ("20260916", "20260917"),
    ("20260917", "20260918"),
    ]

    inventory_limits = [10, 25, 50, 100, 250, 500]

    for cal_date, date in dates:
        print(f'\nRunning {date} using calibration {cal_date}...')

        cal_path = f'/volumes/t9/market_making/features/{cal_date}.parquet'
        path = f'/volumes/t9/market_making/features/{date}.parquet'
        mbopath = f'/volumes/t9/market_making/es_mbo/glbx-mdp3-{date}.mbo.dbn.zst'

        lob_cal = orderbook.load(cal_path)
        lob = orderbook.load(path)

        bins = get_imbalance_bins(lob_cal, 10)
        G6 = estimate_G6(lob_cal, bins, 10, 6, config.ticksize)

        lob['time'] = pd.to_datetime(lob['time'], unit='ns')
        lob = lob.sort_values('time').reset_index(drop=True)

        lob['imbalance'] = lob['bs'] / (lob['bs'] + lob['as'])
        lob['wmid'] = lob['imbalance'] * lob['ask'] + (1 - lob['imbalance']) * lob['bid']

        imb_bucket = pd.cut(lob['imbalance'], bins=bins, labels=False, include_lowest=True).to_numpy()
        one_tick = (np.round((lob['ask'] - lob['bid']).to_numpy() / config.ticksize) == 1) & ~np.isnan(imb_bucket)
        bucket = np.where(one_tick, imb_bucket, 0).astype(int)

        lob['mp'] = lob['mid'] + np.where(one_tick, G6[bucket], 0.0)

        lob_sample = lob[['time', 'mid', 'wmid', 'mp']].copy()

        lob_sample['time'] = lob_sample['time'].dt.ceil(config.sample_freq)

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
        )['log_return'].std() * lob_sample['mid']

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

        results['pnl after fees'] = results['pnl'] - config.fee * results['trade count']
        results['date'] = date
        results['calibration date'] = cal_date

        all_results.append(results)
        pd.concat(all_results, ignore_index=True).to_csv('/volumes/t9/market_making/backtest_daily_results.csv', index=False)

        print(results[['max inventory', 'fair value', 'pnl', 'pnl after fees', 'pnl_std', 'trade count', 'pnl volatility', 'return volatility', 'inventory pnl']])

    results = pd.concat(all_results, ignore_index=True)

    summary = (
        results
        .groupby(['max inventory', 'fair value'])
        .agg(
            total_pnl=('pnl', 'sum'),
            mean_pnl=('pnl', 'mean'),
            mean_pnl_after_fees=('pnl after fees', 'mean'),
            pnl_std=('pnl', 'std'),
            total_trades=('trade count', 'sum'),
            mean_trades=('trade count', 'mean'),
            total_inventory_pnl=('inventory pnl', 'sum'),
            mean_inventory_pnl=('inventory pnl', 'mean')
        )
        .reset_index())
    
    summary['gross sharpe'] = summary['mean_pnl'] / summary['pnl_std']
    summary.to_csv('/volumes/t9/market_making/backtest_summary.csv', index=False)

    print(summary)

    print(f'\nTotal runtime: {time.perf_counter() - start:.2f}s')

if __name__ == '__main__':
    main()