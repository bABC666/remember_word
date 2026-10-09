"""Optional isolated acceptance of the locally generated default candidate."""

import csv
import json
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import LexiconEntry

PACKAGE = Path(__file__).resolve().parents[2] / "test-artifacts/default-lexicon-candidate"
PACKAGES = [PACKAGE, PACKAGE.with_name("default-lexicon-candidate-v2")]


@pytest.mark.parametrize("package", PACKAGES)
def test_candidate_through_existing_file_import(world, package):
    if not (package / "manifest.json").exists():
        pytest.skip("build candidate first")
    manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    with (package / "candidate-provenance.csv").open(encoding="utf-8", newline="") as handle:
        provenance = list(csv.DictReader(handle))
    source_path = next(Path(__file__).resolve().parents[2] / name for name in manifest["source_sha256"]
                       if name.endswith("netem_full_list.json"))
    original = next(iter(json.loads(source_path.read_text("utf-8")).values()))
    expected = []
    seen = set()
    for row in original:
        word = row["单词"].strip()
        if word.casefold() not in seen:
            expected.append((row["序号"], word))
            seen.add(word.casefold())
    assert [(int(row["netem_rank"]), row["word"]) for row in provenance] == expected
    assert {origin: sum(row["meaning_source"] == origin for row in provenance)
            for origin in ("wikdict", "zhwiktionary", "missing")} == manifest["counts"]
    part = package / manifest["import_parts"][0]["file"]
    content = part.read_bytes()

    def upload():
        return {"file": (part.name, BytesIO(content), "text/csv")}

    preview = world.client.post("/api/lexicons/file-preview", files=upload())
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"] == {"valid": 5522, "duplicate": 0, "error": 6}
    imported = world.client.post("/api/lexicons/file-import", files=upload(), data={
        "name": "隔离候选验收", "preview_sha256": preview.json()["sha256"]})
    assert imported.status_code == 201, imported.text
    assert imported.json()["imported_count"] == 5522
    assert imported.json()["source_type"] == "user_file"
    lexicon_id = imported.json()["id"]
    with world.session() as session:
        entries = session.scalars(select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon_id).order_by(LexiconEntry.sequence)).all()
        assert len(entries) == 5522
        assert [entry.sequence for entry in entries] == list(range(1, 5523))
        assert len({entry.normalized_word for entry in entries}) == 5522
        assert entries[0].source_meanings == [preview.json()["rows"][0]["meaning"]]
    queue = world.client.get("/api/study/today", params={"lexicon_id": lexicon_id})
    assert queue.status_code == 200
    assert all(row["meaning_origin"] == "user_provided" for row in queue.json()["words"])


@pytest.mark.skipif(not (PACKAGES[1] / "manifest.json").exists(), reason="build gap candidate first")
def test_gap_candidate_changes_only_old_missing_words():
    def rows(package):
        with (package / "candidate-provenance.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    before, after = rows(PACKAGES[0]), rows(PACKAGES[1])
    assert [(row["word"], row["netem_rank"], row["sequence"]) for row in before] == [
        (row["word"], row["netem_rank"], row["sequence"]) for row in after]
    changed = [(old, new) for old, new in zip(before, after, strict=True)
               if old["meaning"] != new["meaning"]]
    assert len(changed) == 277
    assert all(old["meaning_source"] == "missing" and new["meaning_source"] == "zhwiktionary"
               for old, new in changed)
    assert all(new["source_locator"].startswith("oldid:") and "#line:" in new["source_locator"]
               for _, new in changed)
    by_word = {row["word"]: row for row in after}
    assert by_word["seemingly"]["meaning_source"] == "missing"
    assert by_word["fertiliser"]["meaning_source"] == "missing"
    assert by_word["X-ray"]["meaning_source"] == "zhwiktionary"
    assert by_word["well-known"]["meaning_source"] == "zhwiktionary"
