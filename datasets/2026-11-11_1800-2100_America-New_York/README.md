# MTA B/D/F/M collection — November 11, 2026

**API-based, unvalidated estimates. Candidate arrivals are not observed actual arrivals or measured delays.**

Collected locally on November 11, 2026, from 18:00 to 21:00 America/New_York (UTC−05:00), at 30-second intervals. The process completed successfully at 21:00:03; the first request was at 18:00:02 and the last at 20:59:32.

| Metric | Result |
|---|---:|
| Train-feed requests | 360 successful, 0 failed |
| Alert-feed requests | 360 successful, 0 failed |
| Raw snapshots | 720 |
| Prediction records | 330,882 |
| Vehicle records | 8,669 |
| Alert snapshot records | 13,047 |
| Arrival candidates | 1,102 |
| Candidates matched to ETA | 1,060 |

The existing route and station filters were used: B/D/F/M in both directions (including FX where present), the Manhattan study area, and boundary stations 59 St–Columbus Circle, Lexington Av/63 St, and Roosevelt Island. Raw responses retain the full contents returned by their respective endpoints; extracted observations follow the study filters.

Official notices include 3 distinct delay events, 2 distinct planned-reroute events, and 1 express-to-local classification belonging to one of those same delay events (not a fourth incident). The planned-reroute notices were not active at their source timestamps during this collection. Published alert windows are not independently verified incident durations. Successful requests do not establish feed freshness; the alert feed had 30 distinct source timestamps and a maximum source age of about 23.3 minutes.

## Detailed analysis

See [the analysis report](analysis/README.md) for route, station and within-session hourly comparisons, official events, label quality checks, and illustrative training rows. Seven of the 1,060 matched cases have trajectory review flags; the largest estimate, +14.6 minutes, crosses a 12-minute vehicle observation gap and a station revisit. It should not be presented as a verified delay.

The three hours form **one collection session**. Hourly breakdowns do not replace the additional independent collection windows planned for the experiment.

## Files

- `collection.tar.xz`: complete original run, including the unmodified SQLite database, all 720 compressed raw responses, every CSV, metadata and reports, plus selected collection logs and the exact collector source used.
- `collection_report.md` and `collection_result.json`: session-level request counts, boundaries, official notices and completion statistics.
- `report.md`, `summary.json` and CSV files: directly browsable analysis outputs. `alerts.csv.gz` contains the full alerts CSV losslessly compressed.
- `source_manifest.json`: SHA-256 and original byte size of every file inside the archive.
- `analysis/`: reproducible derived statistics, candidate training rows and the interpretation report.
- `SHA256SUMS`: hashes of the published files, including the derived analysis (excluding this checksum file itself).

The original local output directory name is `20261111T230000_613504Z` (UTC). Absolute Windows paths retained in the original reports are historical locations on the collection machine. Use the relative paths in this README after download.

## Extract and inspect

Download `collection.tar.xz` and extract with an archive utility supporting `.tar.xz`, or run:

```sh
tar -xf collection.tar.xz
```

The database is then at `20261111T230000_613504Z/observations.sqlite`, and raw responses are in `20261111T230000_613504Z/raw/`. The archive restores the database byte-for-byte; SQLite integrity and every archived file's SHA-256 were checked before publication.

The collector was based on repository commit `dfbcb357363e7592e5bcbf7359791e4d95371a6b`. `provenance/mta_explore.py` records the exact executed version; `operations/local_changes.patch` documents local scheduling guards and API-estimate labeling. The existing candidate-arrival algorithm was retained. The archive does not include preflight/test sessions or the Python virtual environment.
