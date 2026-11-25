"""
Split the combined ES.FUT + CL.FUT MBO download in ES_CL_MBO into
per-product daily files in ES_FUT_MBO and CL_MBO.
(Run es_front_month.py afterwards to build the front-month ES files in ES_MBO.)

Every record is kept (all expiries and spreads). Records are routed by
instrument_id using the symbol mappings in each file's DBN header, and
each output gets its own header containing only that product's symbols.

Streams the raw 56-byte MBO records through numpy, so memory stays low.
Resumable: skips any day whose ES and CL outputs both already exist.

Usage:  python3 split_es_cl_mbo.py              (all days)
        python3 split_es_cl_mbo.py 20260906     (selected days)
"""

import os
import re
import sys
import glob
import time
import struct
from types import SimpleNamespace

import numpy as np
import zstandard
import databento_dbn as dbn

BASE_DIR = r'/Volumes/T9/Market_making'
IN_DIR = os.path.join(BASE_DIR, 'ES_CL_MBO')
OUT_DIRS = {
    'ES': os.path.join(BASE_DIR, 'ES_FUT_MBO'),
    'CL': os.path.join(BASE_DIR, 'CL_MBO'),
}
PRODUCTS = list(OUT_DIRS)

MBO_RTYPE = 0xA0
MBO_SIZE = 56
CHUNK_RECORDS = 1_000_000

# Only the record header is needed for routing; the rest is passed through untouched
REC_DTYPE = np.dtype([
    ('length', 'u1'),
    ('rtype', 'u1'),
    ('publisher_id', '<u2'),
    ('instrument_id', '<u4'),
    ('body', f'V{MBO_SIZE - 8}'),
])


def product_of(raw_symbol):
    """ESZ6, ESU6-ESZ6, UD:ES:... -> ES   CLV6, UD:CL: GN ..., WS:XS 02M CL-BZ X6 -> CL"""
    hits = [p for p in PRODUCTS if re.search(rf'\b{p}', raw_symbol)]
    return hits[0] if len(hits) == 1 else None


def read_header(reader):
    prefix = reader.read(8)
    if prefix[:3] != b'DBN':
        raise ValueError('not a DBN stream')
    length = struct.unpack('<I', prefix[4:8])[0]
    return dbn.Metadata.decode(prefix + read_exact(reader, length))


def read_exact(reader, n):
    parts = []
    got = 0
    while got < n:
        b = reader.read(n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return b''.join(parts)


def split_metadata(meta):
    """Returns ({product: encoded header}, {product: instrument_id array})."""
    mappings = {p: [] for p in PRODUCTS}
    ids = {p: set() for p in PRODUCTS}
    unknown = []

    for raw, intervals in meta.mappings.items():
        product = product_of(raw)
        if product is None:
            unknown.append(raw)
            continue
        mappings[product].append(SimpleNamespace(
            raw_symbol=raw,
            intervals=[SimpleNamespace(**iv) for iv in intervals],
        ))
        ids[product].update(int(iv['symbol']) for iv in intervals)

    for raw in list(meta.partial) + list(meta.not_found):
        if product_of(raw) is None:
            unknown.append(raw)

    if unknown:
        raise ValueError(f'Cannot classify symbols as ES or CL: {unknown[:20]}')

    overlap = ids['ES'] & ids['CL']
    if overlap:
        raise ValueError(f'instrument_ids mapped to both ES and CL: {sorted(overlap)[:20]}')

    headers = {}
    for product in PRODUCTS:
        sub = dbn.Metadata(
            dataset=meta.dataset,
            start=meta.start,
            end=meta.end,
            limit=meta.limit,
            stype_in=meta.stype_in,
            stype_out=meta.stype_out,
            schema=meta.schema,
            symbols=[s for s in meta.symbols if s.startswith(product)],
            partial=[s for s in meta.partial if product_of(s) == product],
            not_found=[s for s in meta.not_found if product_of(s) == product],
            mappings=mappings[product],
            ts_out=meta.ts_out,
            version=meta.version,
        )
        headers[product] = sub.encode()

    id_arrays = {p: np.fromiter(ids[p], dtype='<u4') for p in PRODUCTS}
    return headers, id_arrays


def split_file(path):
    filename = os.path.basename(path)
    out_paths = {p: os.path.join(OUT_DIRS[p], filename) for p in PRODUCTS}

    if all(os.path.exists(p) for p in out_paths.values()):
        print(f'{filename}: ES and CL outputs already exist, skipping')
        return

    start = time.time()
    tmp_paths = {p: out_paths[p] + '.partial' for p in PRODUCTS}
    counts = {p: 0 for p in PRODUCTS}
    total = 0

    with open(path, 'rb') as src:
        reader = zstandard.ZstdDecompressor().stream_reader(src)
        headers, id_arrays = split_metadata(read_header(reader))

        files = {p: open(tmp_paths[p], 'wb') for p in PRODUCTS}
        writers = {
            p: zstandard.ZstdCompressor(level=3, threads=-1, write_checksum=True)
                .stream_writer(files[p], closefd=False)
            for p in PRODUCTS
        }

        try:
            for p in PRODUCTS:
                writers[p].write(headers[p])

            while True:
                chunk = read_exact(reader, CHUNK_RECORDS * MBO_SIZE)
                if not chunk:
                    break
                if len(chunk) % MBO_SIZE:
                    raise ValueError(f'{filename}: truncated record at end of file')

                recs = np.frombuffer(chunk, dtype=REC_DTYPE)
                if not (np.all(recs['rtype'] == MBO_RTYPE) and np.all(recs['length'] == MBO_SIZE // 4)):
                    raise ValueError(f'{filename}: found non-MBO records, aborting')

                matched = np.zeros(len(recs), dtype=bool)
                for p in PRODUCTS:
                    mask = np.isin(recs['instrument_id'], id_arrays[p])
                    matched |= mask
                    counts[p] += int(mask.sum())
                    writers[p].write(recs[mask].tobytes())

                if not matched.all():
                    missing = np.unique(recs['instrument_id'][~matched])
                    raise ValueError(f'{filename}: records with unmapped instrument_ids {missing[:20].tolist()}')

                total += len(recs)

            for p in PRODUCTS:
                writers[p].close()
                files[p].close()

        except BaseException:
            for p in PRODUCTS:
                files[p].close()
                if os.path.exists(tmp_paths[p]):
                    os.remove(tmp_paths[p])
            raise

    if sum(counts.values()) != total:
        raise ValueError(f'{filename}: ES + CL ({sum(counts.values()):,}) != total ({total:,})')

    for p in PRODUCTS:
        os.replace(tmp_paths[p], out_paths[p])

    sizes = {p: os.path.getsize(out_paths[p]) / 1e6 for p in PRODUCTS}
    print(
        f'{filename}: total {total:,} | '
        f'ES {counts["ES"]:,} ({sizes["ES"]:,.0f} MB) | '
        f'CL {counts["CL"]:,} ({sizes["CL"]:,.0f} MB) | '
        f'{time.time() - start:,.0f}s',
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
        split_file(path)

    print('=' * 80)
    print('SPLIT COMPLETE')
    print('=' * 80)


if __name__ == '__main__':
    main()
