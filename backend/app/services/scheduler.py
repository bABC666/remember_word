from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Schedule:
    status: str
    next_review_at: datetime
    consecutive_failures: int


PROGRESSION = {
    "new": ("familiar", 1),
    "familiar": ("learning", 3),
    "learning": ("known", 7),
    "known": ("mastered", 14),
    "mastered": ("mastered", 30),
}


def calculate_schedule(
    current_status: str,
    result: str,
    consecutive_failures: int,
    *,
    now: datetime | None = None,
) -> Schedule:
    current = now or datetime.now(UTC)
    if result == "fail":
        failures = consecutive_failures + 1
        return Schedule("weak", current + timedelta(hours=12 if failures == 1 else 4), failures)
    if result == "fuzzy":
        return Schedule("learning", current + timedelta(days=1), 0)
    if result != "know":
        raise ValueError(f"Unsupported review result: {result}")
    if current_status == "weak":
        return Schedule("learning", current + timedelta(days=2), 0)
    next_status, days = PROGRESSION.get(current_status, ("familiar", 1))
    return Schedule(next_status, current + timedelta(days=days), 0)
