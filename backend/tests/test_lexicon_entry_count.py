"""The lexicon read API reports the real entry count, never a stored counter.

``lexicon.entry_count`` is a cache column: ``V1.2-PHASE0-AUDIT-AND-DESIGN.md``
already records it as "缓存列，可重建", and the Phase 2.9 confirmation design decided
that a public import must **not** update it, so that the verified-backup baseline keeps
its ability to detect an unexpected write to the ``lexicon`` row
(``data/recovery/baseline.json`` freezes ``lexicon.entry_count`` into the row identity,
and ``tools/verified_db.py`` fails on a changed row).

That decision is only safe while nothing actually reads the column. These tests make
that an enforced contract rather than a convention: they write a deliberately wrong
value into the column and assert every read path still answers with ``COUNT(*)``. If a
future change starts trusting the column, these fail before a stale count ever reaches
a user.
"""

from __future__ import annotations

from sqlalchemy import select

#: A value that can never be the true count in these tests, so a read path that
#: trusted the column would be caught immediately.
WRONG_STORED_COUNT = 999


def _entries_of(world, lexicon_id: int) -> int:
    from app.models import LexiconEntry

    with world.session() as session:
        return len(
            session.scalars(
                select(LexiconEntry.id).where(LexiconEntry.lexicon_id == lexicon_id)
            ).all()
        )


def _store_wrong_count(world, lexicon_id: int) -> None:
    from app.models import Lexicon

    with world.session() as session:
        lexicon = session.get(Lexicon, lexicon_id)
        assert lexicon is not None
        lexicon.entry_count = WRONG_STORED_COUNT
        session.commit()


def _listed(world, lexicon_id: int) -> dict:
    response = world.client.get("/api/lexicons")
    assert response.status_code == 200, response.text
    match = [item for item in response.json() if item["id"] == lexicon_id]
    assert match, f"lexicon {lexicon_id} missing from the list"
    return match[0]


def test_a_private_lexicon_reports_the_real_count_not_the_stored_column(world) -> None:
    lexicon_id = world.lexicon("cache-probe-private")
    for index in range(3):
        world.add_word(f"cache-probe-{index}", lexicon_id=lexicon_id)
    assert _entries_of(world, lexicon_id) == 3

    _store_wrong_count(world, lexicon_id)

    assert _listed(world, lexicon_id)["entry_count"] == 3
    detail = world.client.get(f"/api/lexicons/{lexicon_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["entry_count"] == 3


def test_a_rename_also_reports_the_real_count(world) -> None:
    lexicon_id = world.lexicon("cache-probe-rename")
    world.add_word("cache-probe-rename-word", lexicon_id=lexicon_id)
    _store_wrong_count(world, lexicon_id)

    response = world.client.patch(
        f"/api/lexicons/{lexicon_id}", json={"description": "renamed"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["entry_count"] == 1


def test_a_system_lexicon_reports_the_real_count_not_the_stored_column(world) -> None:
    """The case an import will actually touch.

    A public import adds ``lexicon_entry`` rows to the system lexicon. Because the read
    path counts rows instead of trusting the column, leaving ``entry_count`` untouched
    changes nothing a user can see -- which is what makes that choice free rather than a
    visible regression.
    """
    from app.models import Lexicon

    with world.session() as session:
        lexicon = Lexicon(
            owner_user_id=None,
            name="cache-probe-system",
            description="system lexicon used by the entry-count contract test",
            visibility="public",
            source_type="manual",
        )
        session.add(lexicon)
        session.commit()
        session.refresh(lexicon)
        lexicon_id = lexicon.id

    for index in range(2):
        world.add_word(f"cache-probe-system-{index}", lexicon_id=lexicon_id)

    _store_wrong_count(world, lexicon_id)

    listed = _listed(world, lexicon_id)
    assert listed["is_system"] is True
    assert listed["entry_count"] == 2
    assert _entries_of(world, lexicon_id) == 2


def test_creating_a_lexicon_reports_zero_without_reading_the_column(world) -> None:
    """A new lexicon has no entries, so ``create_lexicon`` hardcodes zero.

    Pinned because it is the one call site that does not go through
    ``_count_entries``: it must stay correct for the same reason.
    """
    response = world.client.post(
        "/api/lexicons", json={"name": "cache-probe-created", "description": ""}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["entry_count"] == 0
    assert _entries_of(world, body["id"]) == 0
