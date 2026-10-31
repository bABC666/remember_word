"""Bounded same-day practice derived from saved answers, never browser counters.

Recent failures weigh 3, fuzzy answers 1.5; context answers weigh half as much.
Weight halves every three days over a seven-day window. Completed reading gives
at most .25 credit per day, and cannot replace a successful recall.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Article, ArticleWordExposure, ReviewEvent
from app.services.day import today_bounds


@dataclass(frozen=True)
class PracticePlan:
    required: int
    remaining: int
    pressure: float


def practice_plans(session, user_id: int, entry_ids: list[int], now: datetime) -> dict[int, PracticePlan]:
    if not entry_ids:
        return {}
    start, end = today_bounds(now)
    moment = now if now.tzinfo else now.replace(tzinfo=UTC)
    events = session.scalars(select(ReviewEvent).where(
        ReviewEvent.user_id == user_id, ReviewEvent.lexicon_entry_id.in_(entry_ids),
        ReviewEvent.timestamp >= moment - timedelta(days=7), ReviewEvent.timestamp <= moment,
    ).order_by(ReviewEvent.timestamp, ReviewEvent.id)).all()
    readings = session.execute(select(ArticleWordExposure.lexicon_entry_id, Article.completed_at)
        .join(Article, Article.id == ArticleWordExposure.article_id)
        .where(Article.user_id == user_id, Article.completed.is_(True),
               ArticleWordExposure.lexicon_entry_id.in_(entry_ids),
               Article.completed_at >= moment - timedelta(days=7), Article.completed_at <= moment)).all()
    result = {}
    for entry_id in entry_ids:
        history = [event for event in events if event.lexicon_entry_id == entry_id]
        today = []
        pressure = 0.0
        for event in history:
            timestamp = event.timestamp.replace(tzinfo=UTC) if event.timestamp.tzinfo is None else event.timestamp
            decay = 0.5 ** ((moment - timestamp).total_seconds() / (3 * 86400))
            weight = {"fail": 3, "fuzzy": 1.5}.get(event.result, 0)
            pressure += weight * decay * (1 if event.source == "daily" else .5)
            if start <= timestamp < end and event.source == "daily" and event.review_type == "recall":
                today.append(event)
        reading_days = {time.date() for identifier, time in readings if identifier == entry_id}
        pressure = max(0.0, pressure - .25 * len(reading_days))
        required = 3 if pressure >= 6 else 2 if pressure >= 2.5 else 1
        streak = 0
        for event in reversed(today):
            if event.result != "know":
                break
            streak += 1
        remaining = 0 if not today or today[0].result == "know" else max(0, required - streak)
        result[entry_id] = PracticePlan(required, remaining, pressure)
    return result
