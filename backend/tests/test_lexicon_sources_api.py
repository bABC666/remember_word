"""The lexicon's imported-source list: access, isolation, and display safety.

A logged-in user may read which sources a public import recorded for a lexicon they
can access. The endpoint is deliberately narrow. It reads the import's own relations
-- the run for this lexicon, its link rows, and the artifact each one names -- rather
than scanning ``source_artifact`` for anything that looks related, because an artifact
carries no lexicon and two imports may legitimately share one.

What it returns is the *declaration* an administrator recorded, never a verified
right: the licence fields are what a submitter said, the licence text itself is never
opened, and the payload says so in ``authorization_review``. Local paths, the stored
locator, the mapping, the plan and every other user's data stay out.

Every import here is a real confirmation through ``confirm_plan`` against synthetic
CSV, on the session's temporary database. No test reads a file from ``data/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_public_lexicon_confirm import (
    _administrator,
    _build_plan,
    _standard_decisions,
)

#: Fields the response must carry, and the whole of what a source may expose. Named
#: rather than counted, so adding a field has to be a deliberate edit here too.
SOURCE_FIELDS = frozenset({
    "source_artifact_id", "role", "name", "publisher", "version", "obtained_at_utc",
    "format", "license_id", "license_text_sha256", "file_sha256", "mapping_sha256",
    "byte_size", "use_scope", "display_scope", "declared_approval_state", "runs",
})

#: Things that must never appear anywhere in the payload. ``storage_locator`` is the
#: archive's own local path; ``mapping_json`` is the frozen reader configuration;
#: ``plan_sha256`` and ``plan_type`` belong to the locked plan; ``confirmed_by`` is
#: the operator's account. None of them is needed to show a source page.
FORBIDDEN_KEYS = frozenset({
    "storage_locator", "mapping_json", "mapping", "plan_sha256", "plan_type",
    "confirmed_by_user_id", "confirmed_by_username", "file_path", "path",
})

FORBIDDEN_SUBSTRINGS = ("/", "\\", "C:", "file_sha256\"", "sources/primary.csv")


@pytest.fixture()
def admin(make_world):
    """An administrator who performs the confirmations that create the records."""
    value = make_world("lexsources-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


def _session_of(admin):
    return admin.session()


def _system_lexicon_once(session, name: str):
    """The system public lexicon with this name, created on first use.

    ``tests.test_public_lexicon_confirm._system_lexicon`` always inserts, and a second
    insert with the same name would make the plan's target ambiguous -- which the
    confirmation refuses by design. Tests here need one *shared* target for several
    imports, so they resolve it instead.
    """
    from sqlalchemy import select

    from app.models import Lexicon

    found = session.scalars(
        select(Lexicon).where(
            Lexicon.owner_user_id.is_(None),
            Lexicon.visibility == "public",
            Lexicon.name == name,
        )
    ).all()
    assert len(found) <= 1, (
        f"{len(found)} system lexicons are named {name!r}; the tests leaked into each "
        "other's targets"
    )
    if found:
        return found[0]
    lexicon = Lexicon(
        owner_user_id=None,
        name=name,
        description="synthetic target used by the Phase 2.9 source-list tests",
        visibility="public",
        source_type=f"lexsources-{name}",
    )
    session.add(lexicon)
    session.commit()
    session.refresh(lexicon)
    return lexicon


def _plan_for(root: Path, *, token: str, lexicon_name: str) -> dict:
    """A buildable plan for an import of a fresh synthetic source."""
    return _build_plan(
        root, token=token, target=lexicon_name,
        decisions=_standard_decisions(token),
    )


def _import_into(admin, root: Path, *, token: str, lexicon_name: str) -> dict:
    """One real confirmation of a fresh synthetic source into a system lexicon.

    The lexicon is created on first use and reused after that, so two calls with the
    same name send two imports into *one* target -- which is what makes it the same
    lexicon, and therefore the same source list, in the response.
    """
    from app.services.public_lexicon_confirm import confirm_plan

    plan = _plan_for(root, token=token, lexicon_name=lexicon_name)
    with admin.session() as session:
        lexicon = _system_lexicon_once(session, lexicon_name)
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
    return {"result": result, "lexicon_id": lexicon.id, "plan": plan}


def _names(payload: dict) -> list[str]:
    return sorted(source["name"] for source in payload["sources"])


def _all_keys(value: object) -> set[str]:
    """Every key anywhere in the payload, so a nested leak cannot hide."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _all_keys(item)
    return found


# --- who may read it ---------------------------------------------------------


def test_an_unauthenticated_caller_is_refused(admin, tmp_path: Path) -> None:
    """No cookie, no sources -- and the refusal happens before any read."""
    from starlette.testclient import TestClient

    from app.main import app

    root = tmp_path / "sources"
    root.mkdir()
    seeded = _import_into(admin, root, token="lexanon", lexicon_name="lex-anon")

    with TestClient(app) as anonymous:
        response = anonymous.get(f"/api/lexicons/{seeded['lexicon_id']}/sources")

    assert response.status_code == 401, response.text
    assert str(seeded["lexicon_id"]) not in response.text
    assert "primary.csv" not in response.text, "no source data may leak in the refusal"


def test_a_lexicon_the_caller_cannot_access_is_not_disclosed(
    admin, make_world, tmp_path: Path
) -> None:
    """Another user's private lexicon answers 404, not 403 and not an empty list.

    ``load_readable_lexicon`` is the single access check every lexicon route uses, so
    this endpoint inherits the same distinction the rest of the API makes: a system
    public lexicon is readable by any logged-in user, another user's private lexicon
    is a 404 -- identical to a lexicon that does not exist, so responses cannot be
    compared to probe for somebody else's data.
    """
    root = tmp_path / "sources"
    root.mkdir()
    seeded = _import_into(admin, root, token="lexpriv", lexicon_name="lex-priv")
    stranger = make_world("lexsources-stranger")

    try:
        response = stranger.client.get(f"/api/lexicons/{seeded['lexicon_id']}/sources")
        missing = stranger.client.get("/api/lexicons/99999999/sources")
    finally:
        stranger.client.__exit__(None, None, None)

    # An ownerless (system, public) lexicon is readable by every authenticated user.
    assert response.status_code == 200, response.text
    assert _names(response.json()) == ["primary.csv", "supplement.csv"]
    assert missing.status_code == 404, missing.text

    # And the private case, which is the one that must not be disclosed.
    owner = make_world("lexsources-owner")
    try:
        private_id = owner.lexicon("lex-private-owner")
        assert owner.client.get(
            f"/api/lexicons/{private_id}/sources"
        ).status_code == 200, "control: the owner may read their own lexicon"
        outsider = make_world("lexsources-outsider")
        try:
            refused = outsider.client.get(f"/api/lexicons/{private_id}/sources")
        finally:
            outsider.client.__exit__(None, None, None)
        assert refused.status_code == 404, refused.text
        assert str(private_id) not in refused.text
    finally:
        owner.client.__exit__(None, None, None)


# --- what it reports ---------------------------------------------------------


def test_multiple_sources_of_one_lexicon_are_all_reported(admin, tmp_path: Path) -> None:
    """Two confirmations into one lexicon: both sources, each with its own run."""
    root = tmp_path / "sources"
    root.mkdir()
    first = _import_into(admin, root, token="lexmulti1", lexicon_name="lex-multi")
    _import_into(admin, root, token="lexmulti2", lexicon_name="lex-multi")

    response = admin.client.get(f"/api/lexicons/{first['lexicon_id']}/sources")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["lexicon"] == {"id": first["lexicon_id"], "name": "lex-multi"}
    # Four artifacts: two sources in each of the two runs. The second run's files have
    # different bytes, so they are different artifacts, not reuses of the first's.
    assert _names(payload) == [
        "primary.csv", "primary.csv", "supplement.csv", "supplement.csv"
    ]
    assert {source["role"] for source in payload["sources"]} == {"primary", "meaning"}
    for source in payload["sources"]:
        assert len(source["runs"]) == 1
        assert source["runs"][0]["outcome"] in {"created", "reused"}
        assert source["runs"][0]["run_id"].startswith("lex-")
    # The runs reported are exactly the two that targeted this lexicon, one per source
    # row -- so nothing from another import is mixed in and no run is counted twice.
    assert len({source["runs"][0]["run_id"] for source in payload["sources"]}) == 2


def test_another_lexicons_sources_do_not_leak_in(admin, tmp_path: Path) -> None:
    """Each lexicon reports only the runs that named it as the target.

    This is the isolation the endpoint's read path is built for: it starts from the
    runs targeting *this* lexicon, so a second import into a different lexicon cannot
    appear here even though both live in the same artifact table.
    """
    root = tmp_path / "sources"
    root.mkdir()
    mine = _import_into(admin, root, token="lexiso1", lexicon_name="lex-iso-a")
    theirs = _import_into(admin, root, token="lexiso2", lexicon_name="lex-iso-b")

    mine_payload = admin.client.get(
        f"/api/lexicons/{mine['lexicon_id']}/sources"
    ).json()
    theirs_payload = admin.client.get(
        f"/api/lexicons/{theirs['lexicon_id']}/sources"
    ).json()

    mine_runs = {source["runs"][0]["run_id"] for source in mine_payload["sources"]}
    theirs_runs = {source["runs"][0]["run_id"] for source in theirs_payload["sources"]}
    assert mine_runs == {mine["result"]["run_id"]}
    assert theirs_runs == {theirs["result"]["run_id"]}
    assert mine_runs.isdisjoint(theirs_runs)
    assert not {
        source["source_artifact_id"] for source in mine_payload["sources"]
    } & {
        source["source_artifact_id"] for source in theirs_payload["sources"]
    }


def test_a_lexicon_with_no_imported_sources_answers_an_empty_list(world) -> None:
    """Empty is an ordinary answer, and it still states the authorization limit.

    A private lexicon created through the API has never been imported into. The
    response says so with an empty list rather than 404 or a synthesized source, and
    the review block is present so a client cannot mistake "no records" for "cleared".
    """
    response = world.client.post(
        "/api/lexicons", json={"name": "lex-empty", "description": ""}
    )
    assert response.status_code == 201, response.text
    lexicon_id = response.json()["id"]

    payload = world.client.get(f"/api/lexicons/{lexicon_id}/sources").json()

    assert payload["sources"] == []
    assert payload["lexicon"]["name"] == "lex-empty"
    assert payload["authorization_review"]["status"] == "not_assessed"
    assert payload["authorization_review"]["pending_sources"] == []
    assert "未核实许可真实性" in payload["authorization_review"]["message"]


def test_the_response_exposes_only_display_safe_fields(admin, tmp_path: Path) -> None:
    """The boundary: declaration fields in, local paths and plans out.

    The provenance columns are what an operator declared, so they are shown with their
    fingerprints. What is *not* shown is where this machine keeps the file, how it was
    parsed, which plan produced the run, or who confirmed it -- none of which a source
    page needs, and each of which would widen the surface for no reader's benefit.
    """
    root = tmp_path / "sources"
    root.mkdir()
    seeded = _import_into(admin, root, token="lexsafe", lexicon_name="lex-safe")

    payload = admin.client.get(
        f"/api/lexicons/{seeded['lexicon_id']}/sources"
    ).json()

    assert set(payload) == {"lexicon", "sources", "authorization_review"}
    for source in payload["sources"]:
        assert set(source) == SOURCE_FIELDS, (
            f"a source exposes {sorted(set(source) - SOURCE_FIELDS)} and is missing "
            f"{sorted(SOURCE_FIELDS - set(source))}"
        )
        assert set(source["runs"][0]) == {"run_id", "confirmed_at", "outcome"}
        # The declaration itself survived, so this is not safe by having dropped it.
        assert source["publisher"] == "synthetic publisher"
        assert source["version"] == "2026-09-25"
        assert source["license_id"] == "synthetic-test-only"
        assert source["use_scope"] == "local-evaluation"
        assert source["display_scope"] == "not-for-publication"
        assert source["obtained_at_utc"] == "2026-09-25T00:00:00Z"
        assert len(source["file_sha256"]) == 64
        assert len(source["mapping_sha256"]) == 64
        assert source["license_text_sha256"] == ""
        assert source["byte_size"] > 0
        assert source["declared_approval_state"] == "not_assessed"

    leaked = _all_keys(payload) & FORBIDDEN_KEYS
    assert not leaked, f"the payload exposes {sorted(leaked)}"
    body = str(payload)
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in body.replace("synthetic-test-only", ""), (
            f"the payload contains {needle!r}: {body[:400]}"
        )
    assert seeded["plan"]["plan_sha256"] not in body
    assert admin.username not in body


def test_a_declared_licence_is_reported_as_a_declaration_not_an_approval(
    admin, tmp_path: Path
) -> None:
    """The one distinction this endpoint must not blur.

    An ordinary licence declaration and an explicit "not approved" declaration are both
    just declarations. The first must not read as confirmed, and the second must be
    visibly pending -- which is what ``declared_approval_state`` and the review block
    are for. A source page that showed ``license_id`` alone would make the two look
    alike.
    """
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plain = _import_into(admin, root, token="lexdecl1", lexicon_name="lex-decl")

    # The same target, with one source declaring it is not approved yet. The lexicon
    # already exists, so this is a second import into it and the response shows both.
    token = "lexdecl2"
    plan = _plan_for(root, token=token, lexicon_name="lex-decl")
    plan["sources"][0]["provenance"]["use_scope"] = "预演草案·未获批准：仅本机只读预演"
    plan["plan_sha256"] = plan_digest(plan)
    with admin.session() as session:
        _system_lexicon_once(session, "lex-decl")
        confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )

    payload = admin.client.get(
        f"/api/lexicons/{plain['lexicon_id']}/sources"
    ).json()

    states = [source["declared_approval_state"] for source in payload["sources"]]
    assert states.count("explicitly_unapproved") == 1, (
        f"the one source that says it is unapproved must be marked as such: {states}"
    )
    assert states.count("not_assessed") == len(states) - 1, (
        f"plain declarations stay plain: {states}"
    )
    review = payload["authorization_review"]
    assert review["status"] == "pending_owner_approval"
    assert review["pending_sources"], "the pending source must be named"
    assert "不核实许可有效性" in review["message"]
