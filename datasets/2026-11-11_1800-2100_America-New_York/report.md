# MTA exploratory run

Run: data\20261111T230000_613504Z

**These are API-based estimates, not validated actual arrival/delay measurements.**

## Collection

```json
{
  "run": "data\\20261111T230000_613504Z",
  "counts": {
    "predictions": 330882,
    "vehicles": 8669,
    "alerts": 13047
  },
  "feeds": {
    "trains": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 360,
      "min_source_age_seconds": 1.2351305484771729,
      "max_source_age_seconds": 8.350419998168945
    },
    "alerts": {
      "requests": 360,
      "errors": 0,
      "distinct_source_timestamps": 30,
      "min_source_age_seconds": 1.2822089195251465,
      "max_source_age_seconds": 1400.294472694397
    }
  },
  "arrival_candidates": 1102,
  "estimate_basis": "API-based, unvalidated estimates; not actual arrival/delay measurements",
  "eta_matched_candidates": 1060,
  "tracked_trip_stops": 3901,
  "positive_eta_range_trip_stops": 2172,
  "actual_delay_measured": false,
  "arrival_algorithm": "v2_station_path_timestamp",
  "station_change_evidence": 1517,
  "rejection_reasons": {
    "candidate": 1102,
    "not_expected_next_station": 5,
    "remaining_path_not_yet_aligned": 368,
    "repeated_out_of_order_or_gap": 1,
    "stale_or_future_vehicle_time": 37,
    "vehicle_time_not_new_since_previous_observation": 4
  },
  "vehicle_status_counts": {
    "STOPPED_AT": 8561,
    "UNKNOWN": 108
  },
  "prediction_counts_by_route": {
    "B": 52496,
    "D": 69507,
    "F": 136741,
    "M": 72138
  },
  "distinct_station_directions": 45,
  "raw_compressed_bytes": 36062729
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
|D|D22N|Grand St|32.13|
|D|D21N|Broadway-Lafayette St|31.72|
|D|D14N|7 Av|31.63|
|D|A24N|59 St-Columbus Circle|31.55|
|D|D15N|47-50 Sts-Rockefeller Ctr|31.55|
|D|D16N|42 St-Bryant Pk|31.55|
|D|D17N|34 St-Herald Sq|31.55|
|D|D20N|W 4 St-Wash Sq|31.55|
|F|F16S|East Broadway|19.25|
|F|F14S|2 Av|19.23|
|F|D20S|W 4 St-Wash Sq|19.17|
|F|D21S|Broadway-Lafayette St|19.17|
|F|F15S|Delancey St-Essex St|19.17|
|F|D19S|14 St|18.67|
|F|D15S|47-50 Sts-Rockefeller Ctr|13.60|
