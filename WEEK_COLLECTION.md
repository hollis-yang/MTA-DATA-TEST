# 11/13 10:15PM–11/21 12AM all-borough subway collection

Manually run `scripts/collect_week_20261114.py`. No scheduled task is installed.

- Window: **November 13, 2026, 10:15PM through November 21, 2026, 12AM, America/New_York**, independent of the computer's timezone.
- Interval: 30 seconds; 20,370 rounds, up to 183,330 requests (eight subway feed groups plus official subway notices).
- Scope: all five boroughs, both directions, all subway routes returned by the feeds, including express/shuttle variants and Staten Island Railway.
- Endpoints are route-group feeds, not borough-specific APIs. Full compressed original responses are retained, including every returned stop and full-line official notice. The optional offline export retains all station IDs without borough filtering, including IDs not yet in the station metadata.
- Feeds may overlap (for example G can occur in BDFM as well as G). Preserve source provenance; use `unique_predictions` for exact duplicate prediction removal. Different predictions at the same source timestamp require an explicit reconciliation policy before modeling. Shuttle/express variants are retained as returned; unavailable services are not fabricated.

## Run on macOS / Linux

Install Git and Python 3.11 or newer, then:

```sh
git clone https://github.com/hollis-yang/MTA-DATA-TEST.git
cd MTA-DATA-TEST
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/collect_week_20261114.py
```

If already cloned, run `git pull --ff-only` in the repository before installing dependencies and launching the script.

## Run on Windows (PowerShell)

```powershell
git clone https://github.com/hollis-yang/MTA-DATA-TEST.git
cd MTA-DATA-TEST
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\collect_week_20261114.py
```

You can launch before 10:15PM on Nov 13: it fetches station metadata, prints `waiting`, and waits for the start. Keep the terminal/process running, the computer awake and connected. macOS uses caffeinate against idle sleep; on other systems disable sleep yourself. Closing a laptop lid, rebooting or losing connectivity can still interrupt collection. There is no auto-launch or process supervisor.

Restart the same command after interruption. It acquires a file lock, preserves existing daily files and skips elapsed slots. It never backfills missed data or extends the end time. Request/parse failures are recorded, and collection continues at the next scheduled slot. Startup metadata failures or fatal disk errors exit visibly; fix the cause and rerun. Below 5 GiB free space, it pauses requests and resumes only when space recovers; those intervals are missed.

## Output and status

`data/20261113T221500_20261121_all_boroughs/` contains `status.json`, scope/metadata/manifest and one directory per local day. Each day contains gzip-compressed original responses and `index.sqlite` with timestamps, HTTP status, source timestamps, hashes and errors. It does not expand hundreds of millions of predictions into SQLite during collection.

```sh
.venv/bin/python scripts/collect_week_20261114.py --summary
```

The final summary reports successful, failed/interrupted and unattempted requests. An interrupted request is deliberately not retried within the same slot. `completed` means the requested window ended; inspect counts for gaps.

## Storage estimate

A live probe on Nov 13 evening measured approximately **176 KB compressed / 1.56 MB uncompressed per round** for the nine feeds used here. At 20,370 rounds, that is approximately **3.6 GB compressed response content** and **32 GB of downloaded response bodies**. These are decimal GB. Allow **4–7 GB on disk**, including SQLite indexes, filesystem allocation and changing traffic/notice volume; **reserve 10 GB** plus the 5 GiB free-space guard for comfortable headroom. This is an estimate from one evening snapshot, not a bound. HTTP headers and protocol overhead are additional network usage.

Expanding all predictions into SQLite and CSV can require substantially more space; export one day at a time as needed.

## Optional offline all-borough export

After a day's collection has finished:

```sh
.venv/bin/python scripts/collect_week_20261114.py --export-day 20261114
```

This writes `exports/20261114/observations.sqlite`, all-borough `scope.json`, and copied raw responses. It checks raw SHA-256 hashes, preserves feed/slot provenance in `source_polls`, and retains full-line notices and full trip paths for analysis. This export takes additional disk space and will not overwrite an existing export. Candidate arrival inference or model training is not automatically run: the original BDFM analysis workflow must be adapted to multi-feed identity, overlaps and day boundaries before full-network analysis. API vehicle states remain unvalidated arrival proxies.

If you downloaded the earlier Manhattan version, stop it before updating with `git pull --ff-only`, then run the same script command. The revised version uses a new `all_boroughs` output directory and preserves any earlier files.

Start moved earlier to Nov 13 at 10:15PM for a live check; the Nov 21 midnight end remains unchanged (7 days 1 hour 45 minutes total). Stop an already-waiting process with Ctrl+C before pulling this update and restarting. The script filename stays unchanged. This revision uses a new output directory so slot numbers cannot conflict with the previous midnight-start configuration. If started late, collection begins at the next scheduled slot; elapsed slots are not backfilled.
