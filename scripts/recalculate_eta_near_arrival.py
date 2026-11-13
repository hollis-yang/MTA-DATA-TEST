#!/usr/bin/env python3
"""Recalculate ETA error from a near-zero remaining-ETA endpoint.

Reads source SQLite files without changing them. Endpoint accuracy is an assumed
property supplied by the user, not an independently validated arrival label.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
MAJOR_STATIONS = {"D15": "Rockefeller", "D16": "Bryant Pk",
                  "D17": "Herald Sq", "D20": "Wash Sq"}


def local(ts):
    return dt.datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stats(rows, field="error_seconds"):
    x = [r[field] / 60 for r in rows]
    return dict(cases=len(x), distinct_trips=len({r["trip_key"] for r in rows}),
                mean_error_min=statistics.mean(x) if x else None,
                median_error_min=statistics.median(x) if x else None,
                mean_absolute_error_min=statistics.mean(abs(v) for v in x) if x else None,
                over_1_min_count=sum(v > 1 for v in x),
                over_1_min_pct=100 * sum(v > 1 for v in x) / len(x) if x else None,
                min_error_min=min(x) if x else None, max_error_min=max(x) if x else None)


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def calculate(run, output, method="near_0_30"):
    source = run / "observations.sqlite"
    before = digest(source)
    db = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    manifest = json.loads((run / "manifest.json").read_text())
    start = dt.datetime.fromisoformat(manifest.get("scheduled_start", manifest.get("started"))).timestamp()
    end = dt.datetime.fromisoformat(manifest["scheduled_end"]).timestamp() if "scheduled_end" in manifest else start + manifest["duration_seconds"]
    scope = json.loads((run / "scope.json").read_text())
    paths = {(r["poll_id"], r["trip_key"]): set(json.loads(r["skipped"]))
             for r in db.execute("SELECT poll_id,trip_key,skipped FROM trip_paths")}
    vehicles = collections.defaultdict(list)
    for r in db.execute("SELECT * FROM vehicles ORDER BY source_ts,poll_id"):
        vehicles[r["trip_key"]].append(dict(r))
    evidence = collections.defaultdict(list)
    for r in csv.DictReader((run / "arrival_evidence.csv").open()):
        evidence[r["trip_key"]].append(r)
    groups = collections.defaultdict(list)
    for r in db.execute("""SELECT p.*, q.received FROM predictions p JOIN polls q ON q.id=p.poll_id
       WHERE p.eta IS NOT NULL AND q.error IS NULL AND q.http_status=200
       AND q.received-q.source_ts BETWEEN -5 AND 120 ORDER BY p.source_ts,p.poll_id"""):
        groups[(r["trip_key"], r["stop"])].append(dict(r))
    old = {(r["trip_key"], r["stop"]): r for r in csv.DictReader((run / "arrival_candidates.csv").open())
           if r["eta_error_proxy_min"]}
    old_flags = {}
    prior_detail = run / "analysis" / "training_examples_unvalidated.csv"
    if prior_detail.exists():
        old_flags = {(r["trip_key"], r["stop"]): r["label_review_flags"]
                     for r in csv.DictReader(prior_detail.open())}
    counts = collections.Counter()
    cases, unmatched = [], []
    for key, raw in sorted(groups.items()):
        # Repeated source timestamps are one observation, not independent samples.
        by_source = {r["source_ts"]: r for r in raw}
        rows = list(by_source.values())
        near = [r for r in rows if 0 <= r["eta"] - r["source_ts"] <= 30]
        counts["unique_trip_stops"] += 1
        counts["near_arrival_snapshot_rows"] += len(near)
        if near:
            counts["trip_stops_with_near_arrival"] += 1
        if method == "near_0_30" and not near:
            continue
        terminal = (near[-1] if method == "near_0_30" else
                    min(rows, key=lambda r: (abs(r["eta"] - r["source_ts"]), r["source_ts"], r["poll_id"])))
        tied = [r for r in rows if abs(r["eta"] - r["source_ts"]) == abs(terminal["eta"] - terminal["source_ts"])]
        counts["trip_stops_with_endpoint"] += 1
        reference_rows = [r for r in rows if r["source_ts"] < terminal["source_ts"]
                          and 240 <= r["eta"] - r["source_ts"] <= 360]
        if not reference_rows:
            unmatched.append(dict(trip_key=key[0], stop=key[1], route=terminal["route"],
                                  reason="no_earlier_4_to_6_min_reference"))
            counts["endpoint_without_reference"] += 1
            continue
        reference = reference_rows[0]
        history = [r for r in rows if reference["source_ts"] <= r["source_ts"] <= terminal["source_ts"]]
        flags = []
        if any(b["source_ts"] - a["source_ts"] > 90 for a, b in zip(history, history[1:])):
            flags.append("prediction_gap_over_90s")
        if any(key[1] in paths.get((r["poll_id"], key[0]), set()) for r in history):
            flags.append("explicitly_skipped_stop")
        later_future = [r for r in rows if r["source_ts"] > terminal["source_ts"]
                        and r["eta"] - r["source_ts"] > 30]
        if later_future:
            flags.append("eta_returns_above_30s_after_selected_endpoint")
        if not start <= terminal["eta"] <= end:
            flags.append("endpoint_outside_collection_window")
        if any(len({x["eta"] for x in raw if x["source_ts"] == r["source_ts"]}) > 1 for r in history):
            flags.append("conflicting_eta_at_same_source_timestamp")
        vehicle_history = [v for v in vehicles[key[0]] if reference["source_ts"] <= v["source_ts"] <= terminal["source_ts"]]
        if any(b["source_ts"] - a["source_ts"] > 90 for a, b in zip(vehicle_history, vehicle_history[1:])):
            flags.append("vehicle_observation_gap_over_90s")
        sequence = [v["stop"] for i,v in enumerate(vehicle_history) if not i or v["stop"] != vehicle_history[i-1]["stop"]]
        if len(set(sequence)) != len(sequence):
            flags.append("station_revisited")
        if any(e["reason"] == "not_expected_next_station" and
               reference["source_ts"] < float(e["source_ts"]) <= terminal["source_ts"] for e in evidence[key[0]]):
            flags.append("unexpected_station_step")
        previous = old.get(key)
        row = dict(trip_key=key[0], route=terminal["route"], stop=key[1], station_code=key[1][:-1],
                   station=scope.get(key[1][:-1], ""), direction=key[1][-1],
                   reference_poll_id=reference["poll_id"], reference_source_ts=reference["source_ts"],
                   reference_source_local=local(reference["source_ts"]), reference_eta=reference["eta"],
                   reference_eta_local=local(reference["eta"]),
                   reference_remaining_sec=reference["eta"] - reference["source_ts"],
                   endpoint_poll_id=terminal["poll_id"], endpoint_source_ts=terminal["source_ts"],
                   endpoint_source_local=local(terminal["source_ts"]), endpoint_received_local=local(terminal["received"]),
                   endpoint_remaining_sec=terminal["eta"] - terminal["source_ts"],
                   approximate_arrival_ts=terminal["eta"], approximate_arrival_local=local(terminal["eta"]),
                   error_seconds=terminal["eta"] - reference["eta"],
                   eta_error_proxy_min=(terminal["eta"] - reference["eta"]) / 60,
                   equally_close_observations=len(tied), latest_tied_eta=tied[-1]["eta"],
                   latest_tied_error_seconds=tied[-1]["eta"] - reference["eta"],
                   near_arrival_observations=len(near), first_near_eta=near[0]["eta"] if near else "",
                   first_vs_last_near_eta_diff_sec=near[-1]["eta"] - near[0]["eta"] if near else "",
                   later_future_observations=len(later_future), quality_flags=";".join(flags),
                   eligible_after_quality_screen=not flags,
                   old_method_error_min=float(previous["eta_error_proxy_min"]) if previous else "",
                   old_reference_eta=int(previous["reference_eta"]) if previous else "",
                   old_method_review_flags=old_flags.get(key, ""),
                   endpoint_basis=method, independently_validated=False)
        if method == "near_0_30":
            assert 0 <= row["endpoint_remaining_sec"] <= 30
        else:
            assert abs(row["endpoint_remaining_sec"]) == min(abs(r["eta"] - r["source_ts"]) for r in rows)
        assert 240 <= row["reference_remaining_sec"] <= 360
        assert row["reference_source_ts"] < row["endpoint_source_ts"]
        cases.append(row)
    assert len(cases) == len({(r["trip_key"], r["stop"]) for r in cases})
    # Independent SQL selection/reconciliation checks the source pairs and aggregation.
    endpoint_filter = "WHERE eta-source_ts BETWEEN 0 AND 30" if method == "near_0_30" else ""
    endpoint_order = "source_ts DESC,poll_id DESC" if method == "near_0_30" else "ABS(eta-source_ts),source_ts,poll_id"
    sql = f"""WITH eligible AS (
      SELECT p.* FROM predictions p JOIN polls q ON q.id=p.poll_id
      WHERE p.eta IS NOT NULL AND q.error IS NULL AND q.http_status=200
        AND q.received-q.source_ts BETWEEN -5 AND 120),
      ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY trip_key,stop ORDER BY {endpoint_order}) rn
                 FROM eligible {endpoint_filter}),
      paired AS (SELECT e.*,(SELECT r.eta FROM eligible r WHERE r.trip_key=e.trip_key AND r.stop=e.stop
             AND r.source_ts<e.source_ts AND r.eta-r.source_ts BETWEEN 240 AND 360
             ORDER BY r.source_ts,r.poll_id LIMIT 1) ref FROM ranked e WHERE rn=1)
      SELECT trip_key,stop,eta-ref error_seconds FROM paired WHERE ref IS NOT NULL"""
    independent = {(r["trip_key"], r["stop"]): r["error_seconds"] for r in db.execute(sql)}
    assert independent == {(r["trip_key"], r["stop"]): r["error_seconds"] for r in cases}
    clean = [r for r in cases if r["eligible_after_quality_screen"]]
    routes = []
    stations = []
    for name, subset in (("all_matched", cases), ("exclude_quality_flags", clean)):
        for route in ["B", "D", "F", "M", "FX"]:
            routes.append(dict(sample=name, route=route, **stats([r for r in subset if r["route"] == route])))
        for station, label in MAJOR_STATIONS.items():
            stations.append(dict(sample=name, station_code=station, station=label,
                                 **stats([r for r in subset if r["station_code"] == station])))
    overlaps = [r for r in cases if r["old_method_error_min"] != ""]
    old_overlap = [dict(r, old_error_seconds=r["old_method_error_min"] * 60) for r in overlaps]
    diagnostics = [dict(remaining_band=r[0], snapshot_rows=r[1]) for r in db.execute("""SELECT
        CASE WHEN eta-source_ts<0 THEN 'negative' WHEN eta-source_ts<=30 THEN '0–30 seconds'
        WHEN eta-source_ts<=60 THEN '31–60 seconds' WHEN eta-source_ts<=120 THEN '61–120 seconds'
        ELSE 'over 120 seconds' END,COUNT(*) FROM predictions WHERE eta IS NOT NULL GROUP BY 1""")]
    summary = dict(source_run=str(run.resolve()), source_sha256=before,
        collection_start=local(start), collection_end=local(end),
        method=dict(name=method, remaining_eta_basis="ETA absolute timestamp minus feed source timestamp",
            endpoint=("Last valid prediction with 0 <= remaining ETA <= 30 seconds, per trip and directional stop"
                      if method == "near_0_30" else
                      "Prediction minimizing absolute remaining ETA, allowing positive and negative values; earliest source timestamp breaks ties"),
            reference="Earliest valid prediction before endpoint with 240 <= remaining ETA <= 360 seconds",
            calculation="(endpoint ETA - reference ETA) / 60",
            validity="HTTP 200, no parse error, feed age -5 to 120 seconds; repeated feed timestamps deduplicated",
            arrival_assumption="Near-arrival ETA is approximately accurate, as specified by the user",
            route_scope="B/D/F/M/FX retained; F and FX reported separately; all originally collected stops retained",
            quality_screen="Flag prediction/vehicle gaps >90s, skipped or unexpected station steps, revisits, future ETA >30s after selected endpoint, conflicting timestamps, endpoints outside window",
            uncertainty="No independent physical arrival validation; negative ETA is not clamped to zero"),
        counts=dict(counts), all_matched=stats(cases), excluding_quality_flags=stats(clean),
        flagged_cases=len(cases)-len(clean),
        quality_flag_counts=dict(collections.Counter(flag for r in cases for flag in r["quality_flags"].split(";") if flag)),
        distinct_old_matched_cases=len(old), overlap_with_old_method=len(overlaps),
        paired_old_stats=stats(old_overlap, "old_error_seconds"), paired_new_stats=stats(overlaps),
        overlap_reference_changes=sum(r["old_reference_eta"] != r["reference_eta"] for r in overlaps),
        major_stations=stations, routes=routes, remaining_eta_distribution=diagnostics,
        independent_sql_reconciliation="passed")
    summary["endpoint_proximity"] = {}
    for name, subset in (("all_matched", cases), ("exclude_quality_flags", clean)):
        distances = [abs(r["endpoint_remaining_sec"]) for r in subset]
        summary["endpoint_proximity"][name] = dict(
            median_absolute_seconds=statistics.median(distances) if distances else None,
            max_absolute_seconds=max(distances) if distances else None,
            negative_remaining_cases=sum(r["endpoint_remaining_sec"] < 0 for r in subset),
            within_15s=sum(v <= 15 for v in distances), within_30s=sum(v <= 30 for v in distances),
            within_60s=sum(v <= 60 for v in distances), over_60s=sum(v > 60 for v in distances))
    summary["sensitivity_endpoint_within_60s"] = stats([r for r in clean if abs(r["endpoint_remaining_sec"]) <= 60])
    if method == "closest_zero":
        summary["sensitivity_latest_tied_endpoint_same_cases"] = stats(clean, "latest_tied_error_seconds")
        summary["equal_distance_tie_cases"] = sum(r["equally_close_observations"] > 1 for r in cases)
    db.close()
    assert digest(source) == before
    summary["source_unchanged"] = True
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "matched_cases.csv", cases)
    write_csv(output / "unmatched_endpoints.csv", unmatched)
    write_csv(output / "by_route.csv", routes)
    write_csv(output / "major_stations.csv", stations)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--method", choices=["near_0_30", "closest_zero"], default="near_0_30")
    args = parser.parse_args()
    result = calculate(args.run, args.output, args.method)
    print(json.dumps({k:result[k] for k in ("counts", "all_matched", "excluding_quality_flags", "quality_flag_counts",
                                          "overlap_with_old_method", "independent_sql_reconciliation", "source_unchanged")},
                     ensure_ascii=False, indent=2))
