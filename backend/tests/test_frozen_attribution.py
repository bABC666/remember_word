"""A frozen source statement survives import without reading the source at display time."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Lexicon, LexiconEntry, User
from app.services.entry_provenance import entry_sources
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan
from tests.test_public_lexicon_plan import provenance_block


def package(root: Path, url: str = "https://example.org/package.zip") -> None:
    (root / "words.csv").write_text("word,meaning\nalpha,测试义\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "required_fields": [], "sources": [{
            "id": "p", "role": "primary", "file": "words.csv",
            "columns": {"word": "word", "meaning": "meaning"},
            "provenance": provenance_block("p"),
            "attribution": {
                "creators": "Publisher and contributors", "modifications": "Extracted fragments",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "snapshot_url": url, "snapshot_sha256": "a" * 64,
                "snapshot_label": "2026-06-23 ZIP", "links": [],
            },
        }],
    }), encoding="utf-8")
    (root / "decisions.json").write_text('{"format_version":1,"decisions":[]}', encoding="utf-8")


def plan(root: Path):
    return build_plan(manifest_path=Path("manifest.json"), source_root=root,
                      decisions_path=Path("decisions.json"), target_lexicon="frozen attribution")


def test_package_attribution_survives_confirmation_and_counts_as_fixed_source(
    make_world, created_lexicon_cleanup, tmp_path,
):
    package(tmp_path)
    locked = plan(tmp_path)
    assert locked["sources"][0]["mapping"]["attribution"]["snapshot_sha256"] == "a" * 64
    admin = make_world("frozen-attribution-admin", role="admin")
    with admin.session() as session:
        session.add(Lexicon(name="frozen attribution", visibility="public", source_type="attribution"))
        session.commit()
        result = confirm_plan(session, plan=locked, administrator=session.get(User, admin.user_id),
                              source_root=tmp_path)
        entry = session.scalar(select(LexiconEntry).where(
            LexiconEntry.lexicon_id == result["target_lexicon"]["id"]))
        (tmp_path / "words.csv").unlink()
        value = entry_sources(session, entry.id)
        assert value["completeness"]["status"] == "complete"
        source = value["fields"][0]["selected"][0]["source"]
        assert source["attribution"]["modifications"] == "Extracted fragments"
        assert source["attribution"]["snapshot_url"] == "https://example.org/package.zip"
    api_value = admin.client.get(f"/api/lexicons/{result['target_lexicon']['id']}/sources").json()
    assert api_value["sources"][0]["attribution"]["creators"] == "Publisher and contributors"
    admin.client.__exit__(None, None, None)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private.zip", "https://", "https://example.org/a b"])
def test_rejects_unsafe_public_attribution_links(tmp_path, url):
    package(tmp_path, url)
    with pytest.raises(ValueError, match="attribution"):
        plan(tmp_path)
