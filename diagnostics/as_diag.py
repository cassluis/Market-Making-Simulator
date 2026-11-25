#!/usr/bin/env python3
"""Instrumented re-run of as.py's backtest, with each suspected bug switchable.

Read-only against the strategy folder: it imports orderbook, fvest and as from
there and writes only to ./diag_out.

One pass over the day's MBO file drives every variant at once:

  base  as.py as written
  a     no queue decrement on 'T' -- CME follows every resting fill with a C/M
        on the same order, which the C/M branch already takes off the queue
  b     a size increase loses priority (drop it from the queue ahead); clamp >= 0
  c     stop at the last RTH sample: cancel both quotes, flatten at the touch
  d     microprice G* from a correctly shaped estimator (fixed_G below)
  e     microprice applied causally: calibration-day buckets on every row, no
        future-move filter, no stale value carried across wide-spread rows
  f     a quote that crosses the market fills as a taker at the touch
  all   a+b+c+d+e+f

Inventory-control variant (--inv), run on config 'all' only:

  as.py passes sigma = std of 1 s LOG returns (~5e-5), so the reservation shift
  q*sigma^2*gamma*T_t is ~2e-6 pt per contract: inventory control is off. With
  sigma in price units (sigma_log * mid, ~0.3 pt/sqrt(s)) gamma must shrink by
  ~1e3-1e4. Each gamma in INV_GAMMAS gets k = inv_k(gamma), which keeps the
  zero-inventory spread (2/gamma)ln(1+gamma/k) at as.py's 1.291 pt, so only
  the inventory term (and A-S's sigma^2*gamma*T_t spread term) changes.

  ctrl  as.py's gamma = 0.1, k = 1.5, sigma in log units (= config 'all')
  as    AS.quotes with sigma in price units
  skew  as 'as' but without the sigma^2*gamma*T_t spread widening: only the
        reservation-price shift bites (separates skew from wider quotes)

Fees are not a variant: every row reports pnl_fees = pnl - FEE_PTS * fills.

PnL is split exactly (all in index points, x$50 for dollars):
  pnl = spread + carry_pre + carry_post + liq
  spread      sum sign*(mid_at_fill - fill_px)        edge vs mid when filled
  carry_pre   sum sign*(mid_close - mid_at_fill)       inventory P&L to 16:00
  carry_post  inventory P&L from 16:00 to the final mark
  liq         q_final*(liquidation_px - final_mid)      crossing the spread to exit

Usage:
  python3 -B as_diag.py                       # 20250131 -> 20250203
  python3 -B as_diag.py --original            # as.run_backtest itself (port check)
  python3 -B as_diag.py --cal 20250106 --date 20250107
  python3 -B as_diag.py --cal 20250106 --date 20250107 --inv   # inventory-control sweep
"""
import argparse
import importlib
import os
import sys
import time

import numpy as np
import pandas as pd
import databento as db

ROOT = '/Volumes/T9/Market_making'
sys.path.insert(0, ROOT)

import orderbook                                   # noqa: E402
from fvest import Stoikov, get_imbalance_bins      # noqa: E402
AS = importlib.import_module('as')                 # 'as' is a keyword: no plain import

CFG = AS.config(gamma=0.1, k=1.5)   # the pre-fix as.py values the sweeps were run with
TICK = CFG.ticksize
FEE_PTS = 1.20 / 50            # ~$1.20 per contract per side, in ES index points
MARKOUTS = (1, 10, 60)         # seconds
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diag_out')

LIMITS_BASE = [10, 25, 50, 100, 250, 500]   # as.py's grid, for the port check
LIMITS_ABL = [100, 500]

# name: (flags, which microprice series the 'microprice' strategy quotes around)
CONFIGS = {
    'base': ({}, 'mp'),
    'a':    ({'fix_a': True}, 'mp'),
    'b':    ({'fix_b': True}, 'mp'),
    'c':    ({'close': True}, 'mp'),
    'd':    ({}, 'mp_d'),
    'e':    ({}, 'mp_e'),
    'f':    ({'taker': True}, 'mp'),
    'all':  ({'fix_a': True, 'fix_b': True, 'close': True, 'taker': True}, 'mp_de'),
}

# --inv: gamma for sigma in price units. sigma_px^2 * T_t has a median of ~500-1,500
# pt^2 over RTH samples, so these put the per-contract skew at ~0.01-0.25 pt there
# (about 10x that at the open, 0 at the close).
INV_GAMMAS = [1e-5, 2.5e-5, 5e-5, 1e-4, 2.5e-4]
INV_FVS = ['mid', 'wmid']
INV_SPREAD0 = (2 / CFG.gamma) * np.log(1 + CFG.gamma / CFG.k)    # as.py's 1.291 pt


def inv_k(gamma):
    """k that keeps (2/gamma)ln(1+gamma/k) at as.py's zero-inventory spread."""
    return gamma / np.expm1(gamma * INV_SPREAD0 / 2)


def quotes_skew_only(gamma, k, ticksize, T_t, mid, inventory, volatility):
    """AS.quotes without the volatility**2*gamma*T_t spread term."""
    r_t = mid - inventory * volatility**2 * gamma * T_t
    spread_t = (2 / gamma) * np.log(1 + gamma / k)
    bid = np.floor((r_t - spread_t / 2) / ticksize) * ticksize
    ask = np.ceil((r_t + spread_t / 2) / ticksize) * ticksize
    return bid, ask


# --------------------------------------------------------------------------- #
# fair values
# --------------------------------------------------------------------------- #
def fixed_G(df, bins, n_imb=10, steps=6):
    """Stoikov's G* with the state space written out explicitly.

    States are the n_imb imbalance buckets at a one-tick spread. As in Stoikov's
    code (and mm_sim.prep_data_sym) the next state is the next ONE-TICK row, so a
    move is always +-1 tick and K = [-tick, +tick]. Counts land at explicit
    indices via np.add.at, so nothing can be silently reshaped.
    """
    d = df[['bid', 'ask', 'bs', 'as']]
    d = d[np.round((d['ask'] - d['bid']).to_numpy() / TICK) == 1]
    mid = ((d['bid'] + d['ask']) / 2).to_numpy()
    b = pd.cut(d['bs'] / (d['bs'] + d['as']), bins=bins, labels=False,
               include_lowest=True).to_numpy()
    b, nb = b[:-1], b[1:]
    dm = np.round((mid[1:] - mid[:-1]) / TICK)
    keep = ~np.isnan(b) & ~np.isnan(nb) & (np.abs(dm) <= 1)
    b, nb, dm = b[keep].astype(int), nb[keep].astype(int), dm[keep].astype(int)
    # symmetrise: bucket i <-> n_imb-1-i, move m <-> -m
    b, nb, dm = np.r_[b, n_imb - 1 - b], np.r_[nb, n_imb - 1 - nb], np.r_[dm, -dm]

    Q = np.zeros((n_imb, n_imb))
    R1 = np.zeros((n_imb, 2))
    R2 = np.zeros((n_imb, n_imb))
    still = dm == 0
    np.add.at(Q, (b[still], nb[still]), 1)
    np.add.at(R1, (b[~still], (dm[~still] > 0).astype(int)), 1)
    np.add.at(R2, (b[~still], nb[~still]), 1)
    tot = Q.sum(1) + R1.sum(1)
    Q, R1, R2 = Q / tot[:, None], R1 / tot[:, None], R2 / tot[:, None]

    N = np.linalg.inv(np.eye(n_imb) - Q)
    G1 = N @ R1 @ np.array([-TICK, TICK])
    B = N @ R2
    G, term = G1.copy(), G1.copy()
    for _ in range(steps - 1):
        term = B @ term
        G += term
    return G


def build_fair_values(cal_date, date):
    """as.py main()'s preparation, line for line, plus the d/e/all variants."""
    lob_cal = orderbook.load(f'{ROOT}/features/{cal_date}.parquet')
    lob = orderbook.load(f'{ROOT}/features/{date}.parquet')
    cal_raw = lob_cal.copy()                 # prep_data_sym adds columns in place

    T_cal, _, _ = Stoikov.prep_data_sym(lob_cal, 10, 1, 1)
    T, _, _ = Stoikov.prep_data_sym(lob, 10, 1, 1)
    G6 = np.asarray(Stoikov.getG6(*Stoikov.estimate(T_cal)))

    bins = get_imbalance_bins(cal_raw, 10)
    G_fix = fixed_G(cal_raw, bins)

    T['mp'] = T['mid'] + G6[T['imb_bucket'].astype(int)]
    T['mp_d'] = T['mid'] + G_fix[T['imb_bucket'].astype(int)]

    lob['time'] = pd.to_datetime(lob['time'], unit='ns')
    T['time'] = pd.to_datetime(T['time'], unit='ns')
    lob = lob.sort_values('time').reset_index(drop=True)
    T = T.sort_values('time').reset_index(drop=True)
    lob = pd.merge_asof(lob, T[['time', 'mp', 'mp_d']], on='time', direction='backward')

    lob['imbalance'] = lob['bs'] / (lob['bs'] + lob['as'])
    lob['wmid'] = lob['imbalance'] * lob['ask'] + (1 - lob['imbalance']) * lob['bid']

    # (e) causal: calibration buckets on every row, model only at a one-tick spread
    bk = pd.cut(lob['imbalance'], bins=bins, labels=False, include_lowest=True).to_numpy()
    ok = (np.round((lob['ask'] - lob['bid']).to_numpy() / TICK) == 1) & ~np.isnan(bk)
    bi = np.where(ok, bk, 0).astype(int)
    lob['mp_e'] = lob['mid'] + np.where(ok, G6[bi], 0.0)
    lob['mp_de'] = lob['mid'] + np.where(ok, G_fix[bi], 0.0)

    cols = ['mid', 'wmid', 'mp', 'mp_d', 'mp_e', 'mp_de']
    s = lob[['time'] + cols].copy()
    s['time'] = s['time'].dt.ceil(CFG.sample_freq)
    s = s.groupby('time')[cols].last().reset_index()
    s['log_return'] = np.log(s['mid'] / s['mid'].shift(1))
    s['volatility'] = s.rolling(CFG.vol_window, on='time')['log_return'].std()

    sample_times = s['time'].to_numpy(dtype='datetime64[ns]').astype(np.int64)
    return sample_times, s['volatility'].to_numpy(), {c: s[c].to_numpy() for c in cols}, G6, G_fix


# --------------------------------------------------------------------------- #
# simulator
# --------------------------------------------------------------------------- #
class Tracker:
    """Queue position of one resting 1-lot. Strategies that placed the same price
    on the same event under the same queue rules share one: their queues evolve
    identically, so one update stands in for as.py's per-strategy copies."""
    __slots__ = ('side', 'price', 'q', 'ahead', 'fix_a', 'fix_b', 'owners', 'alive')

    def __init__(self, side, price, q, ahead, fix_a, fix_b):
        self.side, self.price, self.q, self.ahead = side, price, q, ahead
        self.fix_a, self.fix_b = fix_a, fix_b
        self.owners, self.alive = [], True


class Strat:
    __slots__ = ('i', 'config', 'fv_name', 'fv_series', 'fv', 'max_inv', 'fix_a', 'fix_b',
                 'close', 'taker', 'inv', 'cash', 'orders', 'n_fills', 'inv_pnl',
                 'prev_bid', 'prev_ask', 'closed', 'inv_close', 'mid2_close', 'term',
                 'crossings', 'eq', 'inv_hist', 'variant', 'gamma', 'k', 'sig', 'var_spread')

    def __init__(self, i, config, fv_name, fv_series, fv, max_inv, flags,
                 variant='', gamma=CFG.gamma, k=CFG.k, sig=None, var_spread=True):
        self.i, self.config, self.fv_name, self.fv_series = i, config, fv_name, fv_series
        self.fv, self.max_inv = fv, max_inv
        # quoting: sig=None uses simulate()'s shared (log-unit) volatility series
        self.variant, self.gamma, self.k = variant, gamma, k
        self.sig, self.var_spread = sig, var_spread
        self.fix_a = flags.get('fix_a', False)
        self.fix_b = flags.get('fix_b', False)
        self.close = flags.get('close', False)
        self.taker = flags.get('taker', False)
        self.inv, self.cash, self.orders, self.n_fills = 0, 0.0, [None, None], 0
        self.inv_pnl, self.prev_bid, self.prev_ask = 0.0, 0, 0
        self.closed, self.inv_close, self.mid2_close, self.term = False, 0, None, None
        self.crossings, self.eq, self.inv_hist = 0, [], []


def make_strats(fvs, configs, limits_base, limits_abl):
    strats = []
    for cname, (flags, mp_series) in configs.items():
        limits = limits_base if cname == 'base' else limits_abl
        for max_inv in limits:
            for fv_name, series in (('mid', 'mid'), ('wmid', 'wmid'), ('microprice', mp_series)):
                strats.append(Strat(len(strats), cname, fv_name, series, fvs[series],
                                    max_inv, flags))
    return strats


def make_inv_strats(fvs, vols, caps=LIMITS_ABL):
    """Config 'all' x INV_FVS x caps for ctrl, and for 'as'/'skew' at each INV_GAMMAS."""
    flags = CONFIGS['all'][0]
    sig_px = vols * fvs['mid']                      # sigma_log * mid: price units
    variants = [('ctrl', CFG.gamma, CFG.k, None, True)]
    for vname, var_spread in (('as', True), ('skew', False)):
        variants += [(vname, g, inv_k(g), sig_px, var_spread) for g in INV_GAMMAS]
    strats = []
    for vname, g, k, sig, var_spread in variants:
        for max_inv in caps:
            for fv_name in INV_FVS:
                strats.append(Strat(len(strats), 'all', fv_name, fv_name, fvs[fv_name],
                                    max_inv, flags, vname, g, k, sig, var_spread))
    return strats


def simulate(path, sample_times, vols, strats, limit=None):
    book = orderbook.OrderBook()
    live = (set(), set())                  # trackers with owners: bids, asks
    watch = {}                             # order_id -> trackers holding it in .ahead
    fills = {k: [] for k in ('s', 'ts', 'sign', 'px', 'mid2', 'kind')}
    sample_log = []                        # (ts, bid+ask) at each quoting sample
    close_fill = None                      # fills recorded before the 16:00 snapshot
    final_time = sample_times[-1]
    n_samp = len(sample_times)
    sample_idx = 0
    next_ts = sample_times[1] if n_samp > 1 else np.iinfo(np.int64).max
    max_bid, min_ask = -1, np.iinfo(np.int64).max

    def record(s, ts, sign, px, mid2, kind):
        f = fills
        f['s'].append(s.i); f['ts'].append(ts); f['sign'].append(sign)
        f['px'].append(px); f['mid2'].append(mid2); f['kind'].append(kind)

    def drop(s, side):
        tr = s.orders[side]
        if tr is None:
            return
        s.orders[side] = None
        tr.owners.remove(s)
        if not tr.owners:
            tr.alive = False
            live[side].discard(tr)

    def place(s, side, price, allowed, cache):
        tr = s.orders[side]
        if not allowed:
            drop(s, side)
            return
        if tr is not None and tr.price == price:
            return                                  # unchanged: keep queue position
        drop(s, side)
        key = (side, price, s.fix_a, s.fix_b)
        tr = cache.get(key)
        if tr is None:
            resting = (book.bids if side == 0 else book.asks).get(price, {})
            tr = Tracker(side, price, sum(resting.values()), set(resting.keys()),
                         s.fix_a, s.fix_b)
            for oid in tr.ahead:
                watch.setdefault(oid, []).append(tr)
            live[side].add(tr)
            cache[key] = tr
        tr.owners.append(s)
        s.orders[side] = tr

    t0 = time.time()
    n = 0
    for rec in db.DBNStore.from_file(path):
        n += 1
        if limit and n > limit:
            break
        if n % 5_000_000 == 0:
            print(f'  {n:,} records  {time.time() - t0:.0f}s', flush=True)
        ts = rec.ts_event
        a = rec.action.value

        if a == 'C' or a == 'M':
            oid = rec.order_id
            lst = watch.get(oid)
            if lst is not None:
                keep = []
                for tr in lst:
                    if not tr.alive or oid not in tr.ahead:
                        continue
                    lvl = (book.bids if tr.side == 0 else book.asks).get(tr.price)
                    old = lvl.get(oid, 0) if lvl else 0
                    if a == 'C' or rec.price != tr.price:
                        removed = old
                        tr.ahead.discard(oid)
                    elif tr.fix_b and rec.size > old:
                        removed = old                       # back of the queue now
                        tr.ahead.discard(oid)
                    else:
                        removed = old - rec.size if old > rec.size else 0
                        if rec.size <= 0:
                            tr.ahead.discard(oid)
                        else:
                            keep.append(tr)
                    tr.q -= removed
                    if tr.fix_b and tr.q < 0:
                        tr.q = 0
                if keep:
                    watch[oid] = keep
                else:
                    del watch[oid]

        book.handle(rec)

        if ts >= next_ts:
            while sample_idx + 1 < n_samp and sample_times[sample_idx + 1] <= ts:
                sample_idx += 1
            next_ts = (sample_times[sample_idx + 1] if sample_idx + 1 < n_samp
                       else np.iinfo(np.int64).max)
            vol = vols[sample_idx]
            if not np.isnan(vol):
                T_t = (final_time - sample_times[sample_idx]) / 1e9
                bb, ba = book.best_bid, book.best_ask
                last = sample_idx == n_samp - 1
                if last:
                    close_fill = len(fills['s'])
                sample_log.append((ts, bb + ba))
                cache = {}
                for s in strats:
                    if s.closed:
                        continue
                    if s.inv > 0:
                        s.inv_pnl += s.inv * ((bb - s.prev_bid) / 1e9)
                    if s.inv < 0:
                        s.inv_pnl += s.inv * ((ba - s.prev_ask) / 1e9)
                    s.prev_bid, s.prev_ask = bb, ba
                    if last:
                        s.inv_close, s.mid2_close = s.inv, bb + ba
                    if last and s.close:
                        drop(s, 0)
                        drop(s, 1)
                        px = bb if s.inv > 0 else ba
                        s.cash += s.inv * px / 1e9
                        s.term = (ts, bb + ba, px, s.inv)
                        s.closed = True
                        s.eq.append(s.cash)
                        s.inv_hist.append(s.inv)
                        s.inv = 0
                        continue

                    sig = vol if s.sig is None else s.sig[sample_idx]
                    qf = AS.quotes if s.var_spread else quotes_skew_only
                    bid, ask = qf(s.gamma, s.k, TICK, T_t, s.fv[sample_idx], s.inv, sig)
                    bid = int(round(bid * 1e9))
                    ask = int(round(ask * 1e9))
                    ok_b = s.inv < s.max_inv
                    ok_a = s.inv > -s.max_inv

                    if bid >= ba:
                        s.crossings += 1
                    if s.taker and ok_b and bid >= ba:
                        drop(s, 0)
                        s.cash -= ba / 1e9
                        s.inv += 1
                        s.n_fills += 1
                        record(s, ts, 1, ba, bb + ba, 1)
                    else:
                        place(s, 0, bid, ok_b, cache)

                    if ask <= bb:
                        s.crossings += 1
                    if s.taker and ok_a and ask <= bb:
                        drop(s, 1)
                        s.cash += bb / 1e9
                        s.inv -= 1
                        s.n_fills += 1
                        record(s, ts, -1, bb, bb + ba, 1)
                    else:
                        place(s, 1, ask, ok_a, cache)

                    if s.inv > 0:
                        s.eq.append(s.cash + s.inv * bb / 1e9)
                    elif s.inv < 0:
                        s.eq.append(s.cash + s.inv * ba / 1e9)
                    else:
                        s.eq.append(s.cash)
                    s.inv_hist.append(s.inv)

                max_bid = max((tr.price for tr in live[0]), default=-1)
                min_ask = min((tr.price for tr in live[1]), default=np.iinfo(np.int64).max)

        if a == 'T':
            px, sz = rec.price, rec.size
            if (px <= max_bid and live[0]) or (px >= min_ask and live[1]):
                bb_, ba_ = book.best_bid, book.best_ask
                mid2 = bb_ + ba_ if bb_ is not None and ba_ is not None else 2 * px
            if px <= max_bid and live[0]:
                for tr in list(live[0]):
                    if px <= tr.price:
                        if sz - tr.q > 0:
                            for s in tr.owners:
                                s.cash -= tr.price / 1e9
                                s.inv += 1
                                s.n_fills += 1
                                s.orders[0] = None
                                record(s, ts, 1, tr.price, mid2, 0)
                            tr.owners.clear()
                            tr.alive = False
                            live[0].discard(tr)
                        elif not tr.fix_a:
                            tr.q -= sz
            if px >= min_ask and live[1]:
                for tr in list(live[1]):
                    if px >= tr.price:
                        if sz - tr.q > 0:
                            for s in tr.owners:
                                s.cash += tr.price / 1e9
                                s.inv -= 1
                                s.n_fills += 1
                                s.orders[1] = None
                                record(s, ts, -1, tr.price, mid2, 0)
                            tr.owners.clear()
                            tr.alive = False
                            live[1].discard(tr)
                        elif not tr.fix_a:
                            tr.q -= sz

    bb, ba = book.best_bid, book.best_ask
    for s in strats:
        if s.closed:
            continue
        px = bb if s.inv > 0 else ba
        s.cash += s.inv * px / 1e9
        s.term = (ts, bb + ba, px, s.inv)
    print(f'  done: {n:,} records in {time.time() - t0:.0f}s', flush=True)
    fills = {k: np.asarray(v) for k, v in fills.items()}
    fills['pre'] = np.arange(len(fills['s'])) < (close_fill if close_fill is not None
                                                 else len(fills['s']))
    return fills, sample_log


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def summarise(strats, fills, features_path):
    full = pd.read_parquet(features_path, columns=['ts', 'bid_px', 'ask_px'])
    f_ts = full['ts'].to_numpy(np.int64)
    f_mid = (full['bid_px'].to_numpy(np.int64) + full['ask_px'].to_numpy(np.int64)) / 2e9

    def mid_at(t):
        return f_mid[np.clip(np.searchsorted(f_ts, t, side='right') - 1, 0, len(f_ts) - 1)]

    rows = []
    for s in strats:
        m = fills['s'] == s.i if len(fills['s']) else np.zeros(0, bool)
        ts, sign = fills['ts'][m], fills['sign'][m].astype(float)
        px, mid = fills['px'][m] / 1e9, fills['mid2'][m] / 2e9
        _, mid2_T, liq_px, q_T = s.term
        mid_T, liq_px = mid2_T / 2e9, liq_px / 1e9
        mid_c = s.mid2_close / 2e9 if s.mid2_close is not None else mid_T
        pre = fills['pre'][m]

        spread = float((sign * (mid - px)).sum())
        carry_pre = float((sign[pre] * (mid_c - mid[pre])).sum())
        carry_post = float(s.inv_close * (mid_T - mid_c) + (sign[~pre] * (mid_T - mid[~pre])).sum())
        liq = float(q_T * (liq_px - mid_T))
        pnl = s.cash
        inv = np.asarray(s.inv_hist)
        eq = np.asarray(s.eq)

        row = {
            'config': s.config, 'fair value': s.fv_name, 'fv series': s.fv_series,
            'max inventory': s.max_inv, 'pnl': pnl, 'trade count': s.n_fills,
            'pnl_fees': pnl - FEE_PTS * s.n_fills,
            'spread': spread, 'carry_pre': carry_pre, 'carry_post': carry_post, 'liq': liq,
            'residual': pnl - (spread + carry_pre + carry_post + liq),
            'carry share': (carry_pre + carry_post) / pnl if pnl else np.nan,
            'inv at close': s.inv_close, 'final inv marked': q_T,
            'max |inv|': int(np.abs(inv).max()) if len(inv) else 0,
            'time at cap': float((np.abs(inv) >= s.max_inv).mean()) if len(inv) else 0.0,
            'fills after close': int((~pre).sum()), 'taker fills': int((fills['kind'][m] == 1).sum()),
            'crossing quotes': s.crossings,
            'spread/fill': spread / len(ts) if len(ts) else np.nan,
            'pnl volatility': float(np.std(np.diff(eq))) if len(eq) > 1 else np.nan,
            'inventory pnl': s.inv_pnl,
        }
        for h in MARKOUTS:
            mk = sign * (mid_at(ts + h * 1_000_000_000) - mid)
            row[f'markout {h}s'] = float(mk.mean()) if len(mk) else np.nan
        row['realized spread 60s'] = (row['spread/fill'] + row['markout 60s']
                                      if len(ts) else np.nan)
        # quoting parameters and inventory profile (inv is one entry per RTH sample)
        row.update({
            'variant': s.variant, 'gamma': s.gamma, 'k': s.k,
            'buys': int((sign > 0).sum()), 'sells': int((sign < 0).sum()),
            'mean |inv|': float(np.abs(inv).mean()) if len(inv) else 0.0,
            'mean inv': float(inv.mean()) if len(inv) else 0.0,
            'share time long': float((inv > 0).mean()) if len(inv) else 0.0,
            'share time short': float((inv < 0).mean()) if len(inv) else 0.0,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cal', default='20250131')
    ap.add_argument('--date', default='20250203')
    ap.add_argument('--limit', type=int, default=None, help='smoke test: first N records')
    ap.add_argument('--original', action='store_true',
                    help="run as.run_backtest itself on as.py's inventory grid")
    ap.add_argument('--inv', action='store_true',
                    help="inventory-control sweep: config 'all', ctrl/as/skew x INV_GAMMAS")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    mbo = f'{ROOT}/ES_MBO/glbx-mdp3-{args.date}.mbo.dbn.zst'

    t0 = time.time()
    sample_times, vols, fvs, G6, G_fix = build_fair_values(args.cal, args.date)
    print(f'fair values built in {time.time() - t0:.0f}s; {len(sample_times):,} samples')
    np.set_printoptions(precision=4, suppress=True)
    print(f'G6 as.py  : {G6}   G[i]+G[9-i] = {G6 + G6[::-1]}')
    print(f'G fixed   : {G_fix}   G[i]+G[9-i] = {G_fix + G_fix[::-1]}')

    if args.original:
        res = AS.run_backtest(mbo, sample_times,
                              {'mid': fvs['mid'], 'wmid': fvs['wmid'], 'microprice': fvs['mp']},
                              vols, CFG.starting_capital, LIMITS_BASE,
                              CFG.gamma, CFG.k, CFG.ticksize)
        out = os.path.join(OUT, f'original_{args.date}.csv')
        res.to_csv(out, index=False)
        print(res.to_string(index=False))
        print(f'wrote {out}  ({time.time() - t0:.0f}s)')
        return

    if args.inv:
        strats = make_inv_strats(fvs, vols)
        print(f'{len(strats)} strategies: ctrl + as/skew x gamma {INV_GAMMAS}')
        labels = [f'{s.variant}|{s.gamma:g}|{s.fv_name}|{s.max_inv}' for s in strats]
        pre = 'inv_'
    else:
        strats = make_strats(fvs, CONFIGS, LIMITS_BASE, LIMITS_ABL)
        print(f'{len(strats)} strategies across {len(CONFIGS)} configs')
        labels = [f'{s.config}|{s.fv_name}|{s.max_inv}' for s in strats]
        pre = ''
    fills, sample_log = simulate(mbo, sample_times, vols, strats, args.limit)
    summary = summarise(strats, fills, f'{ROOT}/features/{args.date}.parquet')

    tag = args.date + (f'_limit{args.limit}' if args.limit else '')
    summary.to_csv(os.path.join(OUT, f'{pre}summary_{tag}.csv'), index=False)
    pd.DataFrame(fills).to_parquet(os.path.join(OUT, f'{pre}fills_{tag}.parquet'), index=False)
    log = np.asarray(sample_log, dtype=np.int64)
    width = max(len(s.inv_hist) for s in strats)
    inv = np.full((len(strats), width), np.nan)
    for s in strats:
        inv[s.i, :len(s.inv_hist)] = s.inv_hist
    extra = {}
    if args.inv:      # per-sample sigma_px^2 * T_t: skew per contract = gamma * this
        extra = {'sample_times': sample_times,
                 'sig2T': (vols * fvs['mid']) ** 2 * (sample_times[-1] - sample_times) / 1e9}
    np.savez(os.path.join(OUT, f'{pre}paths_{tag}.npz'), sample_ts=log[:, 0],
             mid=log[:, 1] / 2e9, inv=inv, labels=np.array(labels), **extra)

    pd.set_option('display.width', 250)
    cols = ['config', 'fair value', 'max inventory', 'pnl', 'pnl_fees', 'trade count',
            'spread', 'carry_pre', 'carry_post', 'liq', 'residual', 'inv at close',
            'time at cap', 'markout 10s', 'realized spread 60s']
    if args.inv:
        cols = ['variant', 'gamma'] + cols + ['buys', 'sells', 'mean |inv|', 'taker fills']
    print(summary[cols].round(3).to_string(index=False))
    print(f'\nwrote {OUT}  ({time.time() - t0:.0f}s total)')


if __name__ == '__main__':
    main()
