# MTA exploratory run

Run: /Users/hollisyang/Desktop/Studio/MTA-DATA-TEST/data/20261112T170000_midday

**These are exploratory observations, not validated actual arrival/delay measurements.**

## Collection

```json
{
  "run": "/Users/hollisyang/Desktop/Studio/MTA-DATA-TEST/data/20261112T170000_midday",
  "counts": {
    "predictions": 372293,
    "vehicles": 10293,
    "alerts": 16958
  },
  "feeds": {
    "trains": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 360,
      "min_source_age_seconds": 2.1217458248138428,
      "max_source_age_seconds": 12.172976970672607
    },
    "alerts": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 49,
      "min_source_age_seconds": 1.1538140773773193,
      "max_source_age_seconds": 1822.1590440273285
    }
  },
  "arrival_candidates": 1001,
  "eta_matched_candidates": 952,
  "tracked_trip_stops": 4421,
  "positive_eta_range_trip_stops": 2233,
  "actual_delay_measured": false,
  "arrival_algorithm": "v2_station_path_timestamp",
  "station_change_evidence": 1436,
  "rejection_reasons": {
    "candidate": 1005,
    "not_expected_next_station": 7,
    "remaining_path_not_yet_aligned": 325,
    "repeated_out_of_order_or_gap": 1,
    "stale_or_future_vehicle_time": 88,
    "vehicle_time_not_new_since_previous_observation": 10
  },
  "vehicle_status_counts": {
    "STOPPED_AT": 10278,
    "UNKNOWN": 15
  },
  "prediction_counts_by_route": {
    "B": 60463,
    "D": 77028,
    "F": 136390,
    "M": 98412
  },
  "distinct_station_directions": 54,
  "raw_compressed_bytes": 43229752
}
```

## Interpretation

- ETA range means forecast revisions, not actual delay.
- Arrival candidates use fresh station-ID changes, advancing vehicle timestamps, and consistent ordered remaining paths. Explicit same-stop transit-to-stopped transitions remain supported.
- Station-ID change estimates use the vehicle timestamp, NOT a verified arrival timestamp. See arrival_evidence.csv for path evidence and rejected changes.
- A train already stopped when collection begins is left-censored and is excluded.
- ETA error uses an earlier forecast showing 4–6 minutes remaining. Short runs may have no eligible reference.
- Event gaps can include missed trains; they are not validated headways or trains/hour.
- Alert active periods include future planned work. active_at_source is relative to the alert feed timestamp, not download time.
- active_at_collection evaluates the published periods at collection time; neither flag establishes the ground-truth duration of an incident.
- Route/time overlap with an alert is an association, not proof that the alert caused a specific train delay.
- Future/stale vehicle timestamps and stale feed snapshots are excluded from arrival estimates, but retained in raw data.
- The two feed source timestamps can differ significantly. Successful downloads do not establish freshness.
- An unchanged alert timestamp alone cannot distinguish an event-driven publisher with no new notices from a stale feed.
- No timetable comparisons are performed. Longer runs and station observation are needed for conclusions.

## Largest ETA revisions (not actual delays)

|Route|Stop|Station|ETA range, min|
|---|---|---|---|
|D|D21N|Broadway-Lafayette St|78.00|
|D|D20N|W 4 St-Wash Sq|75.85|
|D|D17N|34 St-Herald Sq|72.87|
|D|D16N|42 St-Bryant Pk|71.38|
|D|D15N|47-50 Sts-Rockefeller Ctr|69.90|
|D|D14N|7 Av|68.42|
|D|A24N|59 St-Columbus Circle|66.43|
|B|D20S|W 4 St-Wash Sq|42.75|
|B|D21S|Broadway-Lafayette St|42.75|
|B|D22S|Grand St|42.75|
|F|F16S|East Broadway|38.45|
|F|F15S|Delancey St-Essex St|38.37|
|F|D21S|Broadway-Lafayette St|37.87|
|F|F14S|2 Av|37.87|
|F|F15S|Delancey St-Essex St|37.17|
