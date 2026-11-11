"""Local, bounded MTA sampling and exploratory analysis. Python 3.9+."""
import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as gtfs

URLS = {
    'trains': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm',
    'alerts': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json',
}
STATIONS_URL = 'https://data.ny.gov/resource/39hk-dx4f.json?$limit=1000'
ROUTES = {'B', 'D', 'F', 'FX', 'M'}  # FX retained separately: F express variant.


def utc(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).isoformat()


def fetch(url):
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MTA-DATA-TEST/0.1',
                                                   'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=12) as response:
            body = response.read()
            return {'started': start, 'received': time.time(), 'body': body,
                    'http_status': response.status, 'headers': dict(response.headers), 'error': None}
    except Exception as exc:
        return {'started': start, 'received': time.time(), 'body': None,
                'http_status': getattr(exc, 'code', None), 'headers': {},
                'error': '{}: {}'.format(type(exc).__name__, exc)}


def station_scope(rows):
    """Geographic bounds, not an assumption about today's F/M service pattern."""
    north = next(float(r['gtfs_latitude']) for r in rows if r['gtfs_stop_id'] == 'A24')
    return {r['gtfs_stop_id']: r['stop_name'] for r in rows
            if r['borough'] == 'M' and float(r['gtfs_latitude']) <= north}


def parent_stop(stop):
    return stop[:-1] if stop.endswith(('N', 'S')) else stop


def database(path):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS polls(
      id INTEGER PRIMARY KEY, kind TEXT, started REAL, received REAL, http_status INTEGER,
      source_ts INTEGER, sha256 TEXT, raw_path TEXT, headers TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS predictions(
      poll_id INTEGER, source_ts INTEGER, trip_key TEXT, route TEXT, stop TEXT,
      eta INTEGER, departure INTEGER, PRIMARY KEY(poll_id,trip_key,stop));
    CREATE TABLE IF NOT EXISTS vehicles(
      poll_id INTEGER, source_ts INTEGER, trip_key TEXT, route TEXT, stop TEXT,
      status TEXT, status_explicit INTEGER, vehicle_ts INTEGER,
      PRIMARY KEY(poll_id,trip_key));
    CREATE TABLE IF NOT EXISTS alerts(
      poll_id INTEGER, alert_id TEXT, event_key TEXT, alert_type TEXT,
      created INTEGER, updated INTEGER, active_periods TEXT, active_at_source INTEGER,
      routes TEXT, selectors TEXT, title TEXT, description TEXT,
      payload TEXT, PRIMARY KEY(poll_id,alert_id));
    CREATE INDEX IF NOT EXISTS pred_lookup ON predictions(trip_key,stop,source_ts);
    ''')
    return db


def trip_key(trip):
    # Entity IDs can change between snapshots. Use service date and trip identity.
    return '|'.join((trip.start_date, trip.route_id, trip.trip_id, trip.start_time))


def translated(field):
    entries = field.get('translation', [])
    return next((e.get('text', '') for e in entries if e.get('language') == 'en'),
                entries[0].get('text', '') if entries else '')


def event_key(entity_id):
    # MTA appends a mutable status rank to lmm:alert IDs, but not all ID formats.
    parts = entity_id.split(':')
    if len(parts) == 4 and parts[:2] == ['lmm', 'alert'] and parts[-1].isdigit():
        return ':'.join(parts[:-1])
    return entity_id


def active_at(periods, source_ts):
    return int(not periods or any(p.get('start', 0) <= source_ts < p.get('end', float('inf'))
                                  for p in periods))


def parse_trains(db, pid, body, scope):
    feed = gtfs.FeedMessage()
    feed.ParseFromString(body)
    if not feed.header.HasField('timestamp'):
        raise ValueError('Train feed has no timestamp')
    stamp = feed.header.timestamp
    for entity in feed.entity:
        if entity.HasField('trip_update'):
            update = entity.trip_update
            if update.trip.route_id not in ROUTES:
                continue
            for stop in update.stop_time_update:
                if parent_stop(stop.stop_id) not in scope:
                    continue
                eta = stop.arrival.time if stop.HasField('arrival') and stop.arrival.HasField('time') else None
                dep = stop.departure.time if stop.HasField('departure') and stop.departure.HasField('time') else None
                db.execute('INSERT OR REPLACE INTO predictions VALUES(?,?,?,?,?,?,?)',
                           (pid, stamp, trip_key(update.trip), update.trip.route_id, stop.stop_id, eta, dep))
        if entity.HasField('vehicle'):
            v = entity.vehicle
            if v.trip.route_id not in ROUTES or parent_stop(v.stop_id) not in scope:
                continue
            # GTFS default is IN_TRANSIT_TO, but only meaningful with stop sequence.
            status = gtfs.VehiclePosition.VehicleStopStatus.Name(v.current_status) if v.HasField('current_stop_sequence') else 'UNKNOWN'
            db.execute('INSERT OR REPLACE INTO vehicles VALUES(?,?,?,?,?,?,?,?)',
                       (pid, stamp, trip_key(v.trip), v.trip.route_id, v.stop_id, status,
                        int(v.HasField('current_status')), v.timestamp if v.HasField('timestamp') else None))
    return stamp


def parse_alerts(db, pid, body):
    feed = json.loads(body)
    stamp = int(feed['header']['timestamp'])
    for entity in feed.get('entity', []):
        alert = entity.get('alert', {})
        selectors = alert.get('informed_entity', [])
        routes = sorted({s['route_id'] for s in selectors if s.get('route_id')})
        if not ROUTES.intersection(routes):
            continue
        mercury = alert.get('transit_realtime.mercury_alert', {})
        periods = alert.get('active_period', [])
        # Retain whole-line alerts: a Brooklyn incident can affect Manhattan trains.
        db.execute('INSERT OR REPLACE INTO alerts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (pid, entity['id'], event_key(entity['id']), mercury.get('alert_type'),
                    mercury.get('created_at'), mercury.get('updated_at'), json.dumps(periods),
                    active_at(periods, stamp), json.dumps(routes), json.dumps(selectors),
                    translated(alert.get('header_text', {})), translated(alert.get('description_text', {})),
                    json.dumps(alert, ensure_ascii=False)))
    return stamp


def ingest(db, run, kind, result, scope):
    cur = db.execute('INSERT INTO polls(kind,started,received,http_status,headers,error) VALUES(?,?,?,?,?,?)',
                     (kind, result['started'], result['received'], result['http_status'],
                      json.dumps(result['headers']), result['error']))
    pid = cur.lastrowid
    body = result['body']
    if body is not None:
        raw = Path('raw') / ('{:05d}_{}.{}.gz'.format(pid, kind, 'pb' if kind == 'trains' else 'json'))
        with gzip.open(run / raw, 'wb') as out:
            out.write(body)
        db.execute('UPDATE polls SET raw_path=?,sha256=? WHERE id=?',
                   (str(raw), hashlib.sha256(body).hexdigest(), pid))
        db.execute('SAVEPOINT parsing')
        try:
            stamp = parse_trains(db, pid, body, scope) if kind == 'trains' else parse_alerts(db, pid, body)
            db.execute('UPDATE polls SET source_ts=? WHERE id=?', (stamp, pid))
            db.execute('RELEASE parsing')
        except Exception as exc:
            db.execute('ROLLBACK TO parsing')
            db.execute('RELEASE parsing')
            db.execute('UPDATE polls SET error=? WHERE id=?', ('parse: ' + str(exc), pid))
    db.commit()
    record = db.execute('SELECT source_ts,error FROM polls WHERE id=?', (pid,)).fetchone()
    age = round(result['received'] - record[0], 1) if record[0] else None
    print('{} {} source_age_s={} {}'.format(utc(), kind, age, record[1] or 'saved'), flush=True)


def transition(prev, row, max_gap=90):
    """Conservative observation interval, NOT an independently measured arrival."""
    if not prev or row['stop'] != prev['stop']:
        return None
    if prev['status'] not in ('IN_TRANSIT_TO', 'INCOMING_AT') or row['status'] != 'STOPPED_AT':
        return None
    if not row['status_explicit'] or not 0 < row['source_ts'] - prev['source_ts'] <= max_gap:
        return None
    return prev['source_ts'], row['source_ts']


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def analyze(run):
    db = database(run / 'observations.sqlite')
    db.row_factory = sqlite3.Row
    scope = json.loads((run / 'scope.json').read_text())
    polls = [dict(r) for r in db.execute('SELECT * FROM polls ORDER BY id')]
    good = {p['id'] for p in polls if not p['error'] and p['source_ts'] is not None
            and -5 <= p['received'] - p['source_ts'] <= 120}
    last, seen, events = {}, set(), []
    for raw in db.execute('SELECT * FROM vehicles ORDER BY poll_id,trip_key'):
        row = dict(raw)
        if row['poll_id'] not in good or row['vehicle_ts'] is None or not -5 <= row['source_ts'] - row['vehicle_ts'] <= 120:
            continue
        key = row['trip_key']
        prev = last.get(key)
        if prev and row['source_ts'] <= prev['source_ts']:
            continue  # repeated or out-of-order source data is not another observation
        bounds = transition(prev, row)
        event_id = (key, row['stop'])
        if bounds and event_id not in seen:
            lo, hi = bounds
            seen.add(event_id)
            ref = db.execute('''SELECT p.eta,p.source_ts FROM predictions p JOIN polls q ON q.id=p.poll_id
              WHERE trip_key=? AND stop=? AND p.source_ts < ? AND eta-p.source_ts BETWEEN 240 AND 360
              AND q.error IS NULL AND q.received-q.source_ts BETWEEN -5 AND 120
              ORDER BY p.source_ts LIMIT 1''', (key, row['stop'], lo)).fetchone()
            events.append(dict(trip_key=key, route=row['route'], stop=row['stop'],
                               station=scope.get(parent_stop(row['stop']), ''),
                               observation_start=lo, observation_end=hi, midpoint=(lo+hi)/2,
                               reference_eta=ref['eta'] if ref else None,
                               eta_error_proxy_min=((lo+hi)/2-ref['eta'])/60 if ref else None,
                               method='observed_transit_to_stopped_transition_NOT_ground_truth'))
        last[key] = row
    write_csv(run / 'arrival_candidates.csv', events,
              ['trip_key','route','stop','station','observation_start','observation_end','midpoint',
               'reference_eta','eta_error_proxy_min','method'])
    changes = [dict(r) for r in db.execute('''SELECT trip_key,route,stop,COUNT(DISTINCT source_ts) AS snapshots,
       MIN(eta) AS earliest_eta,MAX(eta) AS latest_eta,(MAX(eta)-MIN(eta))/60.0 AS eta_range_minutes
       FROM predictions WHERE eta IS NOT NULL GROUP BY trip_key,route,stop HAVING COUNT(DISTINCT source_ts)>1''')]
    for c in changes:
        c['station'] = scope.get(parent_stop(c['stop']), '')
    write_csv(run / 'eta_changes.csv', changes,
              ['trip_key','route','stop','station','snapshots','earliest_eta','latest_eta','eta_range_minutes'])
    observed_stations = [dict(r) for r in db.execute('''SELECT route,stop,COUNT(DISTINCT trip_key) AS trips,
        COUNT(*) AS prediction_records FROM predictions GROUP BY route,stop ORDER BY route,stop''')]
    for row in observed_stations:
        row['station'] = scope.get(parent_stop(row['stop']), '')
    write_csv(run / 'observed_stations.csv', observed_stations,
              ['route','stop','station','trips','prediction_records'])
    alerts = [dict(r) for r in db.execute('SELECT a.*,p.source_ts,p.received FROM alerts a JOIN polls p ON p.id=a.poll_id ORDER BY a.poll_id')]
    for alert in alerts:
        alert['active_at_collection'] = active_at(json.loads(alert['active_periods']), alert['received'])
    write_csv(run / 'alerts.csv', alerts, ['poll_id','source_ts','received','alert_id','event_key','alert_type',
              'created','updated','active_periods','active_at_source','active_at_collection','routes','selectors','title','description'])
    groups = {}
    for e in events:
        groups.setdefault((e['route'],e['stop']), []).append(e)
    station_stats = []
    for (route,stop), es in sorted(groups.items()):
        es.sort(key=lambda e:e['midpoint'])
        errors = [e['eta_error_proxy_min'] for e in es if e['eta_error_proxy_min'] is not None]
        gaps = [(b['midpoint']-a['midpoint'])/60 for a,b in zip(es,es[1:])]
        station_stats.append(dict(route=route, stop=stop, station=scope.get(parent_stop(stop),''),
          observed_arrival_candidates=len(es), matched_eta_count=len(errors),
          mean_eta_error_proxy_min=statistics.mean(errors) if errors else None,
          mean_detected_event_gap_min=statistics.mean(gaps) if gaps else None,
          note='Unvalidated estimates; missed trains can inflate event gaps. Not actual service frequency.'))
    write_csv(run / 'station_summary.csv', station_stats,
              ['route','stop','station','observed_arrival_candidates','matched_eta_count',
               'mean_eta_error_proxy_min','mean_detected_event_gap_min','note'])
    counts = {name: db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
              for name in ('predictions','vehicles','alerts')}
    summary = {'run':str(run), 'counts':counts, 'feeds':{}, 'arrival_candidates':len(events),
               'eta_matched_candidates':sum(e['reference_eta'] is not None for e in events),
               'tracked_trip_stops':len(changes),
               'positive_eta_range_trip_stops':sum(c['eta_range_minutes']>0 for c in changes),
               'actual_delay_measured':False}
    summary['vehicle_status_counts'] = dict(db.execute('SELECT status,COUNT(*) FROM vehicles GROUP BY status').fetchall())
    summary['prediction_counts_by_route'] = dict(db.execute('SELECT route,COUNT(*) FROM predictions GROUP BY route').fetchall())
    summary['distinct_station_directions'] = db.execute('SELECT COUNT(DISTINCT stop) FROM predictions').fetchone()[0]
    summary['raw_compressed_bytes'] = sum(p.stat().st_size for p in (run/'raw').iterdir())
    for kind in URLS:
        ps = [p for p in polls if p['kind']==kind]
        ages = [p['received']-p['source_ts'] for p in ps if p['source_ts'] is not None]
        summary['feeds'][kind] = dict(requests=len(ps), errors=sum(bool(p['error']) for p in ps),
            distinct_source_timestamps=len({p['source_ts'] for p in ps if p['source_ts'] is not None}),
            min_source_age_seconds=min(ages) if ages else None, max_source_age_seconds=max(ages) if ages else None)
    (run / 'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines = ['# MTA exploratory run', '', 'Run: '+str(run), '',
      '**These are exploratory observations, not validated actual arrival/delay measurements.**', '',
      '## Collection', '', '```json', json.dumps(summary,indent=2), '```', '',
      '## Interpretation', '',
      '- ETA range means forecast revisions, not actual delay.',
      '- Arrival candidates require fresh, increasing source timestamps and an explicit transit-to-stopped transition at the same stop.',
      '- A train already stopped when collection begins is left-censored and is excluded.',
      '- ETA error uses an earlier forecast showing 4–6 minutes remaining. Short runs may have no eligible reference.',
      '- Event gaps can include missed trains; they are not validated headways or trains/hour.',
      '- Alert active periods include future planned work. active_at_source is relative to the alert feed timestamp, not download time.',
      '- active_at_collection evaluates the published periods at collection time; neither flag establishes the ground-truth duration of an incident.',
      '- Route/time overlap with an alert is an association, not proof that the alert caused a specific train delay.',
      '- Future/stale vehicle timestamps and stale feed snapshots are excluded from arrival estimates, but retained in raw data.',
      '- The two feed source timestamps can differ significantly. Successful downloads do not establish freshness.',
      '- An unchanged alert timestamp alone cannot distinguish an event-driven publisher with no new notices from a stale feed.',
      '- No timetable comparisons are performed. Longer runs and station observation are needed for conclusions.', '',
      '## Largest ETA revisions (not actual delays)', '', '|Route|Stop|Station|ETA range, min|', '|---|---|---|---|']
    for c in sorted(changes,key=lambda c:c['eta_range_minutes'],reverse=True)[:15]:
        lines.append('|{}|{}|{}|{:.2f}|'.format(c['route'],c['stop'],c['station'],c['eta_range_minutes']))
    (run / 'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    db.close()
    print(json.dumps(summary,indent=2),flush=True)
    return summary


def collect(args):
    run = Path(args.output) / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    (run / 'raw').mkdir(parents=True)
    meta = fetch(STATIONS_URL)
    if meta['error']:
        raise RuntimeError('Cannot establish station scope: '+meta['error'])
    rows = json.loads(meta['body'])
    (run / 'stations_source.json').write_bytes(meta['body'])
    scope = station_scope(rows)
    (run / 'scope.json').write_text(json.dumps(scope,indent=2,ensure_ascii=False),encoding='utf-8')
    (run / 'manifest.json').write_text(json.dumps(dict(started=utc(),interval=args.interval,
        duration_seconds=args.minutes*60,urls=URLS,station_source=STATIONS_URL,
        routes=sorted(ROUTES),scope='Manhattan stations at/south of A24, including B08/B06; route-filtered per observation',
        max_source_age_for_arrival_estimates=120),indent=2),encoding='utf-8')
    db = database(run / 'observations.sqlite')
    start = time.monotonic()
    deadline = start + args.minutes*60
    slot = 0
    print('RUN_DIRECTORY='+str(run.resolve()),flush=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            while time.monotonic() < deadline:
                target = start + slot*args.interval
                if target >= deadline:
                    time.sleep(max(0,deadline-time.monotonic()))
                    break
                time.sleep(max(0,target-time.monotonic()))
                results = {kind: pool.submit(fetch,url) for kind,url in URLS.items()}
                for kind,future in results.items():
                    ingest(db,run,kind,future.result(),scope)
                slot = max(slot+1, int((time.monotonic()-start)//args.interval)+1)
    except KeyboardInterrupt:
        print('Stopped by user; keeping captured data.',flush=True)
    finally:
        db.close()
        analyze(run)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    c = sub.add_parser('collect')
    c.add_argument('--minutes',type=float,default=5)
    c.add_argument('--interval',type=float,default=30)
    c.add_argument('--output',default='data')
    a = sub.add_parser('analyze')
    a.add_argument('run',type=Path)
    args = parser.parse_args()
    if args.command=='collect':
        if args.minutes<=0 or args.interval<10:
            parser.error('minutes must be positive; interval must be at least 10 seconds')
        collect(args)
    else:
        analyze(args.run)


if __name__=='__main__':
    main()
