"""Accept the actual frozen package, including removed-text and private-library boundaries."""

import csv
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import EntrySourceEvidence, Lexicon, LexiconEntry, User
from app.services.entry_provenance import entry_sources
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "test-artifacts/netem-final-20261005/frozen"
EMPTY = {"shortage", "temporary", "thoughtful", "tribute", "tonight", "ashore", "set",
         "complex", "bit", "honor", "inhabit", "pitch", "progressive", "build", "collect",
         "locate", "outward", "operation"}


@pytest.mark.skipif(not (FROZEN / "fingerprints.json").exists(), reason="local final package unavailable")
def test_final_package_order_empty_evidence_attribution_and_private_regression(
    make_world, created_lexicon_cleanup,
):
    package = FROZEN / "public-package"
    plan = build_plan(manifest_path=Path("manifest.json"), source_root=package,
                      decisions_path=Path("decisions.json"), target_lexicon="NETEM final test")
    assert plan["confirmation_ready"], plan["confirmation_blockers"]
    admin = make_world("netem-final-admin", role="admin")
    learner = make_world("netem-final-learner")
    other = make_world("netem-final-other")
    with admin.session() as session:
        session.add(Lexicon(name="NETEM final test", visibility="public", source_type="netem"))
        session.commit()
        result = confirm_plan(session, plan=plan, administrator=session.get(User, admin.user_id),
                              source_root=package)
        assert (result["entries_created"], result["evidence_written"]) == (5528, 16430)
        lexicon_id = result["target_lexicon"]["id"]
        entries = session.scalars(select(LexiconEntry).where(LexiconEntry.lexicon_id == lexicon_id)
                                  .order_by(LexiconEntry.sequence)).all()
        baseline = list(csv.DictReader((ROOT / "test-artifacts/default-lexicon-candidate-v2/candidate-provenance.csv").open(encoding="utf-8")))
        final = list(csv.DictReader((FROZEN / "candidate-provenance.csv").open(encoding="utf-8")))
        assert [(e.sequence, e.word) for e in entries] == [(int(r["sequence"]), r["word"]) for r in baseline]
        assert sum(bool(e.source_meanings) for e in entries) == 5451
        assert sum(not e.source_meanings for e in entries) == 77
        for entry, row in zip(entries, final, strict=True):
            assert entry.source_meanings == ([row["meaning"]] if row["meaning"] else [])
            if entry.word in EMPTY or entry.word == "owing to":
                assert entry.source_meanings == []
                assert session.scalar(select(func.count()).select_from(EntrySourceEvidence).where(
                    EntrySourceEvidence.lexicon_entry_id == entry.id,
                    EntrySourceEvidence.field_kind == "meaning")) == 0
                assert all(x["field_kind"] == "word" for g in entry_sources(session, entry.id)["fields"] for x in g["selected"])
            if entry.word == "the":
                sources = entry_sources(session, entry.id)
                assert sources["completeness"]["status"] == "complete"
                meaning = next(g for g in sources["fields"] if g["field_kind"] == "meaning")
                assert meaning["selected"][0]["source"]["attribution"]["snapshot_sha256"] == "62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830"
            if entry.word == "modern":
                meaning = next(g for g in entry_sources(session, entry.id)["fields"] if g["field_kind"] == "meaning")
                assert meaning["selected"][0]["source_history_url"].endswith("oldid=9893726&action=history")
    assert learner.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    queue = learner.client.get("/api/study/today", params={"limit": 3}).json()
    public = queue["words"][0]
    assert public["source_meaning_sources"][0]["license_id"] == "CC-BY-SA-4.0"
    assert "Karl Bartel" in public["source_meaning_sources"][0]["attribution"]["creators"]
    assert learner.client.post(f"/api/study/word-states/{public['word_state_id']}/review", json={
        "result": "know", "source": "daily", "review_type": "recall"}).status_code == 200
    for filename, content in (("final.txt", f"{public['word']}\n"),
                              ("final.csv", f"word,meaning\n{public['word']},个人释义\n")):
        files = {"file": (filename, content.encode(), "text/plain")}
        preview = learner.client.post("/api/lexicons/file-preview", files=files)
        imported = learner.client.post("/api/lexicons/file-import", files=files, data={
            "name": filename, "preview_sha256": preview.json()["sha256"]})
        assert imported.status_code == 201
        private_id = imported.json()["id"]
        assert other.client.get(f"/api/lexicons/{private_id}").status_code == 404
        assert learner.client.post(f"/api/lexicons/{private_id}/select").status_code == 200
        private = learner.client.get("/api/study/today").json()["words"][0]
        assert private["meaning_origin"] == "user_provided"
        assert private["word_state_id"] != public["word_state_id"]
        assert "source_meaning_sources" not in private
        assert learner.client.get("/api/lexicons/selection").json()["lexicon_id"] == private_id
    assert learner.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    assert learner.client.get(f"/api/words/state/{public['word_state_id']}").json()["status"] != "new"
    for user in (admin, learner, other):
        user.client.__exit__(None, None, None)
