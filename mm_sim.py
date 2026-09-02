import numpy as np
import databento as db
import pandas as pd
from sortedcontainers import SortedDict
import statsmodels.api as sm
from scipy.linalg import block_diag
import matplotlib.pyplot as plt

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
        ticksize=0.01
        T.spread=T.ask-T.bid
        # adds the spread and mid prices
        T['spread']=np.round((T['ask']-T['bid'])/ticksize)*ticksize
        T['mid']=(T['bid']+T['ask'])/2
        #filter out spreads >= n_spread
        T = T.loc[(T.spread <= n_spread*ticksize) & (T.spread>0)]
        T['imb']=T['bs']/(T['bs']+T['as'])
        #discretize imbalance into percentiles
        T['imb_bucket'] = pd.qcut(T['imb'], n_imb, labels=False)
        T['next_mid']=T['mid'].shift(-dt)
        #step ahead state variables
        T['next_spread']=T['spread'].shift(-dt)
        T['next_time']=T['time'].shift(-dt)
        T['next_imb_bucket']=T['imb_bucket'].shift(-dt)
        # step ahead change in price
        T['dM']=np.round((T['next_mid']-T['mid'])/ticksize*2)*ticksize/2
        T = T.loc[(T.dM <= ticksize*1.1) & (T.dM>=-ticksize*1.1)]
        # symetrize data
        T2 = T.copy(deep=True)
        T2['imb_bucket']=n_imb-1-T2['imb_bucket']
        T2['next_imb_bucket']=n_imb-1-T2['next_imb_bucket']
        T2['dM']=-T2['dM']
        T2['mid']=-T2['mid']
        T3=pd.concat([T,T2])
        T3.index = pd.RangeIndex(len(T3.index)) 
        return T3,ticksize
    
    def estimate(T):
        no_move=T[T['dM']==0]
        no_move_counts=no_move.pivot_table(index=[ 'next_imb_bucket'], 
                        columns=['spread', 'imb_bucket'], 
                        values='time',
                        fill_value=0, 
                        aggfunc='count').unstack()
        Q_counts=np.resize(np.array(no_move_counts[0:(n_imb*n_imb)]),(n_imb,n_imb))
        # loop over all spreads and add block matrices
        for i in range(1,n_spread):
            Qi=np.resize(np.array(no_move_counts[(i*n_imb*n_imb):(i+1)*(n_imb*n_imb)]),(n_imb,n_imb))
            Q_counts=block_diag(Q_counts,Qi)
        #print Q_counts
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

        K=np.array([-0.01, -0.005, 0.005, 0.01])
        move_counts=T[(T['dM']!=0)].pivot_table(index=['spread','imb_bucket'], 
                        columns=['next_spread', 'next_imb_bucket'], 
                        values='time',
                        fill_value=0, 
                        aggfunc='count') #.unstack()

        R2_counts=np.resize(np.array(move_counts),(n_imb*n_spread,n_imb*n_spread))
        T2=np.concatenate((Q_counts,R2_counts),axis=1).astype(float)

        for i in range(0,n_imb*n_spread):
            T2[i]=T2[i]/T2[i].sum()
        R2=T2[:,(n_imb*n_spread):]
        Q2=T2[:,0:(n_imb*n_spread)]
        G1=np.dot(np.dot(np.linalg.inv(np.eye(n_imb*n_spread)-Q),R1),K)
        B=np.dot(np.linalg.inv(np.eye(n_imb*n_spread)-Q),R2)
        
        return G1,B,Q,Q2,R1,R2,K

    def compute_G6(G1, B):
        G, term = G1.copy(), G1.copy()
        for _ in range(5):
            term = np.dot(B, term)
            G = G + term
        return G

def main():
    
    client = db.Historical('db-GMNASrhiH3XaMedRmDh6jfRb3rLqL')
    for d in ['2025-01-08','2025-01-09','2025-01-10','2025-01-13','2025-01-14',
            '2025-01-15','2025-01-16','2025-01-17']:
        data = client.timeseries.get_range(
            dataset='GLBX.MDP3', schema='mbo', symbols='CLG5',
            stype_in='raw_symbol',
            start=f'{d}T14:00', end=f'{d}T19:30',
        )
        data.to_file(f'f:/market_making/CL_MBO/clg5-{d.replace("-","")}.dbn.zst')

    data.to_file('f:/market_making/CL_MBO/clg5.dbn.zst')
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