"""One authorized collection window, Nov 12 2026, 12–3PM New York.

Reuse the verified morning collector with a separate window and output paths.
"""
from datetime import datetime
import collect_morning_20261112 as run

run.__doc__ = __doc__
run.START = datetime(2026, 11, 12, 12, 0, tzinfo=run.NY).timestamp()
run.END = datetime(2026, 11, 12, 15, 0, tzinfo=run.NY).timestamp()
run.OPS = run.ROOT / "data/operations_20261112_midday"
run.RUN = run.ROOT / "data/20261112T170000_midday"
run.STATE = run.OPS / "status.json"

if __name__ == "__main__":
    run.main()
