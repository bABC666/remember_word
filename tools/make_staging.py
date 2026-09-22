"""Build a Level 2 staging clone from the verified V1.1 source.

Three database levels are used from now on:

* **Level 1** pytest temporary databases -- unit, API and migration tests.
* **Level 2** staging clone (this script) -- the only place where a migration may
  be rehearsed against *real* data content. It is never production and may be
  deleted and recreated at will.
* **Level 3** ``data/vocab.db`` -- production. Never migrated during development.

The clone is verified against the recorded baseline before and after use, so a
staging run can never silently start from damaged data.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("verified_db", TOOLS / "verified_db.py")
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

#: Verified V1.1 content: the restore source, already proven byte-identical to
#: production's V1.1 state.
SOURCE = Path("data/recovery/vocab-restored-v1.1.db")
BASELINE = Path("data/recovery/baseline.json")
STAGING_DIR = Path("data/staging")
STAGING = STAGING_DIR / "v1.1-realdata-migration-test.db"
STAGING_BASELINE = Path("data/recovery/staging-pre-migration-baseline.json")


def main() -> int:
    if not SOURCE.exists():
        print("verified V1.1 source missing:", SOURCE)
        return 1
    if not BASELINE.exists():
        print("baseline missing:", BASELINE)
        return 1

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # Refuse to clone from a source that no longer matches the baseline.
    baseline = verified_db.load_baseline(BASELINE)
    ok, report = verified_db.compare_against_baseline(SOURCE, baseline)
    if not ok:
        print("REFUSING to clone: the source no longer matches the baseline")
        for failure in report["failures"]:
            print("  FAIL:", failure)
        return 1
    print("source verified against baseline:", report["sha256"])

    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(STAGING) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    shutil.copy2(SOURCE, STAGING)
    print(f"staging clone : {STAGING} ({STAGING.stat().st_size} bytes)")

    snapshot = verified_db.capture_baseline(
        STAGING,
        label="staging clone before the V1.2 migration",
        notes=(
            "Real V1.1 data content at revision 0003, cloned from the verified restore "
            "source. Compare the migrated result against this baseline."
        ),
    )
    verified_db.save_baseline(snapshot, STAGING_BASELINE)

    print()
    print("pre-migration baseline:", STAGING_BASELINE)
    print("  sha256  :", snapshot["sha256"])
    print("  revision:", snapshot["alembic_revision"])
    for name, info in snapshot["tables"].items():
        print("  %-24s %-12s rows=%s" % (name, info["classification"], info["rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
