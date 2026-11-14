"""Seven-day Manhattan subway archive, Nov 14–21, 2026, New York time.

Full raw line-group feeds are retained; scope.json defines Manhattan-only analysis.
Daily SQLite is a download index, not an expanded prediction database.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mta_explore as m

NY = ZoneInfo('America/New_York')
START = datetime(2026, 11, 14, tzinfo=NY).timestamp()
END = datetime(2026, 11, 21, tzinfo=NY).timestamp()
INTERVAL = 30
SLOTS = int((END - START) / INTERVAL)
RUN = ROOT / 'data/20261114_20261121_manhattan'
BASE = 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/'
URLS = {k: BASE + 'nyct%2Fgtfs' + ('-' + k if k != 'numbered' else '')
        for k in ['numbered', 'ace', 'bdfm', 'g', 'jz', 'l', 'nqrw']}
URLS['alerts'] = m.URLS['alerts']
MIN_FREE = 5 * 1024**3


def iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, NY).isoformat()


def atomic(path, body):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('wb') as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def status(state, **extra):
    print(f'{iso()} {state}', flush=True)
    atomic(RUN / 'status.json', json.dumps(dict(status=state, updated_at=iso(),
        pid=os.getpid(), scheduled_start=iso(START), scheduled_end=iso(END),
        interval_seconds=INTERVAL, scope='Manhattan stations, all subway routes',
        **extra), indent=2).encode())


def next_slot(now):
    # Never backfill elapsed intervals after a restart. Allow the current slot
    # if within one second of its target to absorb ordinary scheduler jitter.
    return max(0, math.ceil((now - START - 1) / INTERVAL))


def connect(day):
    (day / 'raw').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(day / 'index.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('''CREATE TABLE IF NOT EXISTS polls(
        slot INTEGER, feed TEXT, scheduled REAL, started REAL, received REAL,
        http_status INTEGER, source_ts INTEGER, bytes INTEGER, gzip_bytes INTEGER,
        sha256 TEXT, raw_path TEXT, error TEXT, PRIMARY KEY(slot,feed))''')
    return db


def reserve(db, slot):
    pending = []
    for feed in URLS:
        cur = db.execute('INSERT OR IGNORE INTO polls(slot,feed,scheduled,error) VALUES(?,?,?,?)',
                         (slot, feed, START + slot*INTERVAL, 'interrupted before completion'))
        if cur.rowcount:
            pending.append(feed)
    db.commit()
    return pending


def download(url):
    if time.time() >= END:
        now = time.time()
        return dict(started=now, received=now, body=None, http_status=None,
                    error='window ended before request', headers={})
    return m.fetch(url)


def save_result(db, day, slot, feed, result):
    body = result['body']; stamp = None; raw = None; digest = None; packed = b''
    error = result['error']
    if body is not None:
        packed = gzip.compress(body, mtime=0)
        raw = f'raw/{slot:05d}_{feed}.gz'
        atomic(day / raw, packed)
        digest = hashlib.sha256(body).hexdigest()
        try:
            if feed == 'alerts':
                stamp = int(json.loads(body)['header']['timestamp'])
            else:
                msg = m.gtfs.FeedMessage(); msg.ParseFromString(body)
                if not msg.header.HasField('timestamp'):
                    raise ValueError('missing timestamp')
                stamp = msg.header.timestamp
        except Exception as exc:
            error = f'parse: {exc}'
    db.execute('''UPDATE polls SET started=?,received=?,http_status=?,source_ts=?,
        bytes=?,gzip_bytes=?,sha256=?,raw_path=?,error=? WHERE slot=? AND feed=?''',
        (result['started'],result['received'],result['http_status'],stamp,
         len(body) if body is not None else 0,len(packed),digest,raw,error,slot,feed))
    db.commit()


def summarize():
    totals = dict(attempted=0, successful=0, failed_or_interrupted=0, raw_bytes=0,
                  compressed_bytes=0, first_request=None, last_request=None)
    feeds = {}
    for path in sorted(RUN.glob('20*/index.sqlite')):
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        for row in db.execute('''SELECT feed,count(*),sum(error IS NULL AND http_status=200),
            sum(bytes),sum(gzip_bytes),min(started),max(started) FROM polls GROUP BY feed'''):
            feed,n,ok,size,packed,first,last = row
            f = feeds.setdefault(feed,dict(attempted=0,successful=0))
            f['attempted'] += n; f['successful'] += ok
            totals['attempted'] += n; totals['successful'] += ok
            totals['raw_bytes'] += size or 0; totals['compressed_bytes'] += packed or 0
            if first is not None:
                totals['first_request'] = min(totals['first_request'] or first, first)
                totals['last_request'] = max(totals['last_request'] or last, last)
        db.close()
    totals['failed_or_interrupted'] = totals['attempted'] - totals['successful']
    totals['expected_requests'] = SLOTS * len(URLS)
    totals['unattempted_requests'] = totals['expected_requests'] - totals['attempted']
    totals['feeds'] = feeds
    for key in ['first_request','last_request']:
        if totals[key] is not None: totals[key] = iso(totals[key])
    return totals


def metadata():
    if (RUN / 'scope.json').exists(): return
    r = m.fetch(m.STATIONS_URL)
    if r['error']: raise RuntimeError(r['error'])
    rows = json.loads(r['body'])
    scope = {r['gtfs_stop_id']:r['stop_name'] for r in rows if r['borough']=='M'}
    if not scope: raise ValueError('Empty Manhattan scope')
    atomic(RUN/'stations_source.json',r['body'])
    atomic(RUN/'scope.json',json.dumps(scope,indent=2).encode())
    atomic(RUN/'manifest.json',json.dumps(dict(start=iso(START),end=iso(END),
        interval=INTERVAL,urls=URLS,scope='All Manhattan station IDs, both directions, all routes',
        raw_scope='Full line-group feeds and subway alerts, including out-of-borough data',
        overlap='A route may appear in multiple feeds. Deduplicate by trip, stop and source timestamp before analysis.',
        storage='gzip raw responses plus daily SQLite request index; parse predictions offline'),indent=2).encode())


def acquire_lock(lock):
    if os.name == 'nt':
        import msvcrt
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: return False
    else:
        import fcntl
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
    return True


def export_day(name):
    """Materialize one day's Manhattan observations without network access."""
    if len(name)!=8 or not name.isdigit(): raise ValueError('Use YYYYMMDD')
    day = RUN / name
    out = RUN / 'exports' / name
    if out.exists(): raise FileExistsError(f'Export already exists: {out}')
    index = sqlite3.connect((day/'index.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    index.row_factory = sqlite3.Row
    scope = json.loads((RUN/'scope.json').read_text())
    (out/'raw').mkdir(parents=True)
    (out/'scope.json').write_text(json.dumps(scope,indent=2))
    db = m.database(out/'observations.sqlite')
    db.execute('CREATE TABLE source_polls(poll_id INTEGER PRIMARY KEY, feed TEXT, slot INTEGER)')
    try:
        for row in index.execute('SELECT * FROM polls WHERE error IS NULL ORDER BY slot,feed'):
            body = gzip.decompress((day/row['raw_path']).read_bytes())
            assert hashlib.sha256(body).hexdigest()==row['sha256']
            if row['feed']=='alerts':
                f = json.loads(body)
                m.ROUTES = {s['route_id'] for e in f.get('entity',[]) for s in e.get('alert',{}).get('informed_entity',[]) if s.get('route_id')}
                kind = 'alerts'
            else:
                f = m.gtfs.FeedMessage(); f.ParseFromString(body)
                m.ROUTES = {e.trip_update.trip.route_id for e in f.entity if e.HasField('trip_update')}
                m.ROUTES.update(e.vehicle.trip.route_id for e in f.entity if e.HasField('vehicle'))
                kind = 'trains'
            m.ingest(db,out,kind,dict(started=row['started'],received=row['received'],
                http_status=row['http_status'],headers={},error=None,body=body),scope)
            pid = db.execute('SELECT max(id) FROM polls').fetchone()[0]
            db.execute('INSERT INTO source_polls VALUES(?,?,?)',(pid,row['feed'],row['slot']))
            db.commit()
        db.execute('CREATE VIEW unique_predictions AS SELECT DISTINCT source_ts,trip_key,route,stop,eta,departure FROM predictions')
        db.commit()
    finally:
        db.close(); index.close()
    print(f'Exported Manhattan predictions and vehicles to {out}; full-line notices retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',action='store_true')
    parser.add_argument('--export-day',metavar='YYYYMMDD',help='Offline Manhattan SQLite export for a single day; needs extra disk space')
    args = parser.parse_args()
    RUN.mkdir(parents=True,exist_ok=True)
    if args.export_day:
        export_day(args.export_day); return
    if args.summary:
        print(json.dumps(summarize(),indent=2)); return
    with (RUN/'run.lock').open('a+b') as lock:
        if not acquire_lock(lock):
            print('Collection already running.'); return
        if time.time() >= END:
            status('completed',summary=summarize()); return
        awake = subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())]) if sys.platform=='darwin' else None
        db = None
        try:
            metadata()
            status('waiting' if time.time()<START else 'collecting')
            current_day = None
            with ThreadPoolExecutor(max_workers=len(URLS)) as pool:
                while time.time() < END:
                    slot = next_slot(time.time())
                    target = START + slot*INTERVAL
                    if slot >= SLOTS: break
                    while time.time() < target:
                        time.sleep(min(10,max(0,target-time.time())))
                    if time.time() >= END: break
                    # Sleep/wake or clock jumps must not backfill old slots.
                    if time.time() - target >= INTERVAL: continue
                    if shutil.disk_usage(RUN).free < MIN_FREE:
                        status('paused_low_disk',minimum_free_bytes=MIN_FREE)
                        time.sleep(30); continue
                    name = datetime.fromtimestamp(target,NY).strftime('%Y%m%d')
                    if name != current_day:
                        if db: db.close()
                        current_day = name; day = RUN/name; db = connect(day)
                    feeds = reserve(db,slot)
                    futures = {f:pool.submit(download,URLS[f]) for f in feeds}
                    for f,future in futures.items(): save_result(db,day,slot,f,future.result())
                    status('collecting',last_slot=slot,last_slot_time=iso(target),
                           free_bytes=shutil.disk_usage(RUN).free)
                    # Prevent a fast iteration from scheduling the same slot twice.
                    while time.time() < target+INTERVAL and time.time()<END:
                        time.sleep(min(1,max(0,target+INTERVAL-time.time())))
            if db: db.close(); db=None
            status('completed',summary=summarize())
        except BaseException as exc:
            status('failed',error=str(exc)); raise
        finally:
            if db: db.close()
            if awake: awake.terminate()

if __name__=='__main__': main()
