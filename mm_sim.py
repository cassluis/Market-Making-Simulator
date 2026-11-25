import numpy as np
import databento as db
import pandas as pd
from sortedcontainers import SortedDict
import statsmodels.api as sm
from scipy.linalg import block_diag

BID = 'B'
ASK = 'A'
F_LAST = 128
n_imb, n_spread, dt = 10, 1, 1
imb = np.arange(n_imb)
pd.options.mode.chained_assignment = None

class OrderBook:
    def __init__(self):
        self.orders = {}
        self.bids = SortedDict(lambda p: -p)
        self.asks = SortedDict()
        self.trades = []
        self.fills = []
        self.unhandled = 0
        self.unknown = {}
        self.bad_trades = []

    @property
    def best_bid(self):
        return self.bids.peekitem(0)[0] if self.bids else None

    @property
    def best_ask(self):
        return self.asks.peekitem(0)[0] if self.asks else None
    
    def add(self, order_id:int, side:str, price:int, size:int):
        self.orders[order_id] = side, price, size
        book = self._side(side)
        if price not in book:
            book[price] = {}
        book[price][order_id] = size

    def cancel(self, order_id:int):
        if order_id not in self.orders:
            self.unhandled += 1
            return
        
        else:
            side, price, _ = self.orders[order_id]
            book = self._side(side)
            del book[price][order_id]
            del self.orders[order_id]
            if not book[price]:
                del book[price]

    def modify(self, order_id:int, side:str, price:int, new_size:int):
        if new_size == 0:
            self.cancel(order_id)
            return

        if order_id not in self.orders:
            self.unhandled += 1
            return
        
        old_price = self.orders[order_id][1]
        book = self._side(side)
        
        if price == old_price:

            if new_size - book[price][order_id] <= 0:
                book[price][order_id] = new_size

            else:
                del book[price][order_id]
                book[price][order_id] = new_size

        else:
            del book[old_price][order_id]
            if not book[old_price]:
                del book[old_price]
            book.setdefault(price, {})[order_id] = new_size
        self.orders[order_id] = side, price, new_size

    def log(self, action:str, timestamp:str, order_id:int, side:str, price:int, size:int):
        if action == 'T':
            self.trades.append([timestamp, price, size, side, self.best_bid, self.best_ask])
        else:
            self.fills.append([timestamp, price, size, side, order_id])

    def clear(self):
        self.orders = {}
        self.bids = SortedDict(lambda p: -p)
        self.asks = SortedDict()
        self.unhandled = 0
        self.trades = []
        self.fills = []
        self.unknown = {}
        self.bad_trades = []

    def handle(self, rec):
        i = rec.action
        if i == 'A':
            self.add(rec.order_id, rec.side, rec.price, rec.size)
        elif i == 'C':
            self.cancel(rec.order_id)
        elif i == 'M':
            self.modify(rec.order_id, rec.side, rec.price, rec.size)
        elif i in ('T', 'F'):
            self.log(i, rec.ts_event, rec.order_id, rec.side, rec.price, rec.size)
        elif i == 'R':
            self.clear()
        else:
            self.unknown[i] = self.unknown.get(i, 0) + 1

    def _side(self, side: str) -> SortedDict:
        return self.bids if side == BID else self.asks

class Stoikov():
    def prep_data_sym(T,n_imb,dt,n_spread):
        ticksize=0.25
        T.spread=T.ask-T.bid

        T['spread']=np.round((T['ask']-T['bid'])/ticksize)*ticksize
        T['mid']=(T['bid']+T['ask'])/2

        T = T.loc[(T.spread <= n_spread*ticksize) & (T.spread>0)]
        T['imb']=T['bs']/(T['bs']+T['as'])

        T['imb_bucket'] = pd.qcut(T['imb'], n_imb, labels=False)
        T['next_mid']=T['mid'].shift(-dt)

        T['next_spread']=T['spread'].shift(-dt)
        T['next_time']=T['time'].shift(-dt)
        T['next_imb_bucket']=T['imb_bucket'].shift(-dt)

        T['dM']=np.round((T['next_mid']-T['mid'])/ticksize*2)*ticksize/2
        T = T.loc[(T.dM <= ticksize*1.1) & (T.dM>=-ticksize*1.1)]

        T2 = T.copy(deep=True)
        T2['imb_bucket']=n_imb-1-T2['imb_bucket']
        T2['next_imb_bucket']=n_imb-1-T2['next_imb_bucket']
        T2['dM']=-T2['dM']
        T2['mid']=-T2['mid']
        T3=pd.concat([T,T2])
        T3.index = pd.RangeIndex(len(T3.index)) 

        return T, T3, ticksize
    
    def estimate(T):
        no_move=T[T['dM']==0]
        no_move_counts=no_move.pivot_table(index=[ 'next_imb_bucket'], 
                        columns=['spread', 'imb_bucket'], 
                        values='time',
                        fill_value=0, 
                        aggfunc='count').unstack()
        Q_counts=np.resize(np.array(no_move_counts[0:(n_imb*n_imb)]),(n_imb,n_imb))

        for i in range(1,n_spread):
            Qi=np.resize(np.array(no_move_counts[(i*n_imb*n_imb):(i+1)*(n_imb*n_imb)]),(n_imb,n_imb))
            Q_counts=block_diag(Q_counts,Qi)

        move_counts=T[(T['dM']!=0)].pivot_table(index=['dM'], 
                            columns=['spread', 'imb_bucket'], 
                            values='time',
                            fill_value=0, 
                            aggfunc='count').unstack()

        R_counts=np.resize(np.array(move_counts),(n_imb*n_spread,4))
        T1=np.concatenate((Q_counts,R_counts),axis=1).astype(float)
        for i in range(0,n_imb*n_spread):
            T1[i]=T1[i]/T1[i].sum()
        Q=T1[:,0:(n_imb*n_spread)]
        R1=T1[:,(n_imb*n_spread):]

        K=np.array([-0.25, -0.125, 0.125, 0.25])
        move_counts=T[(T['dM']!=0)].pivot_table(index=['spread','imb_bucket'], 
                        columns=['next_spread', 'next_imb_bucket'], 
                        values='time',
                        fill_value=0, 
                        aggfunc='count')

        R2_counts=np.resize(np.array(move_counts),(n_imb*n_spread,n_imb*n_spread))
        T2=np.concatenate((Q_counts,R2_counts),axis=1).astype(float)

        for i in range(0,n_imb*n_spread):
            T2[i]=T2[i]/T2[i].sum()
        R2=T2[:,(n_imb*n_spread):]
        Q2=T2[:,0:(n_imb*n_spread)]
        G1=np.dot(np.dot(np.linalg.inv(np.eye(n_imb*n_spread)-Q),R1),K)
        B=np.dot(np.linalg.inv(np.eye(n_imb*n_spread)-Q),R2)
        
        return G1,B

    def getG6(G1,B,steps=6):
        G2 = G1+np.dot(B,G1)
        G_steps = G2
        for i in range(3,steps+1):
            BB = np.dot(B,B)
            for j in range(3,i):
                BB = np.dot(BB,B)
            G_steps += np.dot(BB,G1)
        return G_steps


def forward_mid_change(df, horizons):
    """{'fwd_<h>': mid(t + h seconds) - mid(t)} for each h, by TIMESTAMP not row count.

    Stoikov's figures are drawn at wall-clock horizons (10s / 60s / 180s), so the lookup
    has to be on `time` rather than a row offset -- ES carries a wildly different number
    of events per second than BAC does, and per-session too. Rows whose horizon runs off
    the end of the session get NaN and drop out of the averages.

    Assumes `df` is ONE session and sorted by time; searching across a day boundary would
    silently pair an afternoon quote with the next morning's.
    """
    ts = df['time'].to_numpy(np.int64)
    mid = df['mid'].to_numpy(float)
    out = {}
    for h in horizons:
        j = np.searchsorted(ts, ts + int(h * 1_000_000_000), side='left')
        ok = j < len(ts)
        fwd = np.full(len(ts), np.nan)
        fwd[ok] = mid[j[ok]]
        out[f'fwd_{h}'] = fwd - mid
    return out


def plot_microprice_panels(df, horizons=(10, 60, 180), imb_buckets=10, spreads_wanted=4,
                           dt_steps=1, tick=0.25, path='microprice_panels.png',
                           title=None, steps=6, ylim='spread'):
    """Stoikov Figures 4-5: G* = P_micro - M_t against the empirical mid-price change.

    One panel per (spread, horizon). Each plots, against imbalance:
        blue   mid adjustment            -- flat at zero by definition
        orange weighted-mid adjustment   -- spread * (imbalance - 0.5), the linear benchmark
        red    G*                        -- the micro-price adjustment, per imbalance bucket
        green  empirical mean mid-price change at that horizon, with +/-1.96 SE bars

    G* is read out of getG6() at state `spread_rank * imb_buckets + imb_bucket`, which is
    the ordering estimate() builds with block_diag -- spread-major. Reshaping the other
    way round is silently correct at one spread and silently wrong at more than one.

    The empirical curve uses the RAW (unsymmetrised) rows: it is a measurement, not a
    model, so it should be free to come out asymmetric. G* comes from the symmetrised
    frame, as in the paper.
    """
    import matplotlib.pyplot as plt

    global n_imb, n_spread
    n_imb, n_spread = imb_buckets, spreads_wanted

    work = df.copy()
    for k, v in forward_mid_change(work, horizons).items():
        work[k] = v

    T, T3, _ = Stoikov.prep_data_sym(work, n_imb, dt_steps, n_spread)
    G1, B = Stoikov.estimate(T3)
    G6 = np.asarray(Stoikov.getG6(G1, B, steps))

    spreads = sorted(T['spread'].unique())
    if len(spreads) != n_spread:
        raise ValueError(f'{len(spreads)} spread levels present but n_spread={n_spread}; '
                         f'estimate() sized its state vector for {n_spread}')
    G = G6.reshape(n_spread, n_imb)          # spread-major -- see docstring

    nrow, ncol = len(spreads), len(horizons)
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.0 * ncol, 3.8 * nrow),
                             squeeze=False)
    grid = np.linspace(0, 1, 100)

    for r, s in enumerate(spreads):
        sub = T[T['spread'] == s]
        for c, h in enumerate(horizons):
            ax = axes[r][c]
            col = f'fwd_{h}'
            g = sub.dropna(subset=[col]).groupby('imb_bucket')
            x = g['imb'].mean()
            y = g[col].mean()
            se = g[col].sem()

            ax.axhline(0.0, color='tab:blue', lw=1.4, label='Mid adj')
            ax.plot(grid, s * (grid - 0.5), color='tab:orange', lw=1.4,
                    label='Weighted mid adj')
            ax.errorbar(x, y, yerr=1.96 * se, color='tab:green', lw=1.2,
                        marker='+', capsize=3, label=f'empirical {h}s midprice change')
            ax.plot(x, G[r][x.index.to_numpy(int)], 'o', color='tab:red',
                    ms=5, label='$G^*$')

            ax.set_xlabel('Imbalance')
            ax.set_title(f'{title or "microprice"} vs empirical, '
                         f'spread={int(round(s / tick))}, {h}s', fontsize=10)
            if ylim == 'spread':
                # Paper convention: the weighted-mid line spans the axis, so G* stays
                # readable. Thin-spread panels push the empirical curve off-scale --
                # that IS the result, and the caption should say so.
                ax.set_ylim(-0.6 * s, 0.6 * s)
            ax.legend(loc='upper left', fontsize=7, frameon=True)
            ax.grid(alpha=0.25)

    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=150)
        plt.close(fig)
        print(f'saved {path}')
    return fig


def main():
    fit_df  = load('/volumes/t9/market_making/features/20250106.parquet')
    test_df = load('/volumes/t9/market_making/features/20250107.parquet')

    # ---- fit G6 on day 1 (symmetrised frame) ----------------------------
    _, T_fit, _ = Stoikov.prep_data_sym(fit_df, n_imb, dt, n_spread)
    G1, B = Stoikov.estimate(T_fit)
    G6 = Stoikov.getG6(G1, B)

    spreads = sorted(T_fit['spread'].unique())
    assert len(spreads) == n_spread, f"{len(spreads)} spreads vs n_spread={n_spread}"
    col = {s: i for i, s in enumerate(spreads)}
    G = np.reshape(G6, (n_imb, n_spread))

    def apply_micro(T):
        T = T[T['spread'].isin(spreads)].copy()      
        T['microprice'] = T['mid'] + G[
            T['imb_bucket'].to_numpy(int),
            T['spread'].map(col).to_numpy(int),
        ]
        return T

    def report(T, label):
        TICK = 0.25
        target = T['dM'].to_numpy()
        mse_mid = (target ** 2).mean()
        rows = []
        for name in ('mid', 'wmid', 'microprice'):
            adj = (T[name] - T['mid']).to_numpy()
            mse = ((target - adj) ** 2).mean()
            beta = np.cov(target, adj)[0, 1] / adj.var() if adj.var() > 0 else np.nan
            rows.append({'day': label, 'model': name, 'n': len(target),
                         'rmse_ticks': mse / TICK,
                         'r2_vs_mid': 1 - mse / mse_mid,
                         'beta': beta})
        return pd.DataFrame(rows)

    T_in,  _, _ = Stoikov.prep_data_sym(fit_df,  n_imb, dt, n_spread)
    T_out, _, _ = Stoikov.prep_data_sym(test_df, n_imb, dt, n_spread)

    print(pd.concat([report(apply_micro(T_in),  'in-sample'),
                     report(apply_micro(T_out), 'out-of-sample')]
                    ).to_string(index=False, float_format='%.6f'))

def replay(path, limit=None):
    book = OrderBook()
    rows = []
    store = db.DBNStore.from_file(path)
    for i, rec in enumerate(store):
        if limit and i >= limit:
            break
        book.handle(rec)
        if rec.flags & F_LAST:
            bb, ba = book.best_bid, book.best_ask
            if bb is not None and ba is not None:
                rows.append((rec.ts_event, bb, ba, sum(book.bids[bb].values()), sum(book.asks[ba].values())))
    return book, rows

def bucket(df, n=100):
    g = df.assign(bucket=np.arange(len(df)) // n).groupby('bucket')
    return pd.DataFrame({
        'ofi': g.e.sum(),
        'depth':(g.bid_sz.mean() + g.ask_sz.mean()) / 2,
        'dmid': g.mid.last() - g.mid.first(),
        'ts': g.ts.first(),
        'n': g.size(),
        'bucket_ts': g.ts.last() - g.ts.first(),
    })

def load(path):
    df = pd.read_parquet(path)
    ny = pd.to_datetime(df.ts, unit='ns', utc=True).dt.tz_convert('America/New_York')
    rth = df[(ny.dt.time >= pd.Timestamp('9:00').time()) &
             (ny.dt.time <  pd.Timestamp('14:30').time())].copy()
    rth[['bid_px','ask_px']] /= 1e9
    rth['mid'] = (rth.bid_px + rth.ask_px) / 2
    rth['imbalance'] = rth.bid_sz / (rth.bid_sz + rth.ask_sz)
    rth['wmid'] = rth.bid_px*(1-rth.imbalance) + rth.ask_px*rth.imbalance
    return rth.rename(columns={'bid_px':'bid','ask_px':'ask',
                               'bid_sz':'bs','ask_sz':'as','ts':'time'})
if __name__ == '__main__':
    main()