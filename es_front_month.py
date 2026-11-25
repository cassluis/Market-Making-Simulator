"""
Build front-month-only ES MBO files for orderbook replay / backtesting.

For each all-contract file in ES_FUT_MBO (output of split_es_cl_mbo.py), picks
the outright ES contract with the most traded volume that day and writes only
its records to ES_MBO under the same filename. This matches the single-contract
ES.c.0 files already in ES_MBO, so es_mbo_to_parquet.py and as.py can replay
them into one OrderBook.

Resumable: skips any day whose ES_MBO output already exists.

Usage:  python3 es_front_month.py              (all days)
        python3 es_front_month.py 20260910     (selected days)
"""

import os
import re
import sys
import glob
import time
from collections import Counter
from types import SimpleNamespace

import numpy as np
import zstandard
import databento_dbn as dbn

from split_es_cl_mbo import read_header, read_exact, MBO_SIZE, CHUNK_RECORDS

BASE_DIR = r'/Volumes/T9/Market_making'
IN_DIR = os.path.join(BASE_DIR, 'ES_FUT_MBO')
OUT_DIR = os.path.join(BASE_DIR, 'ES_MBO')

OUTRIGHT = re.compile(r'ES[FGHJKMNQUVXZ]\d')

MBO_DTYPE = np.dtype([
    ('length', 'u1'),
    ('rtype', 'u1'),
    ('publisher_id', '<u2'),
    ('instrument_id', '<u4'),
    ('ts_event', '<u8'),
    ('order_id', '<u8'),
    ('price', '<i8'),
    ('size', '<u4'),
    ('flags', 'u1'),
    ('channel_id', 'u1'),
    ('action', 'S1'),
    ('side', 'S1'),
    ('ts_recv', '<u8'),
    ('ts_in_delta', '<i4'),
    ('sequence', '<u4'),
])
assert MBO_DTYPE.itemsize == MBO_SIZE


def iter_records(reader):
    while True:
        chunk = read_exact(reader, CHUNK_RECORDS * MBO_SIZE)
        if not chunk:
            return
        if len(chunk) % MBO_SIZE:
            raise ValueError('truncated record at end of file')
        yield np.frombuffer(chunk, dtype=MBO_DTYPE)


def outright_ids(meta):
    """{instrument_id: symbol} for outright ES contracts (no spreads)."""
    return {
        int(iv['symbol']): raw
        for raw, intervals in meta.mappings.items() if OUTRIGHT.fullmatch(raw)
        for iv in intervals
    }


def traded_volume(path):
    """Returns (meta, Counter of traded volume per outright symbol)."""
    with open(path, 'rb') as src:
        reader = zstandard.ZstdDecompressor().stream_reader(src)
        meta = read_header(reader)
        symbols = outright_ids(meta)
        ids = np.fromiter(symbols, dtype='<u4')
        volume = Counter()

        for recs in iter_records(reader):
            trades = recs[(recs['action'] == b'T') & np.isin(recs['instrument_id'], ids)]
            for iid, size in zip(trades['instrument_id'].tolist(), trades['size'].tolist()):
                volume[symbols[iid]] += size

    return meta, volume


def front_month_header(meta, symbol):
    sub = dbn.Metadata(
        dataset=meta.dataset,
        start=meta.start,
        end=meta.end,
        limit=meta.limit,
        stype_in=dbn.SType.RAW_SYMBOL,
        stype_out=meta.stype_out,
        schema=meta.schema,
        symbols=[symbol],
        partial=[],
        not_found=[],
        mappings=[SimpleNamespace(
            raw_symbol=symbol,
            intervals=[SimpleNamespace(**iv) for iv in meta.mappings[symbol]],
        )],
        ts_out=meta.ts_out,
        version=meta.version,
    )
    return sub.encode()


def build_file(path):
    filename = os.path.basename(path)
    out_path = os.path.join(OUT_DIR, filename)

    if os.path.exists(out_path):
        print(f'{filename}: already exists in ES_MBO, skipping')
        return

    start = time.time()
    meta, volume = traded_volume(path)
    if not volume:
        raise ValueError(f'{filename}: no outright ES trades found')

    ranked = volume.most_common()
    symbol, front_vol = ranked[0]
    iids = np.array(
        [int(iv['symbol']) for iv in meta.mappings[symbol]], dtype='<u4'
    )

    tmp_path = out_path + '.partial'
    kept = 0

    try:
        with open(path, 'rb') as src, open(tmp_path, 'wb') as dst:
            reader = zstandard.ZstdDecompressor().stream_reader(src)
            read_header(reader)
            cctx = zstandard.ZstdCompressor(level=3, threads=-1, write_checksum=True)

            with cctx.stream_writer(dst, closefd=False) as writer:
                writer.write(front_month_header(meta, symbol))
                for recs in iter_records(reader):
                    mine = recs[np.isin(recs['instrument_id'], iids)]
                    kept += len(mine)
                    writer.write(mine.tobytes())

    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    os.replace(tmp_path, out_path)

    share = front_vol / sum(volume.values())
    runner_up = f'{ranked[1][0]} {ranked[1][1]:,}' if len(ranked) > 1 else 'none'
    print(
        f'{filename}: {symbol} (vol {front_vol:,}, {share:.1%} of outright volume; '
        f'next {runner_up}) | {kept:,} records -> ES_MBO | {time.time() - start:,.0f}s',
        flush=True,
    )


def main():
    paths = sorted(
        p for p in glob.glob(os.path.join(IN_DIR, 'glbx-mdp3-*.mbo.dbn.zst'))
        if not os.path.basename(p).startswith('._')
    )

    days = sys.argv[1:]
    if days:
        paths = [p for p in paths if any(d in os.path.basename(p) for d in days)]

    if not paths:
        print(f'No MBO files found in {IN_DIR}')
        return

    print(f'Found {len(paths)} files', flush=True)

    for path in paths:
        build_file(path)

    print('=' * 80)
    print('FRONT MONTH COMPLETE')
    print('=' * 80)


if __name__ == '__main__':
    main()
