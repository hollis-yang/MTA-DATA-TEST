#!/usr/bin/env python3
"""Keep the original vehicle-based arrival candidates; move the ETA reference.

Source data and previous analyses are read only. Both unscreened and screened
results are retained. The original 4–6 minute references are independently
reproduced as a check and as a paired comparison, not used to select the new ETA.
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
STATIONS = {"D15": "Rockefeller", "D16": "Bryant Pk", "D17": "Herald Sq", "D20": "Wash Sq"}


def local(ts):
    return dt.datetime.fromtimestamp(float(ts), TZ).isoformat(timespec="seconds")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    if rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def stats(rows, field="eta_error_min"):
    x = [r[field] for r in rows]
    return dict(cases=len(x), distinct_trips=len({r["trip_key"] for r in rows}),
        mean_error_min=statistics.mean(x) if x else None,
        median_error_min=statistics.median(x) if x else None,
        mean_absolute_error_min=statistics.mean(abs(v) for v in x) if x else None,
        over_1_min_count=sum(v > 1 for v in x),
        over_1_min_pct=100 * sum(v > 1 for v in x) / len(x) if x else None,
        min_error_min=min(x) if x else None, max_error_min=max(x) if x else None)


def analyze(run, output, lo_min, hi_min):
    source_files = [run / "observations.sqlite", run / "arrival_candidates.csv", run / "arrival_evidence.csv"]
    hashes = {p.name: sha256(p) for p in source_files}
    db = sqlite3.connect(source_files[0].resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    candidates = read_csv(run / "arrival_candidates.csv")
    assert len(candidates) == len({(c["trip_key"], c["stop"]) for c in candidates})
    polls = {r["id"]: dict(r) for r in db.execute("SELECT * FROM polls")}
    predictions = collections.defaultdict(list)
    for r in db.execute("SELECT * FROM predictions ORDER BY source_ts,poll_id"):
        if r["eta"] is None:
            continue
        poll = polls[r["poll_id"]]
        if poll["error"] or poll["http_status"] != 200 or not -5 <= poll["received"] - poll["source_ts"] <= 120:
            continue
        predictions[(r["trip_key"], r["stop"])].append(dict(r))
    vehicles = collections.defaultdict(list)
    vehicles_at_source = collections.defaultdict(list)
    for r in db.execute("SELECT * FROM vehicles ORDER BY source_ts,poll_id"):
        vehicles[r["trip_key"]].append(dict(r))
        vehicles_at_source[(r["trip_key"], r["source_ts"])].append(dict(r))
    evidence = collections.defaultdict(list)
    for r in read_csv(run / "arrival_evidence.csv"):
        evidence[r["trip_key"]].append(r)

    def flags_for(c, reference):
        # Same trajectory checks as the original descriptive analysis, now over
        # the full interval from the new reference to the arrival observation.
        history = [v for v in vehicles[c["trip_key"]]
                   if reference["source_ts"] <= v["source_ts"] <= float(c["observation_end"])]
        flags = []
        if any(b["source_ts"] - a["source_ts"] > 90 for a, b in zip(history, history[1:])):
            flags.append("vehicle_observation_gap_over_90s")
        sequence = [v["stop"] for i,v in enumerate(history) if not i or v["stop"] != history[i-1]["stop"]]
        if len(sequence) != len(set(sequence)):
            flags.append("station_revisited")
        if any(e["reason"] == "not_expected_next_station" and
               reference["source_ts"] < float(e["source_ts"]) <= float(c["observation_end"])
               for e in evidence[c["trip_key"]]):
            flags.append("unexpected_station_step")
        return flags

    def reference_for(c, low, high):
        return next((p for p in predictions[(c["trip_key"], c["stop"])]
                     if p["source_ts"] < float(c["observation_start"])
                     and low * 60 <= p["eta"] - p["source_ts"] <= high * 60), None)

    matched, missing, verified_arrivals, verified_old = [], [], 0, 0
    for c in candidates:
        arrival = float(c["estimated_event_ts"])
        # Check unchanged arrival labels against source observations, without
        # adopting any closest-to-zero ETA as the arrival time.
        if c["method"] == "station_change_timestamp_path_consistent_UNVALIDATED":
            assert any(v["stop"] == c["stop"] and v["vehicle_ts"] == arrival
                       for v in vehicles_at_source[(c["trip_key"], int(float(c["observation_end"])))] )
        else:
            assert c["method"] == "observed_transit_to_stopped_transition_UNVALIDATED"
            assert arrival == (float(c["observation_start"]) + float(c["observation_end"])) / 2
        verified_arrivals += 1
        old = reference_for(c, 4, 6)
        assert bool(old) == bool(c["eta_error_proxy_min"])
        if old:
            assert old["eta"] == int(c["reference_eta"])
            assert abs((arrival - old["eta"]) / 60 - float(c["eta_error_proxy_min"])) < 1e-9
            verified_old += 1
        reference = reference_for(c, lo_min, hi_min)
        if reference is None:
            missing.append(dict(trip_key=c["trip_key"], route=c["route"], stop=c["stop"],
                estimated_arrival_local=local(arrival), reason=f"no_prior_{lo_min}_to_{hi_min}_minute_reference"))
            continue
        # Independently select the earliest qualifying prediction in SQLite.
        check = db.execute("""SELECT p.eta,p.source_ts,p.poll_id FROM predictions p
            JOIN polls q ON q.id=p.poll_id WHERE p.trip_key=? AND p.stop=? AND p.source_ts<?
            AND p.eta-p.source_ts BETWEEN ? AND ? AND q.error IS NULL AND q.http_status=200
            AND q.received-q.source_ts BETWEEN -5 AND 120 ORDER BY p.source_ts,p.poll_id LIMIT 1""",
            (c["trip_key"], c["stop"], float(c["observation_start"]), lo_min*60, hi_min*60)).fetchone()
        assert (check["eta"], check["source_ts"], check["poll_id"]) == (reference["eta"], reference["source_ts"], reference["poll_id"])
        flags = flags_for(c, reference)
        old_flags = flags_for(c, old) if old else []
        matched.append(dict(trip_key=c["trip_key"], route=c["route"], stop=c["stop"],
            station_code=c["stop"][:-1], station=c["station"], direction=c["stop"][-1],
            reference_poll_id=reference["poll_id"], reference_source_ts=reference["source_ts"],
            reference_source_local=local(reference["source_ts"]), reference_eta=reference["eta"],
            reference_eta_local=local(reference["eta"]), reference_remaining_sec=reference["eta"]-reference["source_ts"],
            estimated_arrival_ts=arrival, estimated_arrival_local=local(arrival), arrival_method=c["method"],
            observation_start=float(c["observation_start"]), observation_end=float(c["observation_end"]),
            eta_error_min=(arrival-reference["eta"])/60, quality_flags=";".join(flags),
            included_after_screen=not flags, old_4_6_eta=old["eta"] if old else None,
            old_4_6_error_min=(arrival-old["eta"])/60 if old else None,
            old_4_6_quality_flags=";".join(old_flags), physical_arrival_validated=False))
    clean = [r for r in matched if r["included_after_screen"]]
    paired = [r for r in clean if r["old_4_6_error_min"] is not None and not r["old_4_6_quality_flags"]]
    stations, routes = [], []
    for sample, rows in (("all_matched", matched), ("exclude_trajectory_flags", clean)):
        for code, name in STATIONS.items():
            stations.append(dict(sample=sample, station_code=code, station=name,
                                 **stats([r for r in rows if r["station_code"] == code])))
        for route in ["B", "D", "F", "M"]:
            members = ["F", "FX"] if route == "F" else [route]
            routes.append(dict(sample=sample, route=route, included_source_routes=";".join(members),
                               **stats([r for r in rows if r["route"] in members])))
        assert sum(r["cases"] for r in routes if r["sample"] == sample) == len(rows)
    summary = dict(source_run=str(run.resolve()), source_sha256=hashes,
        method=dict(arrival="Original vehicle station-change/path-consistent arrival candidates, unchanged",
                    reference=f"First observed valid prediction with {lo_min} <= remaining ETA <= {hi_min} minutes, before arrival observation interval",
                    remaining_eta="Prediction absolute timestamp minus feed source timestamp",
                    error="(Unchanged estimated arrival timestamp - reference ETA timestamp) / 60",
                    quality_screen="Exclude vehicle observation gaps >90s, station revisits, and unexpected station steps between reference and arrival",
                    unit="One trip at one directional stop; a trip can contribute at several stops",
                    scope="Original collection scope; route F includes F and FX; station and overall aggregates retain all collected routes",
                    limitation="Arrival timestamps inferred from API vehicle observations, not independently validated physical arrivals"),
        arrival_candidates=len(candidates), old_4_6_matched=verified_old,
        new_reference_matched=len(matched), missing_reference=len(missing),
        all_matched=stats(matched), exclude_trajectory_flags=stats(clean),
        excluded_cases=len(matched)-len(clean),
        flag_counts=dict(collections.Counter(f for r in matched for f in r["quality_flags"].split(";") if f)),
        selected_reference_remaining_seconds=dict(min=min(r["reference_remaining_sec"] for r in matched),
            median=statistics.median(r["reference_remaining_sec"] for r in matched),
            max=max(r["reference_remaining_sec"] for r in matched)),
        paired_comparison=dict(same_clean_cases=len(paired), reference_4_6=stats(paired,"old_4_6_error_min"),
                               reference_9_11=stats(paired)),
        major_stations=stations, routes=routes,
        validation=dict(arrival_candidates_verified_against_source=verified_arrivals,
            original_4_6_references_reproduced=verified_old, independent_sql_reference_selection="passed"))
    db.close()
    assert all(sha256(p) == hashes[p.name] for p in source_files)
    summary["validation"]["source_files_unchanged"] = True
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "matched_cases.csv", matched)
    write_csv(output / "missing_reference.csv", missing)
    write_csv(output / "major_stations.csv", stations)
    write_csv(output / "by_route.csv", routes)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--min-minutes", type=int, default=9)
    parser.add_argument("--max-minutes", type=int, default=11)
    args = parser.parse_args()
    result = analyze(args.run, args.output, args.min_minutes, args.max_minutes)
    print(json.dumps({k: result[k] for k in ("arrival_candidates", "new_reference_matched", "all_matched",
        "exclude_trajectory_flags", "excluded_cases", "flag_counts", "paired_comparison", "validation")}, indent=2))
