# MTA exploratory run

Run: /Users/hollisyang/Desktop/Studio/MTA-DATA-TEST/data/20261112T113000_morning

**These are exploratory observations, not validated actual arrival/delay measurements.**

## Collection

```json
{
  "run": "/Users/hollisyang/Desktop/Studio/MTA-DATA-TEST/data/20261112T113000_morning",
  "counts": {
    "predictions": 430920,
    "vehicles": 9773,
    "alerts": 14029
  },
  "feeds": {
    "trains": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 360,
      "min_source_age_seconds": 5.1053690910339355,
      "max_source_age_seconds": 14.170076847076416
    },
    "alerts": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 70,
      "min_source_age_seconds": 1.1485040187835693,
      "max_source_age_seconds": 1696.1494870185852
    }
  },
  "arrival_candidates": 1266,
  "eta_matched_candidates": 1236,
  "tracked_trip_stops": 4974,
  "positive_eta_range_trip_stops": 2543,
  "actual_delay_measured": false,
  "arrival_algorithm": "v2_station_path_timestamp",
  "station_change_evidence": 1723,
  "rejection_reasons": {
    "candidate": 1266,
    "not_expected_next_station": 5,
    "path_changed_not_suffix": 2,
    "remaining_path_not_yet_aligned": 392,
    "repeated_out_of_order_or_gap": 1,
    "stale_or_future_vehicle_time": 56,
    "vehicle_time_not_new_since_previous_observation": 1
  },
  "vehicle_status_counts": {
    "STOPPED_AT": 9767,
    "UNKNOWN": 6
  },
  "prediction_counts_by_route": {
    "B": 72756,
    "D": 81347,
    "F": 172143,
    "FX": 4529,
    "M": 100145
  },
  "distinct_station_directions": 41,
  "raw_compressed_bytes": 40143636
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
|M|M18S|Delancey St-Essex St|49.97|
|M|M19S|Bowery|49.85|
|M|M20S|Canal St|49.85|
|F|D16S|42 St-Bryant Pk|28.62|
|F|D15S|47-50 Sts-Rockefeller Ctr|28.53|
|F|D17S|34 St-Herald Sq|28.53|
|F|D18S|23 St|28.53|
|F|D19S|14 St|28.53|
|F|D20S|W 4 St-Wash Sq|28.20|
|F|D21S|Broadway-Lafayette St|28.20|
|F|F14S|2 Av|28.20|
|F|F15S|Delancey St-Essex St|28.20|
|F|F16S|East Broadway|28.20|
|F|D17S|34 St-Herald Sq|25.67|
|F|D15S|47-50 Sts-Rockefeller Ctr|25.65|
