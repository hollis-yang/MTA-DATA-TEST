#!/usr/bin/env python3
"""Read-only descriptive analysis of one collector run; standard library only.

Usage: python3 scripts/analyze_collection.py RUN_DIRECTORY OUTPUT_DIRECTORY
All arrival labels remain unvalidated API estimates. No collector data is edited.
"""
import argparse
import bisect
import collections
import csv
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import statistics
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")


def local(t):
    return dt.datetime.fromtimestamp(float(t), TZ).isoformat(timespec="seconds")


def quantile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def stats(rows):
    x = [r["eta_error_proxy_min"] for r in rows]
    return {
        "matched_cases": len(x),
        "distinct_trips": len({r["trip_key"] for r in rows}),
        "mean_error_min": statistics.mean(x),
        "median_error_min": statistics.median(x),
        "p90_error_min": quantile(x, .9),
        "mean_absolute_error_min": statistics.mean(abs(v) for v in x),
        "min_error_min": min(x),
        "max_error_min": max(x),
        "later_than_eta_count": sum(v > 0 for v in x),
        "over_1_min_count": sum(v > 1 for v in x),
        "over_1_min_pct": 100 * sum(v > 1 for v in x) / len(x),
        "over_2_min_count": sum(v > 2 for v in x),
        "over_2_min_pct": 100 * sum(v > 2 for v in x) / len(x),
    }


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def grouped(rows, keys):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    return [dict(zip(keys, key), **stats(value)) for key, value in sorted(groups.items())]


def active(periods, now):
    return any(p.get("start", float("-inf")) <= now < p.get("end", float("inf"))
               for p in periods) if periods else True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect((args.run / "observations.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    original = json.loads((args.run / "summary.json").read_text())
    counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in ("polls", "predictions", "vehicles", "alerts", "trip_paths")}
    for table, count in original["counts"].items():
        assert counts[table] == count
    polls = {r["id"]: dict(r) for r in db.execute("SELECT * FROM polls ORDER BY received")}
    predictions = collections.defaultdict(list)
    for row in db.execute("SELECT * FROM predictions ORDER BY source_ts, poll_id"):
        predictions[(row["trip_key"], row["stop"])].append(dict(row))
    vehicles = collections.defaultdict(list)
    for row in db.execute("SELECT * FROM vehicles ORDER BY source_ts, poll_id"):
        vehicles[row["trip_key"]].append(dict(row))
    evidence = collections.defaultdict(list)
    with (args.run / "arrival_evidence.csv").open() as f:
        for r in csv.DictReader(f):
            evidence[r["trip_key"]].append(r)

    feed_stats = []
    for kind in ("trains", "alerts"):
        rows = [p for p in polls.values() if p["kind"] == kind]
        good = [p for p in rows if not p["error"] and p["http_status"] == 200]
        ages = [p["received"] - p["source_ts"] for p in good if p["source_ts"] is not None]
        intervals = [b["started"] - a["started"] for a, b in zip(rows, rows[1:])]
        feed_stats.append(dict(feed=kind, requests=len(rows), successful=len(good),
            success_pct=100 * len(good) / len(rows), first_request=local(rows[0]["started"]),
            last_request=local(rows[-1]["started"]), distinct_source_timestamps=len({p["source_ts"] for p in good}),
            median_source_age_sec=statistics.median(ages), p90_source_age_sec=quantile(ages, .9),
            max_source_age_sec=max(ages), median_interval_sec=statistics.median(intervals),
            max_interval_sec=max(intervals)))

    alerts_by_poll = collections.defaultdict(list)
    event_rows = collections.defaultdict(list)
    for row in db.execute("SELECT * FROM alerts ORDER BY poll_id"):
        row = dict(row)
        for field in ("routes", "active_periods"):
            row[field] = json.loads(row[field])
        alerts_by_poll[row["poll_id"]].append(row)
        event_rows[row["event_key"]].append(row)
    alert_polls = sorted((p for p in polls.values() if p["kind"] == "alerts" and not p["error"]),
                         key=lambda p: p["received"])
    alert_received = [p["received"] for p in alert_polls]
    events = []
    for event_key, rows in sorted(event_rows.items()):
        events.append(dict(event_key=event_key, alert_types=sorted({r["alert_type"] for r in rows}),
            routes=sorted({route for r in rows for route in r["routes"]}),
            first_seen_local=local(min(polls[r["poll_id"]]["received"] for r in rows)),
            last_seen_local=local(max(polls[r["poll_id"]]["received"] for r in rows)),
            snapshot_records=len(rows), active_at_source=any(r["active_at_source"] for r in rows),
            active_at_collection=any(active(r["active_periods"], polls[r["poll_id"]]["received"]) for r in rows),
            titles=list(dict.fromkeys(r["title"] for r in rows)),
            descriptions=list(dict.fromkeys(r["description"] for r in rows))))
    (args.output / "official_events.json").write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n")

    with (args.run / "arrival_candidates.csv").open() as f:
        candidates = list(csv.DictReader(f))
    assert len(candidates) == original["arrival_candidates"]
    assert len({(r["trip_key"], r["stop"]) for r in candidates}) == len(candidates)
    matched = []
    for r in candidates:
        if not r["eta_error_proxy_min"]:
            continue
        path = predictions[(r["trip_key"], r["stop"])]
        eligible = [p for p in path if p["source_ts"] < float(r["observation_start"])
                    and 240 <= p["eta"] - p["source_ts"] <= 360
                    and not polls[p["poll_id"]]["error"]
                    and -5 <= polls[p["poll_id"]]["received"] - p["source_ts"] <= 120]
        ref = eligible[0]
        assert ref["eta"] == int(r["reference_eta"])
        label = (float(r["estimated_event_ts"]) - ref["eta"]) / 60
        assert abs(label - float(r["eta_error_proxy_min"])) < 1e-9
        feature_time = polls[ref["poll_id"]]["received"]
        assert feature_time < float(r["estimated_event_ts"])
        history = [v for v in vehicles[r["trip_key"]]
                   if ref["source_ts"] <= v["source_ts"] <= float(r["observation_end"])]
        flags = []
        if any(b["source_ts"] - a["source_ts"] > 90 for a, b in zip(history, history[1:])):
            flags.append("vehicle_observation_gap_over_90s")
        sequence = [v["stop"] for i, v in enumerate(history) if not i or v["stop"] != history[i-1]["stop"]]
        if len(set(sequence)) < len(sequence):
            flags.append("station_revisited")
        if any(e["reason"] == "not_expected_next_station"
               and ref["source_ts"] < float(e["source_ts"]) <= float(r["observation_end"])
               for e in evidence[r["trip_key"]]):
            flags.append("unexpected_station_step")
        prior = [p for p in path if ref["source_ts"] - 60 <= p["source_ts"] < ref["source_ts"]
                 and polls[p["poll_id"]]["received"] <= feature_time]
        revision = (ref["eta"] - prior[0]["eta"]) / 60 if prior else None
        ix = bisect.bisect_right(alert_received, feature_time) - 1
        available_alerts = []
        alert_age = None
        if ix >= 0:
            alert_poll = alert_polls[ix]
            alert_age = feature_time - alert_poll["source_ts"]
            available_alerts = [a for a in alerts_by_poll[alert_poll["id"]]
                                if r["route"] in a["routes"] and active(a["active_periods"], feature_time)]
        row = dict(trip_key=r["trip_key"], route=r["route"], stop=r["stop"], station_code=r["stop"][:-1],
            station=r["station"], direction=r["stop"][-1],
            event_hour_local=dt.datetime.fromtimestamp(float(r["estimated_event_ts"]), TZ).strftime("%Y-%m-%d %H:00"),
            feature_poll_id=ref["poll_id"], feature_time_local=local(feature_time),
            reference_source_time_local=local(ref["source_ts"]), reference_eta_local=local(ref["eta"]),
            predicted_wait_min_at_source=(ref["eta"] - ref["source_ts"]) / 60,
            eta_revision_within_prior_60s_min=revision,
            alert_source_age_at_feature_sec=alert_age,
            active_route_alert_types=";".join(sorted({a["alert_type"] for a in available_alerts})),
            active_route_alert_event_keys=";".join(sorted({a["event_key"] for a in available_alerts})),
            candidate_arrival_local=local(r["estimated_event_ts"]), eta_error_proxy_min=label,
            label_review_flags=";".join(flags), label_validated=False)
        matched.append(row)
    assert len(matched) == original["eta_matched_candidates"]
    write_csv(args.output / "training_examples_unvalidated.csv", matched)
    quality = collections.Counter(flag for r in matched for flag in r["label_review_flags"].split(";") if flag)
    sensitivity = [r for r in matched if not r["label_review_flags"]]
    changes = list(csv.DictReader((args.run / "eta_changes.csv").open()))
    assert len(changes) == original["tracked_trip_stops"]
    max_revision = max(changes, key=lambda r: float(r["eta_range_minutes"]))
    summary = dict(timezone="America/New_York", estimate_basis="API-derived unvalidated candidate arrivals",
        counts=counts, distinct_prediction_trips=len({key[0] for key in predictions}),
        distinct_predicted_trip_stops=len(predictions),
        distinct_station_directions=len({key[1] for key in predictions}), feeds=feed_stats,
        arrival_candidates=len(candidates), matched_candidates=len(matched),
        matched_pct_of_candidates=100 * len(matched) / len(candidates),
        matched_pct_of_predicted_trip_stops=100 * len(matched) / len(predictions),
        overall=stats(matched), label_review_flag_counts=dict(quality),
        flagged_cases=sum(bool(r["label_review_flags"]) for r in matched),
        sensitivity_excluding_flagged_cases=stats(sensitivity),
        tracked_trip_stops=len(changes),
        eta_varied_trip_stops=sum(float(r["eta_range_minutes"]) > 0 for r in changes),
        maximum_eta_range=max_revision,
        unique_delay_events=sum("Delays" in e["alert_types"] for e in events),
        unique_active_delay_events=sum("Delays" in e["alert_types"] and e["active_at_collection"] for e in events),
        top_candidate_errors=sorted(matched, key=lambda r: r["eta_error_proxy_min"], reverse=True)[:10])
    (args.output / "analysis_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output / "feed_quality.csv", feed_stats)
    for filename, keys in (("by_route.csv", ["route"]), ("by_route_direction.csv", ["route", "direction"]),
            ("by_station.csv", ["station_code", "station"]),
            ("by_station_direction.csv", ["station_code", "station", "direction"]),
            ("by_hour.csv", ["event_hour_local"]),
            ("by_hour_route_direction.csv", ["event_hour_local", "route", "direction"])):
        write_csv(args.output / filename, grouped(matched, keys))
    write_csv(args.output / "major_stations.csv", [r for r in grouped(matched, ["station_code", "station"])
              if r["station_code"] in ("D15", "D17", "D20")])
    db.close()
    print(json.dumps({k: v for k, v in summary.items() if k != "top_candidate_errors"}, indent=2))


if __name__ == "__main__":
    main()
