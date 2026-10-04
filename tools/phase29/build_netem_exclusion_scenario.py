"""Create an isolated publish-exclusion scenario for the launch decision.

This is a count/product-behavior rehearsal, never a licence-approved package.
"""

import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "test-artifacts/netem-launch-decision-20261005"
SOURCE = AUDIT / "repaired-public-package"
TARGET = AUDIT / "exclusion-scenario-package"
EXCLUDE = {
    "shortage",
    "temporary",
    "thoughtful",
    "tribute",
    "tonight",
    "ashore",
    "set",
    "complex",
    "bit",
    "honor",
    "inhabit",
    "pitch",
    "progressive",
}


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in ("netem-words.csv", "manifest.json", "decisions.json"):
        shutil.copyfile(SOURCE / name, TARGET / name)
    retained = {}
    for name in ("wikdict.csv", "zhwiktionary.csv"):
        with (SOURCE / name).open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        kept = [row for row in rows if row["word"] not in EXCLUDE]
        retained[name] = len(kept)
        with (TARGET / name).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, rows[0], lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept)
    assert retained == {"wikdict.csv": 4817, "zhwiktionary.csv": 639}
    manifest_file = TARGET / "manifest.json"
    manifest = json.loads(manifest_file.read_text("utf-8"))
    for item in manifest["sources"]:
        item["provenance"]["display_scope"] = (
            "isolated-exclusion-scenario-only; publication-pending-approval"
        )
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (AUDIT / "exclusion-scenario.json").write_text(
        json.dumps(
            {
                "excluded_words": sorted(EXCLUDE),
                "retained_meaning_rows": retained,
                "remaining_definitions": sum(retained.values()),
                "empty_words": 5528 - sum(retained.values()),
                "status": "isolated-scenario-only",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(retained))


if __name__ == "__main__":
    main()
