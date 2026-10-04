"""Full v2 candidate acceptance through the existing public import services.

The fixture database is migrated under pytest and never points at production.
The pinned candidate and source snapshots remain ignored local evidence.
"""

from __future__ import annotations

import csv
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import EntrySourceEvidence, Lexicon, LexiconEntry, SourceArtifact, User
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "test-artifacts/default-lexicon-candidate-v2"


@pytest.mark.skipif(not (CANDIDATE / "manifest.json").exists(), reason="local v2 candidate unavailable")
def test_v2_public_import_all_words_sources_and_learning(
    make_world, created_lexicon_cleanup, tmp_path: Path
) -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    script = ROOT / "tools/phase29/build_default_public_package.py"
    spec = spec_from_file_location("default_public_package", script)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    source_root = tmp_path / "package"
    generated = module.run(CANDIDATE, source_root, "2026-10-04T00:00:00Z")
    assert generated["rows"] == 5528
    assert generated["counts"] == {"wikdict": 4823, "zhwiktionary": 646, "missing": 59}
    with (CANDIDATE / "candidate-provenance.csv").open(encoding="utf-8", newline="") as handle:
        candidate = list(csv.DictReader(handle))
    plan = build_plan(manifest_path=Path("manifest.json"), source_root=source_root,
                      decisions_path=Path("decisions.json"), target_lexicon="NETEM isolated v2")
    assert plan["confirmation_ready"], plan["confirmation_blockers"]
    assert plan["summary"]["ready_entries"] == 5528
    assert [entry["default_snapshot"]["word"] for entry in plan["entries"]] == [
        row["word"] for row in candidate]

    admin = make_world("netem-v2-admin", role="admin")
    learner = make_world("netem-v2-learner")
    with admin.session() as session:
        target = Lexicon(name="NETEM isolated v2", visibility="public", source_type="netem",
                         description="isolated candidate only")
        session.add(target)
        session.commit()
        result = confirm_plan(session, plan=plan, administrator=session.get(User, admin.user_id),
                              source_root=source_root)
        assert result["entries_created"] == 5528
        assert result["entries_matched"] == 0
        target_id = target.id
        entries = session.scalars(select(LexiconEntry).where(LexiconEntry.lexicon_id == target_id)
                                  .order_by(LexiconEntry.sequence)).all()
        assert len(entries) == 5528
        assert [(item.sequence, item.word) for item in entries] == [
            (index, row["word"]) for index, row in enumerate(candidate, 1)]
        by_word = {entry.word.casefold(): entry for entry in entries}
        for phrase in ("according to", "air conditioning", "ice cream", "living room",
                       "ought to", "owing to"):
            assert phrase in by_word
        assert sum(not entry.source_meanings for entry in entries) == 59
        assert by_word["owing to"].source_meanings == []
        assert by_word["ice cream"].source_meanings
        entry_ids = [entry.id for entry in entries]
        assert session.scalar(select(func.count()).select_from(EntrySourceEvidence).where(
            EntrySourceEvidence.lexicon_entry_id.in_(entry_ids),
            EntrySourceEvidence.field_kind == "meaning",
            EntrySourceEvidence.selected_for_default.is_(True),
        )) == 5469
        artifacts = {artifact.publisher: artifact for artifact in session.scalars(
            select(SourceArtifact).where(SourceArtifact.id.in_(
                select(EntrySourceEvidence.source_artifact_id).where(
                    EntrySourceEvidence.lexicon_entry_id.in_(entry_ids))
            ))).all()}
        assert len(artifacts) == 3
        assert "WikDict / Wiktionary via DBnary" in artifacts
        assert "中文维基词典 contributors" in artifacts
        selected_by_entry = {evidence.lexicon_entry_id: evidence for evidence in session.scalars(
            select(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id.in_(entry_ids),
                EntrySourceEvidence.field_kind == "meaning",
                EntrySourceEvidence.selected_for_default.is_(True),
            )).all()}
        artifacts_by_id = {artifact.id: artifact for artifact in artifacts.values()}
        for row in candidate:
            entry = by_word[row["word"].casefold()]
            assert entry.source_meanings == ([row["meaning"]] if row["meaning"] else [])
            evidence = selected_by_entry.get(entry.id)
            if row["meaning_source"] == "missing":
                assert evidence is None
                continue
            assert evidence is not None
            artifact = artifacts_by_id[evidence.source_artifact_id]
            assert evidence.raw_text == row["meaning"]
            assert evidence.row_locator >= 2
            if row["meaning_source"] == "wikdict":
                assert artifact.publisher.startswith("WikDict")
                assert evidence.sense_key.startswith(row["source_locator"] + ":offset:")
            else:
                assert artifact.publisher.startswith("中文维基词典")
                assert evidence.source_revision == row["source_locator"].split("#", 1)[0].removeprefix("oldid:")
                if "#line:" in row["source_locator"]:
                    assert evidence.sense_key == "wikitext:line:" + row["source_locator"].split("#line:", 1)[1]
                else:
                    assert evidence.sense_key.startswith("cache:def:")

    recommended = learner.client.get("/api/lexicons/selection")
    assert recommended.json() == {"lexicon_id": target_id, "source": "recommended"}
    assert learner.client.post(f"/api/lexicons/{target_id}/select").status_code == 200
    queue = learner.client.get("/api/study/today", params={"limit": 5}).json()
    assert queue["selection_source"] == "explicit"
    assert queue["words"][0]["meaning_origin"] == "platform"
    assert queue["words"][0]["source_meaning_sources"][0]["publisher"].startswith("WikDict")
    assert queue["words"][0]["concise_meanings"] == []
    state = queue["words"][0]["word_state_id"]
    review = learner.client.post(f"/api/study/word-states/{state}/review", json={
        "result": "know", "source": "daily", "review_type": "recall"})
    assert review.status_code == 200, review.text
    assert learner.client.get("/api/lexicons/selection").json() == {
        "lexicon_id": target_id, "source": "explicit"}

    public_word = queue["words"][0]["word"]
    for filename, content in (
        ("own.txt", f"{public_word}\n"),
        ("own.csv", f"word,meaning\n{public_word},个人释义\n"),
    ):
        raw = content.encode("utf-8")
        preview = learner.client.post("/api/lexicons/file-preview", files={
            "file": (filename, BytesIO(raw), "text/plain"),
        })
        assert preview.status_code == 200, preview.text
        imported = learner.client.post("/api/lexicons/file-import", data={
            "name": filename, "preview_sha256": preview.json()["sha256"],
        }, files={"file": (filename, BytesIO(raw), "text/plain")})
        assert imported.status_code == 201, imported.text
        private_id = imported.json()["id"]
        assert learner.client.post(f"/api/lexicons/{private_id}/select").status_code == 200
        private_queue = learner.client.get("/api/study/today", params={"limit": 5}).json()
        private_word = private_queue["words"][0]
        assert private_queue["selection_source"] == "explicit"
        assert private_word["word"] == public_word
        assert private_word["word_state_id"] != state
        assert private_word["meaning_origin"] == "user_provided"
        assert "source_meaning_sources" not in private_word
        assert learner.client.get("/api/lexicons/selection").json() == {
            "lexicon_id": private_id, "source": "explicit"}
    assert learner.client.post(f"/api/lexicons/{target_id}/select").status_code == 200
    assert learner.client.get("/api/lexicons/selection").json() == {
        "lexicon_id": target_id, "source": "explicit"}
    restored = learner.client.get(f"/api/words/state/{state}")
    assert restored.status_code == 200
    assert restored.json()["status"] != "new"
    learner.client.__exit__(None, None, None)
    admin.client.__exit__(None, None, None)
