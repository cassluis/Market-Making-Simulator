"""Download the official mbp-1 feed for every ES session held in ES_MBO/.

Mirrors the MBO batch job exactly -- GLBX.MDP3, symbols ES.c.0, stype_in
continuous, stype_out instrument_id, one UTC day per file -- so the two feeds
carry the same instrument and the same MDP3 sequence numbers, which is what
recon_check.py joins on.

Uses the batch API rather than streaming get_range: the streaming endpoint
delivers this volume at ~3 MB/min, which is ~11 hours for the set, while batch
prepares the files server-side and serves them at full speed. Same price.

Cost-guarded: the job is priced with metadata.get_cost first and the run aborts
before it can exceed BUDGET_USD. Resumable -- days already on disk are excluded
from the requested range, and download() skips files it already has.

Usage:
    python fetch_mbp1.py --dry-run    # price it, submit nothing
    python fetch_mbp1.py              # submit, wait, download
    python fetch_mbp1.py --resume     # re-attach to the newest pending job
"""

import os
import sys
import glob
import time
import datetime as dt

import databento as db

MBO_DIR = r'F:/market_making/ES_MBO'
OUT_DIR = r'F:/market_making/ES_MBP1'
LEGACY_MBP1_DIR = r'C:/Users/cassc/Downloads/GLBX-20260822-5SNJX4LUJ4'

API_KEY = 'db-GMNASrhiH3XaMedRmDh6jfRb3rLqL'
DATASET = 'GLBX.MDP3'
SCHEMA = 'mbp-1'
SYMBOLS = ['ES.c.0']
STYPE_IN = 'continuous'

BUDGET_USD = 50.0                 # hard ceiling given by the user
JOB_END_NS = 1738875600000000000  # 2025-02-06T21:00Z -- end of the MBO job
DAY_NS = 86_400_000_000_000
POLL_SECS = 20


def day_of(path):
    for part in os.path.basename(path).split('-'):
        d = part.split('.')[0]
        if len(d) == 8 and d.isdigit():
            return d
    return os.path.basename(path)


def have(day):
    for d in (OUT_DIR, LEGACY_MBP1_DIR):
        if glob.glob(os.path.join(d, f'*{day}*.mbp-1.dbn.zst')):
            return True
    return False


def day_start_ns(day):
    d = dt.datetime.strptime(day, '%Y%m%d').replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp()) * 1_000_000_000


def wait_and_download(client, job_id):
    print(f'\njob {job_id} submitted; waiting for it to finish...')
    t0 = time.time()
    last = None
    while True:
        det = client.batch.get_job_details(job_id)
        state = det.get('state')
        if state != last:
            print(f'  [{time.time() - t0:>6.0f}s] state={state}')
            last = state
        if state == 'done':
            break
        if state in ('expired', 'cancelled', 'failed'):
            print(f'job ended in state {state!r} -- nothing downloaded.')
            return 1
        time.sleep(POLL_SECS)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f'\ndownloading into {OUT_DIR} ...')
    t1 = time.time()
    paths = client.batch.download(job_id, output_dir=OUT_DIR)
    got = [p for p in paths if str(p).endswith('.dbn.zst')]
    mb = sum(os.path.getsize(p) for p in got) / 1e6
    print(f'  {len(got)} data files, {mb:,.0f} MB in {time.time() - t1:.0f}s')

    # batch download nests files under <job_id>/ -- flatten so recon_check
    # picks them up from a single directory.
    moved = 0
    for p in got:
        dest = os.path.join(OUT_DIR, os.path.basename(p))
        if os.path.abspath(str(p)) != os.path.abspath(dest):
            os.replace(str(p), dest)
            moved += 1
    if moved:
        print(f'  flattened {moved} files into {OUT_DIR}')
    return 0


def main(argv):
    client = db.Historical(API_KEY)

    if '--resume' in argv:
        jobs = [j for j in client.batch.list_jobs()
                if j.get('schema') == SCHEMA and j.get('dataset') == DATASET]
        if not jobs:
            print('no pending mbp-1 job to resume.')
            return 1
        job = sorted(jobs, key=lambda j: j.get('ts_received', ''))[-1]
        return wait_and_download(client, job['id'])

    days = sorted({day_of(p) for p in
                   glob.glob(os.path.join(MBO_DIR, '*.mbo.dbn.zst'))})
    todo = [d for d in days if not have(d)]
    print(f'{len(days)} ES sessions in {MBO_DIR}')
    print(f'{len(days) - len(todo)} already have mbp-1, {len(todo)} to fetch')
    if not todo:
        print('nothing to do.')
        return 0

    start = day_start_ns(todo[0])
    end = min(day_start_ns(todo[-1]) + DAY_NS, JOB_END_NS)
    print(f'range: {np_ts(start)} -> {np_ts(end)}  '
          f'({todo[0]} .. {todo[-1]}, {len(todo)} sessions)')

    cost = client.metadata.get_cost(dataset=DATASET, schema=SCHEMA,
                                    symbols=SYMBOLS, stype_in=STYPE_IN,
                                    start=start, end=end)
    print(f'cost : ${cost:.2f} of ${BUDGET_USD:.2f} budget')
    if cost > BUDGET_USD:
        print(f'ABORT: ${cost:.2f} exceeds the ${BUDGET_USD:.2f} budget.')
        return 1
    if '--dry-run' in argv:
        print('dry run -- nothing submitted.')
        return 0

    job = client.batch.submit_job(
        dataset=DATASET, schema=SCHEMA, symbols=SYMBOLS,
        stype_in=STYPE_IN, stype_out='instrument_id',
        start=start, end=end,
        encoding='dbn', compression='zstd',
        split_duration='day', delivery='download',
    )
    return wait_and_download(client, job['id'])


def np_ts(ns):
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).isoformat()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
