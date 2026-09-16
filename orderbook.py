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

def load(path):
    df = pd.read_parquet(path)
    ny = pd.to_datetime(df.ts, unit='ns', utc=True).dt.tz_convert('America/New_York')
    rth = df[(ny.dt.time >= pd.Timestamp('9:30').time()) &
             (ny.dt.time <  pd.Timestamp('16:00').time())].copy()
    rth[['bid_px','ask_px']] /= 1e9
    rth['mid'] = (rth.bid_px + rth.ask_px) / 2
    return rth.rename(columns={'bid_px':'bid','ask_px':'ask',
                               'bid_sz':'bs','ask_sz':'as','ts':'time'})