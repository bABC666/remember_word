"""Synthetic contracts for the locked public-lexicon adjudication plan.

Everything here uses temporary directories and synthetic CSV: the plan is
source-independent, and no test needs real material, a production database or a
licensed dictionary. The plan is read-only, so the assertions also pin down that the
CLI opens no application database and creates no learning state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.public_lexicon_joint_preview import PROVENANCE_REQUIRED_FIELDS
from app.services.public_lexicon_plan import (
    PlanError,
    build_plan,
    canonical_bytes,
    evidence_idempotency_key,
    load_plan,
    plan_digest,
    write_plan,
)

PRIMARY = "head,cn\nApple,苹果；果实\nBare,\n"
SUPPLEMENT = "term,translation,ipa,pos\n apple ,苹果公司,/ˈæpəl/,noun\norphan,孤儿,/ˈɔːfən/,noun\n"


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def provenance_block(source_id: str = "source") -> dict[str, str]:
    """A complete declared provenance block for a synthetic source.

    Real declarations are the operator's problem; what these tests pin is that a
    plan without one is blocked, so every manifest here carries a valid block unless
    the test is specifically about provenance.
    """
    return {
        "publisher": "synthetic publisher",
        "version": "2026-09-25",
        "obtained_at_utc": "2026-09-25T00:00:00Z",
        "license_id": "synthetic-test-only",
        "use_scope": "local-evaluation",
        "display_scope": "not-for-publication",
        "storage_locator": f"sources/{source_id}.csv",
    }


def _manifest(root: Path, sources: list[dict[str, Any]], **extra: Any) -> Path:
    document: dict[str, Any] = {
        "required_fields": ["meaning"],
        "sources": [
            source if "provenance" in source
            else {**source, "provenance": provenance_block(source.get("id", "source"))}
            for source in sources
        ],
    }
    document.update(extra)
    path = root / "manifest.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _decisions(root: Path, decisions: list[dict[str, Any]]) -> Path:
    path = root / "decisions.json"
    path.write_text(
        json.dumps({"format_version": 1, "decisions": decisions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _two_sources(root: Path) -> Path:
    _write(root, "primary.csv", PRIMARY)
    _write(root, "supplement.csv", SUPPLEMENT)
    return _manifest(root, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "supplement", "role": "meaning", "file": "supplement.csv",
         "columns": {"word": "term", "meaning": "translation", "phonetic": "ipa",
                     "part_of_speech": "pos"}},
    ])


def _plan(
    root: Path,
    manifest: Path,
    decisions: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return build_plan(
        manifest_path=manifest,
        source_root=root,
        decisions_path=_decisions(root, decisions or []),
        target_lexicon="kaoyan-public",
        **kwargs,
    )


def _entry(plan: dict[str, Any], word: str) -> dict[str, Any]:
    return next(item for item in plan["entries"] if item["normalized_word"] == word)


SELECT_PRIMARY_MEANING = {
    "normalized_word": "apple", "field": "meaning", "action": "select",
    "evidence": [{"source_id": "primary", "line": 2}], "note": "以主词表释义为默认",
}


def test_plan_takes_membership_and_order_from_the_primary_source_only(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)

    plan = _plan(tmp_path, manifest, [
        SELECT_PRIMARY_MEANING,
        {"normalized_word": "bare", "action": "exclude_word", "note": "全部来源缺释义"},
    ])

    assert [entry["normalized_word"] for entry in plan["entries"]] == ["apple", "bare"]
    assert [entry["sequence"] for entry in plan["entries"]] == [1, 2]
    # The supplement's own word never becomes a lexicon entry.
    assert plan["unmatched_supplement_words"] == ["orphan"]
    assert plan["summary"]["candidate_entries"] == 2
    assert not any(
        entry["normalized_word"] == "orphan" for entry in plan["entries"]
    )


def test_plan_blocks_a_conflicting_meaning_until_a_human_selects_one(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)

    undecided = _plan(tmp_path, manifest, [
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])
    apple = _entry(undecided, "apple")
    assert apple["status"] == "blocked"
    assert apple["block_reasons"] == ["undecided_conflict:meaning"]
    assert apple["default_snapshot"]["source_meanings"] == []
    assert undecided["confirmation_ready"] is False
    assert any("blocked_entries" in item for item in undecided["confirmation_blockers"])

    decided = _plan(tmp_path, manifest, [
        SELECT_PRIMARY_MEANING,
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])
    chosen = _entry(decided, "apple")
    assert chosen["status"] == "ready"
    assert chosen["default_snapshot"]["source_meanings"] == ["苹果；果实"]
    assert chosen["default_evidence"]["meaning"] == [
        {"source_id": "primary", "line": 2}
    ]
    assert decided["confirmation_ready"] is True


def test_plan_keeps_every_source_meaning_verbatim_and_grouped(tmp_path: Path) -> None:
    manifest = _two_sources(tmp_path)

    entry = _entry(_plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING]), "apple")

    assert entry["conflicts"][0]["raw_values"] == ["苹果；果实", "苹果公司"]
    assert [item["raw_value"] for item in entry["evidence"]["meaning"]] == [
        "苹果；果实", "苹果公司",
    ]
    assert [item["source_id"] for item in entry["evidence"]["meaning"]] == [
        "primary", "supplement",
    ]
    # Nothing was assembled from two sources and no unselected value survived as a
    # default; the two texts stay two pieces of evidence.
    assert entry["default_snapshot"]["source_meanings"] == ["苹果；果实"]
    assert all(
        "苹果公司" not in value for value in entry["default_snapshot"]["source_meanings"]
    )
    assert entry["default_snapshot"]["source_raw"] == "Apple,苹果；果实"


def test_plan_cites_every_agreeing_source_instead_of_picking_one(tmp_path: Path) -> None:
    _write(tmp_path, "a.csv", "head,cn\nWord,词\n")
    _write(tmp_path, "b.csv", "head,cn\nword,词\n")
    manifest = _manifest(tmp_path, [
        {"id": "a", "role": "primary", "file": "a.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "b", "role": "meaning", "file": "b.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])

    entry = _entry(_plan(tmp_path, manifest), "word")

    assert entry["status"] == "ready"
    assert entry["default_snapshot"]["source_meanings"] == ["词"]
    assert entry["default_evidence"]["meaning"] == [
        {"source_id": "a", "line": 2}, {"source_id": "b", "line": 2},
    ]
    assert len(entry["evidence"]["meaning"]) == 2


def test_plan_digest_is_reproducible_and_independent_of_source_order(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)
    decisions = [SELECT_PRIMARY_MEANING]

    first = _plan(tmp_path, manifest, decisions)

    reordered = _manifest(tmp_path, list(reversed(json.loads(
        manifest.read_text(encoding="utf-8")
    )["sources"])))
    second = _plan(tmp_path, reordered, decisions)
    third = _plan(tmp_path, manifest, decisions)

    assert first["plan_sha256"] == second["plan_sha256"] == third["plan_sha256"]
    assert first["entries"] == second["entries"]
    assert first["run_id"] != second["run_id"]
    # The manifest's own bytes did change when the list was re-ordered, and that is
    # recorded -- but it is not what decides the write set, so the digest holds.
    assert first["manifest_file"]["sha256"] != second["manifest_file"]["sha256"]
    assert plan_digest(first) == first["plan_sha256"]


def test_plan_freezes_input_fingerprints_and_the_joint_report_hash(
    tmp_path: Path,
) -> None:
    from app.services.public_lexicon_joint_preview import preview_manifest

    manifest = _two_sources(tmp_path)
    plan = _plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING])
    report = preview_manifest(manifest, source_root=tmp_path)

    assert plan["joint_report_sha256"] == report["report_sha256"]
    assert plan["manifest_file"]["sha256"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    primary = next(item for item in plan["sources"] if item["source_id"] == "primary")
    supplement = next(
        item for item in plan["sources"] if item["source_id"] == "supplement"
    )
    assert primary["file"]["sha256"] == hashlib.sha256(
        (tmp_path / "primary.csv").read_bytes()
    ).hexdigest()
    assert primary["role"] == "primary"
    assert supplement["role"] == "meaning"
    # One hash per distinct mapping: the two sources map different columns.
    assert len(primary["mapping_sha256"]) == 64
    assert primary["mapping_sha256"] != supplement["mapping_sha256"]
    assert plan["rule_versions"]["normalization"] == "strip_casefold_v1"
    assert plan["rule_versions"]["membership"] == "primary_source_first_occurrence_v1"
    assert plan["target"] == {
        "lexicon": "kaoyan-public", "verified_against_database": False,
    }


def test_plan_evidence_keys_ignore_the_local_source_label(tmp_path: Path) -> None:
    manifest = _two_sources(tmp_path)
    decisions = [SELECT_PRIMARY_MEANING]
    before = _plan(tmp_path, manifest, decisions)

    renamed = _manifest(tmp_path, [
        {**source, "id": {"primary": "primary-renamed"}.get(source["id"], source["id"])}
        for source in json.loads(manifest.read_text(encoding="utf-8"))["sources"]
    ])
    after = _plan(tmp_path, renamed, [
        {**SELECT_PRIMARY_MEANING,
         "evidence": [{"source_id": "primary-renamed", "line": 2}]},
    ])

    keys_before = [
        item["idempotency_key"]
        for item in _entry(before, "apple")["evidence"]["meaning"]
    ]
    keys_after = [
        item["idempotency_key"]
        for item in _entry(after, "apple")["evidence"]["meaning"]
    ]
    # Same bytes, same mapping: the same evidence, whatever the manifest calls it.
    assert keys_before == keys_after
    frozen = {source["source_id"]: source for source in before["sources"]}
    assert keys_before == [
        evidence_idempotency_key(
            file_sha256=frozen["primary"]["file"]["sha256"],
            mapping_sha256=frozen["primary"]["mapping_sha256"],
            line=2, field="meaning", raw_value="苹果；果实",
        ),
        evidence_idempotency_key(
            file_sha256=frozen["supplement"]["file"]["sha256"],
            mapping_sha256=frozen["supplement"]["mapping_sha256"],
            line=2, field="meaning", raw_value="苹果公司",
        ),
    ]
    # The source-level key does include the label, and the plan digest moves with it.
    assert before["plan_sha256"] != after["plan_sha256"]
    assert before["idempotency"]["evidence"]["key_fields"] == [
        "file.sha256", "mapping_sha256", "line", "field", "raw_value",
    ]


def test_plan_requires_one_primary_and_refuses_a_corpus_source(tmp_path: Path) -> None:
    _write(tmp_path, "a.csv", "head,cn\nWord,词\n")

    no_role = _manifest(tmp_path, [
        {"id": "a", "file": "a.csv", "columns": {"word": "head", "meaning": "cn"}},
    ])
    with pytest.raises(ValueError, match="missing_role"):
        _plan(tmp_path, no_role)

    two_primaries = _manifest(tmp_path, [
        {"id": "a", "role": "primary", "file": "a.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "b", "role": "primary", "file": "a.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])
    with pytest.raises(ValueError, match="primary_source_count"):
        _plan(tmp_path, two_primaries)

    corpus = _manifest(tmp_path, [
        {"id": "a", "role": "primary", "file": "a.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "papers", "role": "exam_corpus", "file": "a.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])
    with pytest.raises(ValueError, match="exam_corpus_not_supported"):
        _plan(tmp_path, corpus)


@pytest.mark.parametrize(("decision", "pattern"), [
    ({"normalized_word": "ghost", "field": "meaning", "action": "select",
      "evidence": [{"source_id": "primary", "line": 2}]},
     r"not a word of the primary source"),
    ({"normalized_word": "apple", "field": "meaning", "action": "select",
      "evidence": [{"source_id": "primary", "line": 99}]},
     r"no non-empty evidence"),
    ({"normalized_word": "apple", "field": "phonetic", "action": "select",
      "evidence": [{"source_id": "supplement", "line": 2},
                   {"source_id": "supplement", "line": 3}]},
     r"takes exactly one evidence entry"),
    ({"normalized_word": "apple", "field": "word", "action": "no_default"},
     r"always has the primary spelling"),
    ({"normalized_word": "bare", "action": "exclude_word"},
     r"needs a note"),
    ({"normalized_word": "apple", "field": "meaning", "action": "select",
      "evidence": []},
     r"needs a nonempty evidence list"),
    ({"normalized_word": "apple", "field": "meaning", "action": "explode"},
     r"unknown action"),
    ({"normalized_word": "apple", "field": "frequency", "action": "defer"},
     r"unknown field"),
])
def test_plan_rejects_stale_or_malformed_decisions(
    tmp_path: Path, decision: dict[str, Any], pattern: str
) -> None:
    manifest = _two_sources(tmp_path)

    with pytest.raises(ValueError, match=pattern):
        _plan(tmp_path, manifest, [decision])


def test_plan_rejects_a_word_that_is_both_excluded_and_adjudicated(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)

    with pytest.raises(ValueError, match="both excluded and adjudicated"):
        _plan(tmp_path, manifest, [
            {"normalized_word": "apple", "action": "exclude_word", "note": "排除"},
            SELECT_PRIMARY_MEANING,
        ])


def test_plan_reports_every_decision_error_at_once(tmp_path: Path) -> None:
    manifest = _two_sources(tmp_path)

    with pytest.raises(ValueError) as failure:
        _plan(tmp_path, manifest, [
            {"normalized_word": "ghost", "action": "exclude_word", "note": "a"},
            {"normalized_word": "phantom", "action": "exclude_word", "note": "b"},
        ])

    message = str(failure.value)
    assert "ghost" in message and "phantom" in message


def test_plan_blocks_unreadable_rows_until_they_are_acknowledged(tmp_path: Path) -> None:
    _write(tmp_path, "primary.csv", "head,cn\nApple,苹果；果实\ntoo,many,cells\n")
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])

    blocked = _plan(tmp_path, manifest)
    assert blocked["sources"][0]["unreadable_rows"] == [3]
    assert blocked["summary"]["unreadable_rows"] == 1
    assert any(
        "unacknowledged_bad_rows:primary" in item
        for item in blocked["confirmation_blockers"]
    )
    assert blocked["confirmation_ready"] is False

    acknowledged = _plan(tmp_path, manifest, [
        {"action": "exclude_row", "source_id": "primary", "line": 3,
         "note": "该行多一列，无法判定字段，明确排除"},
    ])
    assert acknowledged["confirmation_ready"] is True
    assert acknowledged["entries"][0]["status"] == "ready"


def test_plan_refuses_a_row_acknowledgement_for_a_row_that_is_fine(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)

    with pytest.raises(ValueError, match="is not an unreadable row"):
        _plan(tmp_path, manifest, [
            {"action": "exclude_row", "source_id": "primary", "line": 2, "note": "无理由"},
        ])


def test_plan_reports_two_sources_reading_the_same_bytes_through_one_mapping(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "primary.csv", "head,cn\nApple,苹果；果实\n")
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "copy", "role": "meaning", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])

    plan = _plan(tmp_path, manifest)

    assert any(
        item.startswith("duplicate_source_fingerprint") for item in plan["confirmation_blockers"]
    )
    assert plan["confirmation_ready"] is False


def test_plan_makes_defer_and_no_default_visible_instead_of_silent(
    tmp_path: Path,
) -> None:
    manifest = _two_sources(tmp_path)

    deferred = _plan(tmp_path, manifest, [
        {"normalized_word": "apple", "field": "meaning", "action": "defer",
         "note": "等负责人确认主词表口径"},
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])
    assert _entry(deferred, "apple")["block_reasons"] == ["deferred_decision:meaning"]
    assert _entry(deferred, "apple")["decisions"][0]["action"] == "defer"

    declined = _plan(tmp_path, manifest, [
        {"normalized_word": "apple", "field": "meaning", "action": "no_default",
         "note": "两来源释义冲突，暂不定默认"},
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])
    entry = _entry(declined, "apple")
    assert entry["block_reasons"] == ["no_default_on_required_field:meaning"]
    assert entry["default_snapshot"]["source_meanings"] == []
    assert len(entry["evidence"]["meaning"]) == 2
    assert declined["confirmation_ready"] is False


def test_plan_records_the_missing_required_field_of_a_single_source(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "primary.csv", "head,cn\nSolo,\n")
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])

    plan = _plan(tmp_path, manifest)

    assert _entry(plan, "solo")["block_reasons"] == ["missing_required_field:meaning"]
    assert plan["summary"]["blocked_entries"] == 1


def test_plan_write_is_once_only_and_tampering_is_detected(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _two_sources(tmp_path), [SELECT_PRIMARY_MEANING])
    target = tmp_path / "plan.json"
    write_plan(plan, target)

    assert load_plan(target)["plan_sha256"] == plan["plan_sha256"]
    with pytest.raises(FileExistsError):
        write_plan(plan, target)

    tampered = json.loads(target.read_text(encoding="utf-8"))
    tampered["entries"][0]["default_snapshot"]["source_meanings"] = ["改过的释义"]
    target.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PlanError, match="digest does not match"):
        load_plan(target)


def test_plan_digest_covers_the_decision_bearing_fields(tmp_path: Path) -> None:
    manifest = _two_sources(tmp_path)
    plan = _plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING])

    for field in ("entries", "sources", "confirmation_ready", "joint_report_sha256",
                  "required_fields", "target"):
        mutated = json.loads(json.dumps(plan))
        assert field in mutated
        mutated[field] = "tampered"
        assert plan_digest(mutated) != plan["plan_sha256"], field
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_plan_cli_is_read_only_confined_and_refuses_to_overwrite(
    world, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli
    from app.models import LexiconEntry, ReviewEvent, UserWordState

    root = tmp_path / "sources"
    root.mkdir()
    _write(root, "primary.csv", PRIMARY)
    _write(root, "supplement.csv", SUPPLEMENT)
    manifest = _manifest(root, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "supplement", "role": "meaning", "file": "supplement.csv",
         "columns": {"word": "term", "meaning": "translation", "phonetic": "ipa",
                     "part_of_speech": "pos"}},
    ])
    decisions = _decisions(root, [
        SELECT_PRIMARY_MEANING,
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])
    plan_path = tmp_path / "plan.json"
    before = (manifest.read_bytes(), decisions.read_bytes(),
              (root / "primary.csv").read_bytes(), (root / "supplement.csv").read_bytes())
    with world.session() as session:
        before_rows = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )

    def unexpected_database_call(*_args, **_kwargs):
        raise AssertionError("the locked plan must not initialize a database")

    monkeypatch.setattr(cli, "get_settings", unexpected_database_call)
    monkeypatch.setattr(cli, "verify_schema_revision", unexpected_database_call)

    assert cli.main([
        "public-lexicon", "plan", str(manifest), "--source-root", str(root),
        "--decisions", str(decisions), "--plan", str(plan_path),
        "--target-lexicon", "kaoyan-public",
    ]) == 0
    printed = capsys.readouterr().out
    assert "confirmation_ready: True" in printed
    written = load_plan(plan_path)
    assert written["target"] == {
        "lexicon": "kaoyan-public", "verified_against_database": False,
    }
    expected_sha = written["plan_sha256"]

    # Every input is untouched, and no learning state appeared.
    assert (manifest.read_bytes(), decisions.read_bytes(),
            (root / "primary.csv").read_bytes(),
            (root / "supplement.csv").read_bytes()) == before
    with world.session() as session:
        after_rows = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )
    assert after_rows == before_rows

    assert cli.main([
        "public-lexicon", "plan", str(manifest), "--source-root", str(root),
        "--decisions", str(decisions), "--plan", str(plan_path),
        "--target-lexicon", "kaoyan-public",
    ]) == 2
    # The previous plan is evidence and stays exactly as it was written.
    assert "生成失败" in capsys.readouterr().err
    assert load_plan(plan_path)["plan_sha256"] == expected_sha

    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"format_version": 1, "decisions": []}), encoding="utf-8"
    )
    assert cli.main([
        "public-lexicon", "plan", str(manifest), "--source-root", str(root),
        "--decisions", str(outside), "--plan", str(tmp_path / "second.json"),
        "--target-lexicon", "kaoyan-public",
    ]) == 2
    assert "outside_source_root" in capsys.readouterr().err


def test_plan_cli_require_ready_fails_closed_on_an_unadjudicated_plan(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    _write(root, "primary.csv", PRIMARY)
    _write(root, "supplement.csv", SUPPLEMENT)
    manifest = _manifest(root, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
        {"id": "supplement", "role": "meaning", "file": "supplement.csv",
         "columns": {"word": "term", "meaning": "translation"}},
    ])
    decisions = _decisions(root, [])

    def unexpected_database_call(*_args, **_kwargs):
        raise AssertionError("the locked plan must not initialize a database")

    monkeypatch.setattr(cli, "get_settings", unexpected_database_call)
    monkeypatch.setattr(cli, "verify_schema_revision", unexpected_database_call)

    assert cli.main([
        "public-lexicon", "plan", str(manifest), "--source-root", str(root),
        "--decisions", str(decisions), "--plan", str(tmp_path / "plan.json"),
        "--target-lexicon", "kaoyan-public", "--require-ready",
    ]) == 1
    printed = capsys.readouterr().out
    assert "confirmation_ready: False" in printed
    assert (tmp_path / "plan.json").exists()


# --- the entry-identity rule, pinned against a tempting alternative -----------
#
# The Phase 2.9 source research compares candidate word lists with its own
# normalisation: "只保留字母数字并小写" (keep only alphanumerics, lowercase) --
# §7.1 of `docs/V1.2-PHASE2.9-EXTERNAL-LEXICON-SOURCE-RESEARCH.md`. That rule exists
# to measure overlap between two research data sets; it is not this product's entry
# identity, and adopting it would silently merge genuine headwords (`well-known` and
# `wellknown`, `can't` and `cant`) inside the shared public lexicon.
#
# The contract is `strip().casefold()`: leading/trailing whitespace and case fold,
# and nothing else. These tests make the difference observable, so a future change
# cannot adopt the research rule without failing here.


def test_plan_keeps_the_strip_casefold_identity_rule(tmp_path: Path) -> None:
    _write(tmp_path, "primary.csv", (
        "head,cn\n"
        "Apple,苹果\n"
        " apple ,苹果\n"
        "well-known,知名的\n"
        "wellknown,众所周知的\n"
        "can't,不能\n"
        "cant,伪善的言辞\n"
    ))
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}},
    ])

    plan = _plan(tmp_path, manifest)

    assert plan["rule_versions"]["normalization"] == "strip_casefold_v1"
    # Case and surrounding whitespace fold: the two `apple` rows are one entry, and
    # both rows stay as evidence.
    assert [entry["normalized_word"] for entry in plan["entries"]] == [
        "apple", "well-known", "wellknown", "can't", "cant",
    ]
    assert len(_entry(plan, "apple")["evidence"]["meaning"]) == 2
    # Punctuation inside a word is never stripped. The research matrix would collapse
    # each of these pairs into one key.
    assert len(plan["entries"]) == 5


def test_plan_source_raw_stays_the_primary_line_even_when_a_supplement_is_default(
    tmp_path: Path,
) -> None:
    """`source_raw` is the primary row's own text, never an assembled one.

    Selecting a supplement's meaning as the displayed default must not pull that
    supplement's text into `source_raw`, and must not merge the two originals: the
    snapshot cites where each value came from, and both raw texts survive in evidence.
    """
    manifest = _two_sources(tmp_path)

    entry = _entry(_plan(tmp_path, manifest, [{
        "normalized_word": "apple", "field": "meaning", "action": "select",
        "evidence": [{"source_id": "supplement", "line": 2}],
        "note": "以补充来源释义为默认",
    }]), "apple")

    assert entry["status"] == "ready"
    assert entry["default_snapshot"]["source_meanings"] == ["苹果公司"]
    assert entry["default_snapshot"]["source_raw"] == "Apple,苹果；果实"
    assert entry["default_evidence"]["source_raw"] == {
        "source_id": "primary", "line": 2,
    }
    assert [item["raw_value"] for item in entry["evidence"]["meaning"]] == [
        "苹果；果实", "苹果公司",
    ]
    # No value anywhere in the entry mixes the two source texts.
    assert all(
        item["raw_value"] in {"苹果；果实", "苹果公司", "Apple", " apple "}
        for field in ("word", "meaning")
        for item in entry["evidence"][field]
    )


# --- declared provenance: an import nobody can audit must not be confirmable ---
#
# The design's rule is that a source whose content licence is unsettled may be
# evaluated locally but must not be published. These tests pin the "recorded, not
# verified" contract from both sides: the plan freezes whatever was declared and
# blocks when something required is missing, so the failure is visible in the
# artifact a human reviews rather than discovered months later on a blank column.


def test_plan_blocks_a_source_that_declares_no_provenance(tmp_path: Path) -> None:
    _write(tmp_path, "primary.csv", PRIMARY)
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}, "provenance": None},
    ])

    plan = _plan(tmp_path, manifest, [])

    assert plan["sources"][0]["provenance"]["license_id"] == ""
    assert plan["sources"][0]["missing_provenance"] == sorted(
        PROVENANCE_REQUIRED_FIELDS
    )
    assert plan["confirmation_ready"] is False
    assert any(
        item.startswith("incomplete_provenance:primary")
        for item in plan["confirmation_blockers"]
    )
    # The blocker names what is owed, so an operator does not have to diff it out.
    blocker = next(
        item for item in plan["confirmation_blockers"]
        if item.startswith("incomplete_provenance:primary")
    )
    assert "license_id" in blocker and "publisher" in blocker


def test_plan_blocks_a_partially_declared_source(tmp_path: Path) -> None:
    _write(tmp_path, "primary.csv", PRIMARY)
    partial = provenance_block("primary")
    partial["license_id"] = "   "
    partial["display_scope"] = ""
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}, "provenance": partial},
    ])

    plan = _plan(tmp_path, manifest, [])

    assert plan["sources"][0]["missing_provenance"] == ["display_scope", "license_id"]
    assert plan["confirmation_ready"] is False
    # Whitespace is not a declaration.
    assert plan["sources"][0]["provenance"]["license_id"] == ""


def test_plan_confirms_once_every_source_is_declared(tmp_path: Path) -> None:
    manifest = _two_sources(tmp_path)

    plan = _plan(tmp_path, manifest, [
        SELECT_PRIMARY_MEANING,
        {"normalized_word": "bare", "action": "exclude_word", "note": "缺释义"},
    ])

    assert all(source["missing_provenance"] == [] for source in plan["sources"])
    assert plan["confirmation_ready"] is True
    primary = next(item for item in plan["sources"] if item["source_id"] == "primary")
    assert primary["provenance"]["license_id"] == "synthetic-test-only"
    assert primary["provenance"]["storage_locator"] == "sources/primary.csv"


def test_plan_digest_covers_the_declared_provenance(tmp_path: Path) -> None:
    """Changing a licence declaration must invalidate the plan that recorded it."""
    _write(tmp_path, "primary.csv", PRIMARY)
    base = provenance_block("primary")

    def build(license_id: str) -> dict[str, Any]:
        manifest = _manifest(tmp_path, [
            {"id": "primary", "role": "primary", "file": "primary.csv",
             "columns": {"word": "head", "meaning": "cn"},
             "provenance": {**base, "license_id": license_id}},
        ])
        return _plan(tmp_path, manifest, [])

    first = build("cc-by-sa-4.0")
    second = build("internal-only")

    assert first["plan_sha256"] != second["plan_sha256"]


@pytest.mark.parametrize(("mutation", "pattern"), [
    ({"license": "typo"},
     r"unknown field\(s\) \['license'\]"),
    ({"obtained_at_utc": "2026-09-25T00:00:00"},
     r"must be an ISO-8601 instant with a timezone"),
    ({"obtained_at_utc": "yesterday"},
     r"must be an ISO-8601 instant with a timezone"),
    ({"license_text_sha256": "not-a-hash"},
     r"must be 64 lowercase hex characters"),
    ({"publisher": 123},
     r"must be a string"),
    ({"version": "x" * 121},
     r"exceeds 120 characters"),
])
def test_manifest_refuses_a_malformed_provenance_block(
    tmp_path: Path, mutation: dict[str, Any], pattern: str
) -> None:
    _write(tmp_path, "primary.csv", PRIMARY)
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"},
         "provenance": {**provenance_block("primary"), **mutation}},
    ])

    with pytest.raises((TypeError, ValueError), match=pattern):
        _plan(tmp_path, manifest, [])


def test_manifest_refuses_a_provenance_block_that_is_not_an_object(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "primary.csv", PRIMARY)
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}, "provenance": "CC BY-SA"},
    ])

    with pytest.raises(TypeError, match="provenance must be an object"):
        _plan(tmp_path, manifest, [])


def test_preview_still_tolerates_a_manifest_without_provenance(tmp_path: Path) -> None:
    """Evaluating a file before its licence is settled is what a preview is for."""
    from app.services.public_lexicon_joint_preview import preview_manifest

    _write(tmp_path, "primary.csv", PRIMARY)
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"}, "provenance": None},
    ])

    report = preview_manifest(manifest, source_root=tmp_path)

    assert report["summary"]["candidate_words"] == 2


# --- the pinned revision of the row a value came from -------------------------
#
# A source may declare where its pinned revision comes from (design 3.4). The plan is
# the artifact a human reviews before anything is written, so the revision a value was
# read at has to be visible in it -- and, being part of what the plan claims, covered
# by its digest.

PINNED_TEMPLATE = "https://zh.wiktionary.org/w/index.php?oldid={revision}"
COMMIT_TEMPLATE = "https://github.com/exam-data/NETEMVocabulary/tree/{revision}"


def _pinned_sources(root: Path) -> Path:
    """Two sources that carry their own per-row revisions, at different lines.

    The same word appears in both, which is what makes "each evidence item carries the
    revision of its own source row" a question with a wrong answer available.
    """
    _write(root, "primary.csv", "head,cn,oldid\nApple,苹果；果实,1111\nBare,,1112\n")
    _write(root, "supplement.csv", "term,translation,rev\n apple ,苹果公司,2222\n")
    return _manifest(root, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"},
         "revision": {"column": "oldid", "url_template": PINNED_TEMPLATE}},
        {"id": "supplement", "role": "meaning", "file": "supplement.csv",
         "columns": {"word": "term", "meaning": "translation"},
         "revision": {"column": "rev", "url_template": PINNED_TEMPLATE}},
    ])


def _locators(items: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    return [
        (item["source_id"], item["line"], item["source_revision"]) for item in items
    ]


def test_each_evidence_item_carries_the_revision_of_its_own_source_row(
    tmp_path: Path,
) -> None:
    manifest = _pinned_sources(tmp_path)

    plan = _plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING])
    entry = _entry(plan, "apple")

    assert _locators(entry["evidence"]["meaning"]) == [
        ("primary", 2, "1111"), ("supplement", 2, "2222"),
    ], "a value must carry the revision of the row it was read from, not of the source"
    assert _locators(entry["evidence"]["word"]) == [
        ("primary", 2, "1111"), ("supplement", 2, "2222"),
    ]
    # The same source, a different row: the revision is per row, not per file.
    assert _locators(_entry(plan, "bare")["evidence"]["word"]) == [("primary", 3, "1112")]


def test_a_file_pinned_as_a_whole_gives_every_row_the_same_revision(
    tmp_path: Path,
) -> None:
    commit = "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44"
    _write(tmp_path, "primary.csv", "head,cn\nApple,苹果；果实\nBare,\n")
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"},
         "revision": {"value": commit, "url_template": COMMIT_TEMPLATE}},
    ])

    plan = _plan(tmp_path, manifest, [])
    entry = _entry(plan, "apple")

    assert _locators(entry["evidence"]["meaning"]) == [("primary", 2, commit)]
    assert _locators(_entry(plan, "bare")["evidence"]["word"]) == [("primary", 3, commit)]


def test_an_empty_revision_cell_stays_empty_rather_than_being_invented(
    tmp_path: Path,
) -> None:
    """A word whose page does not exist upstream has no revision, and the plan says so.

    The row is still evidence -- its meaning is real -- but the revision is empty,
    which is the honest "no link" answer and never a number derived from the line.
    """
    _write(tmp_path, "primary.csv", "head,cn,oldid\nApple,苹果；果实,\n")
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"},
         "revision": {"column": "oldid", "url_template": PINNED_TEMPLATE}},
    ])

    plan = _plan(tmp_path, manifest, [])
    entry = _entry(plan, "apple")

    assert entry["evidence"]["meaning"][0]["source_revision"] == ""
    assert "source_revision" in entry["evidence"]["meaning"][0], (
        "the key is present with an empty value: this source has a revision column, "
        "and this row's cell is empty. That is not the same fact as a source that "
        "declares no revision at all"
    )


@pytest.mark.parametrize("bad_revision", ["x" * 65, "has space", "bad\x01"])
def test_invalid_revision_row_cannot_enter_a_confirmable_plan(
    tmp_path: Path, bad_revision: str
) -> None:
    _write(
        tmp_path, "primary.csv",
        f"head,cn,oldid\nApple,苹果；果实,{bad_revision}\nBare,裸露,1234\n",
    )
    manifest = _manifest(tmp_path, [
        {"id": "primary", "role": "primary", "file": "primary.csv",
         "columns": {"word": "head", "meaning": "cn"},
         "revision": {"column": "oldid", "url_template": PINNED_TEMPLATE}},
    ])

    plan = _plan(tmp_path, manifest, [])

    assert plan["sources"][0]["unreadable_rows"] == [2]
    assert plan["confirmation_ready"] is False
    assert any("unacknowledged_bad_rows" in item for item in plan["confirmation_blockers"])
    assert all(item["normalized_word"] != "apple" for item in plan["entries"])
    assert _entry(plan, "bare")["status"] == "ready"

    acknowledged = _plan(tmp_path, manifest, [
        {"action": "exclude_row", "source_id": "primary", "line": 2,
         "note": "修订号无效，排除该行"},
    ])
    assert acknowledged["confirmation_ready"] is True
    assert [item["normalized_word"] for item in acknowledged["entries"]] == ["bare"]


def test_editing_a_frozen_revision_invalidates_the_plan(tmp_path: Path) -> None:
    """The digest covers the revision, so an edited claim is refused, not confirmed."""
    manifest = _pinned_sources(tmp_path)
    plan = _plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING])
    path = write_plan(plan, tmp_path / "plan.json")

    assert load_plan(path)["plan_sha256"] == plan["plan_sha256"], "control: it loads"

    # Rewrite one revision, in the same serialisation the writer used, so the only
    # difference between the two documents is the claim itself.
    document = json.loads(path.read_text(encoding="utf-8"))
    meaning = next(
        item for item in document["entries"]
        if item["normalized_word"] == "apple"
    )["evidence"]["meaning"]
    assert [item["source_revision"] for item in meaning] == ["1111", "2222"]
    meaning[0]["source_revision"] = "9999"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert plan_digest(document) != document["plan_sha256"]
    with pytest.raises(PlanError, match="digest does not match its contents"):
        load_plan(path)


def test_a_manifest_that_declares_no_revision_keeps_the_old_plan_shape(
    tmp_path: Path,
) -> None:
    """Compatibility: nothing is added to a plan whose sources declare no revision.

    The revision key is added only when a source declared one, so the plan an old
    manifest produces is byte-identical to the one it produced before this existed --
    which is also what keeps a plan written earlier loadable, since the digest is
    computed over these very entries.
    """
    manifest = _two_sources(tmp_path)
    plan = _plan(tmp_path, manifest, [SELECT_PRIMARY_MEANING])

    assert "source_revision" not in json.dumps(plan, ensure_ascii=False)
    for entry in plan["entries"]:
        for items in entry["evidence"].values():
            for item in items:
                assert set(item) == {
                    "source_id", "line", "raw_value", "idempotency_key"
                }

    path = write_plan(plan, tmp_path / "old-shape.json")
    assert load_plan(path)["plan_sha256"] == plan["plan_sha256"]
