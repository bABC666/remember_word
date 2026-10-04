"""Verify archived launch evidence and isolated databases without writing files.

Requires the local, ignored fixed inputs named in the committed hashes-only
index. This verifies engineering evidence, never permission or semantic quality.
"""

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "test-artifacts/netem-launch-decision-20261005"
INDEX = ROOT / "docs/NETEM-V2-LAUNCH-EVIDENCE-2026-10-05.json"
DECISION = ROOT / "docs/V1.2-NETEM-V2-FIRST-LAUNCH-DECISION-2026-10-05.md"


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def database_snapshot(name, meanings, evidence_count):
    path = (AUDIT / name / "vocab.db").resolve()
    assert path.is_relative_to((ROOT / "test-artifacts").resolve())
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT word, sequence, source_meanings, source_raw FROM lexicon_entry ORDER BY sequence"
        ).fetchall()
        assert len(rows) == 5528
        assert sum(bool(json.loads(row[2])) for row in rows) == meanings
        assert (
            connection.execute("SELECT count(*) FROM entry_source_evidence").fetchone()[0]
            == evidence_count
        )
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0015_session_autoincrement"
        )
        if name == "closeout-exclusion-browser":
            excluded = read_json(AUDIT / "exclusion-scenario.json")["excluded_words"]
            for word in excluded:
                row = next(row for row in rows if row[0] == word)
                assert json.loads(row[2]) == []
                assert re.fullmatch(
                    re.escape(word) + r",,netem:rank:\d+,70dc6b68c855f21e666a7a291ff8ead5ca1f7b44",
                    row[3],
                )
                assert (
                    connection.execute(
                        "SELECT count(*) FROM entry_source_evidence WHERE normalized_word=? AND field_kind='meaning'",
                        (word.casefold(),),
                    ).fetchone()[0]
                    == 0
                )
    return rows


def main():
    index = read_json(INDEX)
    for relative, expected in index["files"].items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
    for relative, expected in read_json(AUDIT / "preserved-before.json").items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    links = re.findall(r"\]\((\.\./test-artifacts/[^)]+)\)", DECISION.read_text("utf-8"))
    for link in links:
        path = (DECISION.parent / link).resolve()
        assert path.is_file(), link
        assert path.relative_to(ROOT).as_posix() in index["files"], link
    sample = read_json(AUDIT / "sample.json")
    assert sample["pool_sizes"] == {"wikdict": 4823, "zh_original": 369, "zh_restored": 277}
    assert len(sample["rows"]) == 68
    with (AUDIT / "sample-review.csv").open(encoding="utf-8", newline="") as handle:
        review = list(csv.DictReader(handle))
    counts = {
        status: sum(row["status"] == status for row in review)
        for status in {row["status"] for row in review}
    }
    assert counts == {"需剔除样本译文": 7, "边界修复": 4, "待语义裁定": 5, "抽样未见明显问题": 52}
    repair = read_json(AUDIT / "repair-results.json")
    assert (repair["changed_words"], repair["removed_segments"], repair["empty_after"]) == (
        17,
        29,
        0,
    )
    scenario = read_json(AUDIT / "exclusion-scenario.json")
    assert len(scenario["excluded_words"]) == 13
    assert (scenario["remaining_definitions"], scenario["empty_words"]) == (5456, 72)
    assert scenario["status"] == "isolated-scenario-only"
    original = database_snapshot("browser", 5469, 16466)
    repaired = database_snapshot("closeout-repaired-browser", 5469, 16466)
    excluded = database_snapshot("closeout-exclusion-browser", 5456, 16440)
    assert (
        [(r[0], r[1]) for r in original]
        == [(r[0], r[1]) for r in repaired]
        == [(r[0], r[1]) for r in excluded]
    )
    assert sum(a[2] != b[2] for a, b in zip(original, repaired, strict=True)) == 17
    assert [r[0] for r in original if not json.loads(r[2])] == [
        r[0] for r in repaired if not json.loads(r[2])
    ]
    print(
        json.dumps(
            {
                "verified_files": len(index["files"]),
                "document_evidence_links": len(links),
                "preserved_documents": 3,
                "sample_words": 68,
                "repair": "17 words / 29 segments",
                "isolated_exclusion": "13 meanings removed; 5456 / 72",
                "public_package": "unchanged; publication not approved",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
