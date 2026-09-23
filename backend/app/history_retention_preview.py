"""Read-only preview of the *unconfirmed* G6 history retention proposal.

This module deliberately has no archive writer, deletion path, or receipt API.
The preview cannot authorize a later cleanup: that requires a separately locked
candidate set and transactional implementation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

RETENTION_DAYS_PROPOSAL = 365
EVENT_TYPES_PROPOSAL = (
    "login_failed",
    "reauth_failed",
    "user_login",
    "article_word_lookup",
)


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def preview_history_retention(database_path: Path, *, now: datetime | None = None) -> dict:
    """Count proposed candidates from one SQLite read snapshot, without writes."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    cutoff = now - timedelta(days=RETENTION_DAYS_PROPOSAL)
    counts = {name: {"total": 0, "candidate": 0} for name in EVENT_TYPES_PROPOSAL}
    skipped = {
        "highest_id": 0,
        "not_allowlisted": 0,
        "invalid_timestamp": 0,
        "future_timestamp": 0,
        "not_before_cutoff": 0,
    }
    candidate_ids: list[int] = []
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        # One SELECT keeps MAX(id) and candidate rows in the same read snapshot.
        rows = connection.execute(
            "SELECT id, event_type, timestamp, MAX(id) OVER () AS max_id "
            "FROM history_event ORDER BY id"
        )
        max_id = None
        total = 0
        for event_id, event_type, timestamp, row_max_id in rows:
            total += 1
            max_id = row_max_id
            if event_type in counts:
                counts[event_type]["total"] += 1
            if event_id == row_max_id:
                skipped["highest_id"] += 1
            elif event_type not in counts:
                skipped["not_allowlisted"] += 1
            else:
                parsed = _utc_timestamp(timestamp)
                if parsed is None:
                    skipped["invalid_timestamp"] += 1
                elif parsed > now:
                    skipped["future_timestamp"] += 1
                elif parsed >= cutoff:
                    skipped["not_before_cutoff"] += 1
                else:
                    candidate_ids.append(event_id)
                    counts[event_type]["candidate"] += 1
    canonical_ids = json.dumps(candidate_ids, separators=(",", ":"), ensure_ascii=True)
    return {
        "policy_status": "proposal_unconfirmed",
        "retention_days_proposal": RETENTION_DAYS_PROPOSAL,
        "cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
        "max_history_event_id": max_id,
        "total_count": total,
        "candidate_count": len(candidate_ids),
        "candidate_ids_sha256": hashlib.sha256(canonical_ids.encode("ascii")).hexdigest(),
        "event_counts": counts,
        "skipped": skipped,
    }
