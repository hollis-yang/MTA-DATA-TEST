# MTA collection — 2026-11-12_1200-1500_America-New_York

30-second collection of B/D/F/M (including FX when present), both directions, using the same geographic scope as the November 11 evening run.

- Requests: [('alerts', 360, 360), ('trains', 360, 360)]; 720 raw response files.
- Prediction records: 372,293.
- Exact timing and completion: [operations/status.json](operations/status.json).

## Contents

`collection.tar.xz` preserves the complete run, including raw responses, SQLite, CSVs and analysis. Extract with `tar -xf collection.tar.xz`; the database is under `20261112T170000_midday/observations.sqlite`. `source_manifest.json` records every archived file's SHA-256 and size. CSV/JSON reports and `analysis/` are also directly browsable; alerts CSV is gzip-compressed. `provenance/` contains the collector and analysis source at publication. `SHA256SUMS` covers published files.

The original `analysis/` uses a 4–6 minute ETA reference. Later recalculations, including 9–11 minute references and F/FX grouping, are preserved separately in [recalculations](../recalculations/README.md). Do not mix methods in comparisons.

Candidate arrivals are inferred from API observations and are not independently verified physical arrivals. Alert rows are repeated snapshots, not independent incidents. Historical absolute local paths in metadata are retained for provenance.
