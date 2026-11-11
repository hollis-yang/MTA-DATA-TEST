import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO

from google.transit import gtfs_realtime_pb2 as g
import mta_explore as m


class ExplorationTests(unittest.TestCase):
    def station_case(self):
        a=dict(stop='D16N',status='STOPPED_AT',status_explicit=1,source_ts=100,vehicle_ts=90)
        b=dict(stop='D15N',status='STOPPED_AT',status_explicit=1,source_ts=130,vehicle_ts=115)
        old=dict(stops=['D16N','D15N','D14N'],skipped=[])
        new=dict(stops=['D15N','D14N'],skipped=[])
        return a,b,old,new

    def test_constant_stopped_status_with_three_consistent_signals(self):
        a,b,old,new=self.station_case()
        event,reason=m.station_transition(a,b,old,new)
        self.assertEqual(reason,'candidate')
        self.assertEqual(event['estimated_event_ts'],115)
        self.assertEqual((event['observation_start'],event['observation_end']),(100,130))
        # An already-cleared old stop is allowed if the next station remains consistent.
        self.assertEqual(m.station_transition(a,b,new,new)[1],'candidate')

    def test_station_changes_reject_skips_reroutes_and_unaligned_path(self):
        a,b,old,new=self.station_case()
        self.assertEqual(m.station_transition(a,b,old,dict(new,skipped=['D15N']))[1],'explicitly_skipped_station')
        self.assertEqual(m.station_transition(a,b,old,dict(stops=['D15N','OTHER'],skipped=[]))[1],'path_changed_not_suffix')
        self.assertEqual(m.station_transition(a,b,old,old)[1],'remaining_path_not_yet_aligned')
        self.assertEqual(m.station_transition(a,dict(b,stop='D14N'),old,dict(stops=['D14N'],skipped=[]))[1],'not_expected_next_station')
        self.assertEqual(m.station_transition(a,b,None,new)[1],'missing_trip_path')

    def test_station_changes_reject_stale_future_and_nonadvancing_times(self):
        a,b,old,new=self.station_case()
        for stamp in (-20,135):
            self.assertEqual(m.station_transition(a,dict(b,vehicle_ts=stamp),old,new)[1],'stale_or_future_vehicle_time')
        self.assertEqual(m.station_transition(a,dict(b,vehicle_ts=95),old,new)[1],
                         'vehicle_time_not_new_since_previous_observation')
        self.assertIsNone(m.station_transition(a,dict(b,source_ts=100),old,new)[0])
        self.assertIsNone(m.station_transition(a,dict(b,source_ts=300,vehicle_ts=290),old,new)[0])

    def test_constant_status_pipeline_detects_one_candidate_and_keeps_full_path(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            run=Path(directory);(run/'raw').mkdir();(run/'scope.json').write_text(json.dumps({'D16':'Bryant','D15':'Rockefeller'}))
            db=m.database(run/'observations.sqlite')
            for stamp,stop,remaining in [(100,'D16N',['D16N','D15N','D14N']),
                (130,'D15N',['D15N','D14N']),(130,'D15N',['D15N','D14N']),
                (160,'D15N',['D15N','D14N'])]:
                f=g.FeedMessage();f.header.gtfs_realtime_version='2.0';f.header.timestamp=stamp
                e=f.entity.add();e.id='t';t=e.trip_update.trip;t.trip_id='test';t.route_id='D';t.start_date='20261111'
                for name in remaining:
                    s=e.trip_update.stop_time_update.add();s.stop_id=name;s.arrival.time=stamp+300
                e=f.entity.add();e.id='v';v=e.vehicle;v.trip.CopyFrom(t);v.stop_id=stop
                v.current_status=1;v.current_stop_sequence=1;v.timestamp=stamp-10
                m.ingest(db,run,'trains',dict(started=stamp,received=stamp+1,body=f.SerializeToString(),
                    http_status=200,headers={},error=None),{'D16':'Bryant','D15':'Rockefeller'})
            path=json.loads(db.execute('select stops from trip_paths where poll_id=1').fetchone()[0])
            self.assertIn('D14N',path)  # out-of-scope stations still support path consistency
            db.close();summary=m.analyze(run)
            self.assertEqual(summary['arrival_candidates'],1)
            self.assertEqual(summary['eta_matched_candidates'],0)  # no fabricated reference ETA
            self.assertEqual(summary['station_change_evidence'],1)
            # Legacy data can recover complete paths from local raw snapshots.
            db=m.database(run/'observations.sqlite')
            db.execute('DELETE FROM trip_paths');db.commit();db.close()
            replay=m.analyze(run)
            self.assertEqual(replay['arrival_candidates'],summary['arrival_candidates'])
            self.assertEqual(replay['rejection_reasons'],summary['rejection_reasons'])

    def test_arrival_requires_transition_not_first_stopped_or_duplicate(self):
        stopped = dict(stop='D15N', status='STOPPED_AT', status_explicit=1, source_ts=130)
        self.assertIsNone(m.transition(None, stopped))
        self.assertIsNone(m.transition(stopped, dict(stopped, source_ts=160)))
        moving = dict(stopped, status='IN_TRANSIT_TO', source_ts=100)
        self.assertEqual(m.transition(moving, stopped), (100,130))
        self.assertIsNone(m.transition(moving, dict(stopped, source_ts=100)))
        self.assertIsNone(m.transition(moving, dict(stopped, source_ts=250)))
        self.assertIsNone(m.transition(moving, dict(stopped, stop='D16N')))

    def test_alert_identity_and_planned_period(self):
        self.assertEqual(m.event_key('lmm:alert:123:26'), m.event_key('lmm:alert:123:32'))
        self.assertEqual(m.event_key('lmm:planned_work:123'), 'lmm:planned_work:123')
        self.assertEqual(m.active_at([{'start':200,'end':300}], 100), 0)
        self.assertEqual(m.active_at([{'start':200,'end':300}], 250), 1)
        self.assertEqual(m.active_at([{'start':200,'end':300}], 300), 0)

    def test_scope_includes_boundaries_but_excludes_northern_and_queens_stops(self):
        rows = [dict(gtfs_stop_id=i, borough=b, gtfs_latitude=str(lat), stop_name=i)
                for i,b,lat in [('A24','M',40.768296),('B08','M',40.764629),
                                ('B06','M',40.759145),('A22','M',40.775),('Q01','Q',40.75)]]
        self.assertEqual(set(m.station_scope(rows)), {'A24','B08','B06'})

    def test_full_pipeline_ignores_stale_data_and_deduplicates_stops(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            run = Path(directory)
            (run/'raw').mkdir()
            (run/'scope.json').write_text(json.dumps({'D15':'47-50 Sts'}))
            db=m.database(run/'observations.sqlite')
            for stamp,status,received in [(100,2,101),(370,2,371),(400,1,401),
                                           (400,1,431),(430,1,431),(500,2,1000),(530,1,1031)]:
                f=g.FeedMessage(); f.header.gtfs_realtime_version='2.0'; f.header.timestamp=stamp
                e=f.entity.add(); e.id='trip'
                t=e.trip_update.trip; t.trip_id='test'; t.start_date='20261111'; t.route_id='F'
                s=e.trip_update.stop_time_update.add(); s.stop_id='D15N'; s.arrival.time=400
                e=f.entity.add(); e.id='vehicle'; e.vehicle.trip.CopyFrom(t)
                v=e.vehicle; v.stop_id='D15N'; v.current_status=status; v.current_stop_sequence=1; v.timestamp=stamp
                m.ingest(db,run,'trains',dict(started=received,received=received,body=f.SerializeToString(),
                         http_status=200,headers={},error=None),{'D15':'47-50 Sts'})
            # Corrupt responses remain on disk and do not partially populate observations.
            m.ingest(db,run,'trains',dict(started=1100,received=1100,body=b'bad data',
                      http_status=200,headers={},error=None),{'D15':'47-50 Sts'})
            db.close()
            summary=m.analyze(run)
            self.assertEqual(summary['arrival_candidates'],1)
            self.assertEqual(summary['eta_matched_candidates'],1)
            self.assertEqual(summary['feeds']['trains']['errors'],1)
            self.assertFalse(summary['actual_delay_measured'])
            self.assertEqual(len(list((run/'raw').iterdir())),8)

    def test_alerts_keep_route_level_not_just_corridor_station_matches(self):
        db=m.database(':memory:')
        alert={'informed_entity':[{'route_id':'D'}],
               'header_text':{'translation':[{'language':'en','text':'Delay outside Manhattan'}]},
               'active_period':[{'start':200}],
               'transit_realtime.mercury_alert':{'alert_type':'Delays','created_at':50,'updated_at':80}}
        body=json.dumps({'header':{'timestamp':100},'entity':[{'id':'lmm:alert:1:26','alert':alert}]}).encode()
        m.parse_alerts(db,1,body)
        row=db.execute('SELECT event_key,active_at_source,title FROM alerts').fetchone()
        self.assertEqual(row,('lmm:alert:1',0,'Delay outside Manhattan'))
        db.close()


if __name__=='__main__':
    unittest.main()
