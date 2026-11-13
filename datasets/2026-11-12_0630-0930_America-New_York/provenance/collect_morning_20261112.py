"""One authorized collection window, Nov 12 2026, 6:30–9:30AM New York."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mta_explore as collector

NY = ZoneInfo("America/New_York")
START = datetime(2026, 11, 12, 6, 30, tzinfo=NY).timestamp()
END = datetime(2026, 11, 12, 9, 30, tzinfo=NY).timestamp()
INTERVAL = 30
OPS = ROOT / "data/operations_20261112_morning"
RUN = ROOT / "data/20261112T113000_morning"
STATE = OPS / "status.json"


def iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, NY).isoformat()


def save(status, **extra):
    data = dict(status=status, updated_at=iso(), scheduled_start=iso(START),
                scheduled_end=iso(END), interval_seconds=INTERVAL,
                pid=os.getpid(), run_directory=str(RUN), **extra)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(STATE)
    print(json.dumps(data), flush=True)


def preflight():
    checks = {}
    for name, url in {"stations": collector.STATIONS_URL, **collector.URLS}.items():
        result = collector.fetch(url)
        if result["error"]:
            raise RuntimeError(name + ": " + result["error"])
        if name == "trains":
            message = collector.gtfs.FeedMessage()
            message.ParseFromString(result["body"])
            count = len(message.entity)
        else:
            parsed = json.loads(result["body"])
            count = len(collector.station_scope(parsed)) if name == "stations" else len(parsed.get("entity", []))
        checks[name] = dict(http_status=result["http_status"], bytes=len(result["body"]), entries=count)
    report = dict(checked_at=iso(), scheduled_start=iso(START), scheduled_end=iso(END),
                  scheduled_collections_per_feed=int((END-START)/INTERVAL), checks=checks)
    (OPS / "preflight.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate feeds now without starting the scheduled run")
    args = parser.parse_args()
    OPS.mkdir(parents=True, exist_ok=True)
    if args.check:
        preflight()
        return
    with (OPS / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("This collection is already running.", flush=True)
            return
        if STATE.exists() and json.loads(STATE.read_text()).get("status") == "completed":
            print("This one-time collection has already completed.", flush=True)
            return
        if time.time() >= END:
            save("expired", reason="Window ended; no backfill or collection outside the requested window")
            return
        if (RUN / "observations.sqlite").exists():
            raise RuntimeError("Existing partial run retained; inspect before retrying, do not overwrite it")
        awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        db = None
        try:
            save("preparing")
            metadata = collector.fetch(collector.STATIONS_URL)
            if metadata["error"]:
                raise RuntimeError("Cannot establish station scope: " + metadata["error"])
            scope = collector.station_scope(json.loads(metadata["body"]))
            (RUN / "raw").mkdir(parents=True, exist_ok=False)
            (RUN / "stations_source.json").write_bytes(metadata["body"])
            (RUN / "scope.json").write_text(json.dumps(scope, indent=2, ensure_ascii=False))
            (RUN / "manifest.json").write_text(json.dumps(dict(
                scheduled_start=iso(START), scheduled_end=iso(END), interval=INTERVAL,
                duration_seconds=END-START, urls=collector.URLS, station_source=collector.STATIONS_URL,
                routes=sorted(collector.ROUTES),
                scope="Same geographic and route filters as the Nov 11 evening run",
                max_source_age_for_arrival_estimates=120), indent=2))
            db = collector.database(RUN / "observations.sqlite")
            save("waiting")
            while time.time() < START:
                time.sleep(max(0, min(10, START-time.time())))
            slot = max(0, math.floor((time.time()-START)/INTERVAL))
            completed_slots = 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                while slot < int((END-START)/INTERVAL):
                    target = START + slot*INTERVAL
                    while time.time() < target:
                        time.sleep(max(0, min(10, target-time.time())))
                    if time.time() >= END:
                        break
                    results = {kind: pool.submit(collector.fetch, url) for kind, url in collector.URLS.items()}
                    for kind, future in results.items():
                        collector.ingest(db, RUN, kind, future.result(), scope)
                    completed_slots += 1
                    save("collecting", completed_slots=completed_slots, scheduled_slot=slot,
                         missed_slots=slot+1-completed_slots)
                    slot = max(slot+1, math.floor((time.time()-START)/INTERVAL)+1)
                while time.time() < END:
                    time.sleep(max(0, min(10, END-time.time())))
            db.close()
            db = None
            save("analyzing", completed_slots=completed_slots)
            collector.analyze(RUN)
            subprocess.run([sys.executable, str(ROOT / "scripts/analyze_collection.py"),
                            str(RUN), str(RUN / "analysis")], cwd=ROOT, check=True)
            summary = json.loads((RUN / "summary.json").read_text())
            save("completed", completed_slots=completed_slots, summary=summary)
        except BaseException as exc:
            save("failed", error=str(exc))
            traceback.print_exc()
            raise
        finally:
            if db is not None:
                db.close()
            awake.terminate()


if __name__ == "__main__":
    main()
