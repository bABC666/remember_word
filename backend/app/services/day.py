"""What "a day" means for this application.

Several answers depend on the same question -- "is this timestamp part of today?" --
so the answer lives in exactly one place instead of being derived per endpoint: the
dashboard's streak and "added today" numbers, and the study queue's daily new-word
allowance (G8 / T16).

The day is a **UTC** day. Nothing in the schema stores a user's time zone, so a local
day cannot be derived from the data. Deriving one from the server's clock instead
would make the same review belong to different days depending on where the process
happens to run, and would move the reset time whenever the deployment moved.

``timestamp`` columns hold UTC wall-clock values (SQLAlchemy writes an aware
``datetime.now(UTC)`` as its UTC fields), so comparing a bound window against them is
a plain comparison against the same representation -- no ``date()`` conversion, and no
second time-zone interpretation in SQL.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta


def _as_utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; every value this application stores is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The half-open window ``[start, end)`` of one UTC day."""
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def today_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The window of the UTC day that ``now`` falls in."""
    moment = now or datetime.now(UTC)
    return day_bounds(_as_utc(moment).date())
