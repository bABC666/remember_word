"""Read-only verification of the frozen final package and an isolated imported DB."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "test-artifacts/netem-final-20261005"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def verify(frozen: Path, database: Path) -> dict:
    safe = (ROOT / "test-artifacts").resolve()
    if not frozen.resolve().is_relative_to(safe) or not database.resolve().is_relative_to(safe):
        raise ValueError("verification accepts only isolated test-artifacts paths")
    # The source-controlled index is the authority for this final candidate;
    # a replacement package cannot authorize itself by rewriting its hashes.
    index = json.loads((ROOT / "docs/NETEM-FINAL-CANDIDATE-EVIDENCE-2026-10-05.json").read_text("utf-8"))
    fingerprint_path = frozen / "fingerprints.json"
    assert sha(fingerprint_path) == index["files"]["test-artifacts/netem-final-20261005/frozen/fingerprints.json"], "candidate fingerprints differ from final evidence index"
    fingerprint = json.loads(fingerprint_path.read_text("utf-8"))
    assert fingerprint["package_sha256"] == index["candidate"]["package_sha256"], "not the frozen final candidate"
    actual = {p.relative_to(frozen).as_posix(): sha(p) for p in sorted(frozen.rglob("*"))
              if p.is_file() and p.name != "fingerprints.json"}
    assert actual == fingerprint["files"], "frozen files changed"
    assert hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == fingerprint["package_sha256"]
    final = rows(frozen / "candidate-provenance.csv")
    baseline_path = ROOT / "test-artifacts/default-lexicon-candidate-v2/candidate-provenance.csv"
    assert sha(baseline_path) == "fb9b943c36abce1e6441e41f2b3485e1f0fbdd017af47713134c9b1fdc37a9e9"
    baseline = rows(baseline_path)
    assert [(r["word"], r["sequence"], r["netem_rank"]) for r in final] == [(r["word"], r["sequence"], r["netem_rank"]) for r in baseline]
    assert len(final) == 5528 and sum(not r["meaning"] for r in final) == 77
    package = frozen / "public-package"
    manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    attribution_rows = rows(package / "zhwiktionary-attribution.csv")
    assert len(attribution_rows) == 637 and all(r["adapter_license"] == "CC-BY-SA-4.0" for r in attribution_rows)
    for spec in manifest["sources"]:
        expected_rows = [{"word": r["word"], "meaning": r["meaning"]} for r in final if r["meaning_source"] == spec["id"]]
        content = rows(package / spec["file"])
        if spec["id"] == "netem":
            assert [r["word"] for r in content] == [r["word"] for r in final]
            assert all(not r["meaning"] for r in content)
        else:
            assert [{"word": r["word"], "meaning": r["meaning"]} for r in content] == expected_rows
        assert json.loads((package / (Path(spec["file"]).stem + ".SOURCE.json")).read_text("utf-8")) == spec["attribution"]
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        assert con.execute("select version_num from alembic_version").fetchone()[0] == "0015_session_autoincrement"
        assert con.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert con.execute("pragma foreign_key_check").fetchall() == []
        lexicon_id = con.execute("select id from lexicon where source_type='netem' and owner_user_id is null").fetchone()[0]
        imported = con.execute("select id,word,sequence,source_meanings,source_raw from lexicon_entry where lexicon_id=? order by sequence", (lexicon_id,)).fetchall()
        assert len(imported) == 5528
        for stored, expected in zip(imported, final, strict=True):
            entry_id, word, sequence, meanings, raw = stored
            assert (word, sequence, json.loads(meanings)) == (expected["word"], int(expected["sequence"]), [expected["meaning"]] if expected["meaning"] else [])
            if not expected["meaning"]:
                assert con.execute("select count(*) from entry_source_evidence where lexicon_entry_id=? and field_kind='meaning'", (entry_id,)).fetchone()[0] == 0
                assert next(csv.reader([raw]))[1] == ""
        count = con.execute("select count(*) from entry_source_evidence where lexicon_entry_id in (select id from lexicon_entry where lexicon_id=?)", (lexicon_id,)).fetchone()[0]
        assert count == 16430
        artifacts = con.execute("select name,mapping_json from source_artifact").fetchall()
        by_file = {spec["file"]: spec for spec in manifest["sources"]}
        for name, mapping in artifacts:
            assert json.loads(mapping)["attribution"] == by_file[name]["attribution"]
    return {"package_sha256": fingerprint["package_sha256"], "words": 5528,
            "definitions": 5451, "empty": 77, "source_evidence": count,
            "order_matches_v2": True, "empty_has_no_meaning_evidence_or_raw_fallback": True,
            "frozen_attribution_matches_database": True, "integrity": "ok", "foreign_keys": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", type=Path, default=DEFAULT / "frozen")
    parser.add_argument("--database", type=Path, default=DEFAULT / "browser-verified/vocab.db")
    args = parser.parse_args()
    print(json.dumps(verify(args.frozen, args.database), ensure_ascii=False, indent=2))
