from datetime import UTC, datetime

import pytest


@pytest.mark.parametrize(
    ("initial", "result", "expected"),
    [
        ("new", "fail", "weak"),
        ("new", "fuzzy", "learning"),
        ("new", "know", "familiar"),
        ("weak", "know", "learning"),
        ("known", "know", "mastered"),
    ],
)
def test_transparent_status_transitions(initial: str, result: str, expected: str) -> None:
    from app.services.scheduler import calculate_schedule

    schedule = calculate_schedule(initial, result, 0, now=datetime(2026, 9, 21, tzinfo=UTC))
    assert schedule.status == expected
    assert schedule.next_review_at > datetime(2026, 9, 21, tzinfo=UTC)


def test_review_writes_full_event_and_updates_cached_counts(session) -> None:
    from app.models import ReviewEvent, Word
    from app.services.study import record_review

    word = Word(word="retain", status="new", source_meanings=["保留"], source_raw="retain")
    session.add(word)
    session.commit()
    event = record_review(session, word.id, "fail", "daily", "recall")
    session.refresh(word)
    assert event.status_before == "new"
    assert event.status_after == "weak"
    assert event.result == "fail"
    assert event.review_type == "recall"
    assert word.recall_fail == 1
    assert word.status == "weak"
    assert session.query(ReviewEvent).count() == 1
