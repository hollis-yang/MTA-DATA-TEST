import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import collect_week_20261114 as w


class WeekTests(unittest.TestCase):
    def test_window_and_resume(self):
        self.assertEqual(w.SLOTS,20160)
        self.assertIn('si',w.URLS)
        self.assertEqual(w.SLOTS*len(w.URLS),181440)
        self.assertEqual(w.next_slot(w.START-50),0)
        self.assertEqual(w.next_slot(w.START+0.1),0)
        self.assertEqual(w.next_slot(w.START+45),2)
        self.assertEqual(w.next_slot(w.END),w.SLOTS)

    def test_reservation_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); db=w.connect(p)
            self.assertEqual(len(w.reserve(db,0)),9)
            db.close();db=w.connect(p)
            self.assertEqual(w.reserve(db,0),[])
            self.assertEqual(db.execute('select count(*) from polls').fetchone()[0],9)
            db.close()

    def test_save_preserves_raw_and_records_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); db=w.connect(p); w.reserve(db,0)
            body=json.dumps({'header':{'timestamp':123},'entity':[]}).encode()
            r=dict(started=100,received=101,http_status=200,error=None,body=body)
            w.save_result(db,p,0,'alerts',r)
            row=db.execute("select raw_path,error,source_ts from polls where feed='alerts'").fetchone()
            self.assertEqual(gzip.decompress((p/row[0]).read_bytes()),body)
            self.assertEqual(row[1:],(None,123))
            w.save_result(db,p,0,'numbered',dict(r,body=b'invalid'))
            self.assertTrue(db.execute("select error from polls where feed='numbered'").fetchone()[0].startswith('parse:'))
            db.close()

    def test_no_network_after_end(self):
        with patch.object(w.time,'time',return_value=w.END),patch.object(w.m,'fetch') as fetch:
            self.assertIsNotNone(w.download('unused')['error'])
            fetch.assert_not_called()

    def test_metadata_all_boroughs(self):
        with tempfile.TemporaryDirectory() as d,patch.object(w,'RUN',Path(d)):
            body=json.dumps([dict(gtfs_stop_id='A01',stop_name='Uptown',borough='M'),dict(gtfs_stop_id='Q01',stop_name='Queens',borough='Q')]).encode()
            with patch.object(w.m,'fetch',return_value=dict(body=body,error=None)):
                w.metadata()
            self.assertEqual(json.loads((Path(d)/'scope.json').read_text()),{'A01':'Uptown','Q01':'Queens'})

    def test_export_keeps_all_boroughs_and_unknown_stops(self):
        from google.transit import gtfs_realtime_pb2 as g
        from contextlib import redirect_stdout
        from io import StringIO
        import sqlite3
        with tempfile.TemporaryDirectory() as d, patch.object(w,'RUN',Path(d)), redirect_stdout(StringIO()):
            scope={'A01':'Manhattan','Q01':'Queens','B01':'Brooklyn','X01':'Bronx','S01':'Staten Island'}
            (Path(d)/'scope.json').write_text(json.dumps(scope))
            day=Path(d)/'20261114';db=w.connect(day);w.reserve(db,0)
            feed=g.FeedMessage();feed.header.gtfs_realtime_version='2.0';feed.header.timestamp=123
            e=feed.entity.add();e.id='trip';e.trip_update.trip.route_id='SI';e.trip_update.trip.trip_id='example'
            stops=[s+'N' for s in scope]+['NEWN']
            for name in stops:
                stop=e.trip_update.stop_time_update.add();stop.stop_id=name;stop.arrival.time=400
            w.save_result(db,day,0,'si',dict(started=124,received=125,http_status=200,error=None,body=feed.SerializeToString()))
            db.close()
            w.export_day('20261114')
            out=Path(d)/'exports/20261114'
            db=sqlite3.connect(out/'observations.sqlite')
            self.assertEqual({r[0] for r in db.execute('select stop from predictions')},set(stops))
            self.assertEqual(db.execute('select distinct route from predictions').fetchall(),[('SI',)])
            self.assertEqual(db.execute('select feed from source_polls').fetchall(),[('si',)])
            self.assertIn('NEW',json.loads((out/'scope.json').read_text()))
            db.close()

if __name__=='__main__': unittest.main()
