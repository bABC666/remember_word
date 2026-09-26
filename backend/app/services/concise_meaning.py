"""Short study meanings: who proposed them, who confirmed them, what is shown.

The study page prefers one to three short, common, simplified senses over the full
source text. Those senses are a **display value**, and this module is the only place
that writes them. Three rules shape everything below.

**Nothing is shown until a human confirms it.** A proposal is stored as a
``candidate``; the read path filters on ``confirmed``. So "an unconfirmed candidate
cannot reach a page" is not a habit any caller has to remember -- there is no code
path from a candidate to a response, and the database refuses to store a confirmed
row without a named person and a timestamp behind it.

**The source's own words are never overwritten.** Proposing and confirming touch
``entry_concise_meaning`` only. ``lexicon_entry.source_raw``, ``source_meanings`` and
``default_anchor`` are never written here, and neither is
``entry_source_evidence``: the point of the display value is that a reader who doubts
it can still go and read what the source actually said.

**Wording carries its own provenance.** ``source`` means the text is the source's own
value at a named position; ``derived`` means it is a documented change to one
(traditional to simplified, noise removed, re-worded, one sense extracted), which has
to say *what* was changed; ``ai_supplement`` means no source says this, which has to
say why it was added and is forbidden from carrying a source pointer at all. A
supplement therefore cannot masquerade as a quotation even if a later caller tries,
because the shape is not representable.

**Change is a withdrawal, not an edit.** Replacing a displayed meaning is
``reject`` (with a reason) followed by a fresh ``propose`` and ``confirm``. Nothing
overwrites a value in place, and every step appends a row to
``entry_concise_meaning_revision``, which this module never updates or deletes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CONCISE_MEANING_KINDS,
    CONCISE_MEANING_MAX_LENGTH,
    CONCISE_MEANING_MAX_SLOTS,
    EntryConciseMeaning,
    EntryConciseMeaningRevision,
    EntrySourceEvidence,
    LexiconEntry,
    User,
)

KIND_SOURCE = "source"
KIND_DERIVED = "derived"
KIND_AI_SUPPLEMENT = "ai_supplement"

STATUS_CANDIDATE = "candidate"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

ACTION_PROPOSED = "proposed"
ACTION_CONFIRMED = "confirmed"
ACTION_REJECTED = "rejected"

#: What the study page calls each kind. Kept here rather than in the frontend so the
#: label and the stored value cannot drift apart.
KIND_LABELS: dict[str, str] = {
    KIND_SOURCE: "来源原文",
    KIND_DERIVED: "据来源改写",
    KIND_AI_SUPPLEMENT: "自拟补充",
}

#: Recorded on a candidate that a newer proposal replaced at the same slot.
SUPERSEDED_NOTE = "被同一位置的更新候选取代"


class ConciseMeaningRefused(Exception):
    """The write cannot be made as it stands. **Nothing was written.**

    Raised before any row is added, so a refusal leaves the database exactly as it
    was. The message names the offending value: the answer is a corrected proposal,
    never a hand-edited row.
    """


@dataclass(frozen=True)
class ConciseMeaningProposal:
    """One short value proposed for one display slot.

    ``source_locator`` is the human-readable source position (``primary:12``) and is
    required for everything except a supplement. ``source_evidence_id`` is optional
    because it only exists once an import has recorded evidence for this entry; the
    locator is what keeps the value checkable either way.
    """

    text: str
    provenance_kind: str
    display_order: int
    source_locator: str = ""
    derivation_note: str = ""
    source_evidence_id: int | None = None


def normalize_word(word: str) -> str:
    """The entry identity rule, unchanged from the locked plan (``strip_casefold_v1``)."""
    return word.strip().casefold()


def _require_administrator(actor: User | None) -> User:
    """Only an active administrator may change what the shared lexicon displays.

    A displayed meaning is content every account sees, so a normal user's approval
    cannot be enough -- otherwise one account could change what another is studying.
    Returns the actor so callers do not have to assert what was just proved.
    """
    if actor is None:
        raise ConciseMeaningRefused("需要管理员身份才能修改学习页展示的简短释义。")
    if not actor.is_active:
        raise ConciseMeaningRefused("该管理员账号已停用，拒绝修改简短释义。")
    if not actor.is_admin:
        raise ConciseMeaningRefused("当前账号不是管理员，拒绝修改简短释义。")
    return actor


def _moment(value: datetime | None) -> datetime:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("the moment must be timezone-aware")
    return moment.astimezone(UTC)


def _validate(proposal: ConciseMeaningProposal) -> tuple[str, str, str]:
    """Check one proposal, returning the stripped text, locator and note."""
    text = proposal.text.strip()
    locator = proposal.source_locator.strip()
    note = proposal.derivation_note.strip()
    kind = proposal.provenance_kind

    if kind not in CONCISE_MEANING_KINDS:
        raise ConciseMeaningRefused(
            f"未知的释义来源类型 {kind!r}；只接受 "
            + "、".join(sorted(CONCISE_MEANING_KINDS))
            + "。"
        )
    if not text:
        raise ConciseMeaningRefused("简短释义不能为空。")
    if len(text) > CONCISE_MEANING_MAX_LENGTH:
        raise ConciseMeaningRefused(
            f"简短释义太长（{len(text)} 字，上限 {CONCISE_MEANING_MAX_LENGTH} 字）："
            f"{text[:20]}…。学习页只显示 1–{CONCISE_MEANING_MAX_SLOTS} 个常见义项，"
            "完整释义请留在来源记录里。"
        )
    if not 1 <= proposal.display_order <= CONCISE_MEANING_MAX_SLOTS:
        raise ConciseMeaningRefused(
            f"展示位置 {proposal.display_order} 越界；只允许 1–{CONCISE_MEANING_MAX_SLOTS}。"
        )
    if kind == KIND_AI_SUPPLEMENT:
        if locator or proposal.source_evidence_id is not None:
            raise ConciseMeaningRefused(
                "自拟补充不得指向任何来源位置：它没有来源原文，不能写成引文。"
                "请去掉 source_locator 与 source_evidence_id。"
            )
        if not note:
            raise ConciseMeaningRefused("自拟补充必须写明补充理由。")
    else:
        if not locator:
            raise ConciseMeaningRefused(
                f"{KIND_LABELS[kind]}必须记录来源位置（如 primary:12），"
                "否则无法回查。"
            )
        if kind == KIND_DERIVED and not note:
            raise ConciseMeaningRefused(
                "据来源改写必须写明改了什么（如繁转简、删去词形表噪声、重新措辞），"
                "否则读者无法核对。"
            )
    return text, locator, note


def _slots(session: Session, entry_id: int) -> dict[int, EntryConciseMeaning]:
    """Every live (non-rejected) row of one entry, by display slot."""
    rows = session.scalars(
        select(EntryConciseMeaning).where(
            EntryConciseMeaning.lexicon_entry_id == entry_id,
            EntryConciseMeaning.status != STATUS_REJECTED,
        )
    ).all()
    return {row.display_order: row for row in rows}


def propose(
    session: Session,
    *,
    entry: LexiconEntry,
    proposals: Sequence[ConciseMeaningProposal],
    actor: User | None,
    moment: datetime | None = None,
) -> list[EntryConciseMeaning]:
    """Append candidates for one entry. Nothing becomes visible yet.

    A later candidate for the same slot replaces the earlier *candidate* by rejecting
    it with a note, so a draft can be corrected before anyone approves it. A
    **confirmed** value is never replaced this way: it has to be withdrawn first,
    which is a separate, recorded decision.
    """
    administrator = _require_administrator(actor)
    at = _moment(moment)
    if len(proposals) > CONCISE_MEANING_MAX_SLOTS:
        raise ConciseMeaningRefused(
            f"一次最多提交 {CONCISE_MEANING_MAX_SLOTS} 个简短释义，收到 {len(proposals)} 个。"
        )

    checked = [(proposal, *_validate(proposal)) for proposal in proposals]
    orders = [proposal.display_order for proposal, _t, _l, _n in checked]
    if len(set(orders)) != len(orders):
        raise ConciseMeaningRefused("同一个词条内展示位置不能重复。")

    occupied = _slots(session, entry.id)
    created: list[EntryConciseMeaning] = []
    for proposal, text, locator, note in checked:
        evidence = _require_evidence_belongs_to_entry(
            session, entry, proposal.source_evidence_id
        )
        if proposal.provenance_kind == KIND_SOURCE:
            recorded_texts = (
                [evidence.raw_text] if evidence is not None
                else [*entry.source_meanings, entry.source_raw]
            )
            if not any(text in recorded for recorded in recorded_texts if recorded):
                raise ConciseMeaningRefused(
                    f"「{text}」未见于该词的来源原文，不能标为来源逐字内容。"
                    "若经过改写，请用 derived 并说明改动；若没有来源，请用 ai_supplement。"
                )
        existing = occupied.get(proposal.display_order)
        if existing is not None:
            if existing.status == STATUS_CONFIRMED:
                raise ConciseMeaningRefused(
                    f"第 {proposal.display_order} 位已经有一条已确认的释义"
                    f"「{existing.text}」，不能直接覆盖。"
                    "请先拒绝（撤回）它，再重新提交候选并确认。"
                )
            _reject_row(
                session,
                row=existing,
                entry=entry,
                actor=administrator,
                note=SUPERSEDED_NOTE,
                at=at,
            )
        row = EntryConciseMeaning(
            lexicon_entry_id=entry.id,
            display_order=proposal.display_order,
            text=text,
            provenance_kind=proposal.provenance_kind,
            source_evidence_id=proposal.source_evidence_id,
            source_locator=locator,
            derivation_note=note,
            status=STATUS_CANDIDATE,
            proposed_by_username=administrator.username,
            proposed_at=at,
            created_at=at,
            updated_at=at,
        )
        session.add(row)
        session.flush()
        _record(
            session,
            row=row,
            entry=entry,
            action=ACTION_PROPOSED,
            actor=administrator,
            note=note,
            at=at,
        )
        occupied[proposal.display_order] = row
        created.append(row)
    session.flush()
    return created


def confirm(
    session: Session,
    *,
    meaning: EntryConciseMeaning,
    confirmer: User | None,
    note: str = "",
    moment: datetime | None = None,
) -> EntryConciseMeaning:
    """Move one candidate to ``confirmed``, naming the person who did it.

    This is the human step the owner's rule requires before anything an AI drafted
    can appear on the study page. Confirming an already-confirmed row is a no-op
    rather than an error, so re-running a review pass is safe.
    """
    actor = _require_administrator(confirmer)
    at = _moment(moment)
    if meaning.status == STATUS_CONFIRMED:
        return meaning
    if meaning.status != STATUS_CANDIDATE:
        raise ConciseMeaningRefused(
            f"只有待确认的候选才能确认；该行状态为 {meaning.status!r}。"
        )
    entry = session.get(LexiconEntry, meaning.lexicon_entry_id)
    if entry is None:
        raise ConciseMeaningRefused("词条已不存在，无法确认该简短释义。")

    meaning.status = STATUS_CONFIRMED
    meaning.confirmed_by_user_id = actor.id
    meaning.confirmed_by_username = actor.username
    meaning.confirmed_at = at
    meaning.updated_at = at
    session.add(meaning)
    session.flush()
    _record(
        session, row=meaning, entry=entry, action=ACTION_CONFIRMED,
        actor=actor, note=note, at=at,
    )
    session.flush()
    return meaning


def reject(
    session: Session,
    *,
    meaning: EntryConciseMeaning,
    actor: User | None,
    note: str,
    moment: datetime | None = None,
) -> EntryConciseMeaning:
    """Withdraw a candidate or a displayed value, recording who and why.

    Rejecting a confirmed row takes it off the study page and frees its slot. The
    row itself stays, so the record of what was once displayed -- and of who approved
    it -- is not erased by withdrawing it. A reason is required: a withdrawal nobody
    explained is not reviewable.
    """
    administrator = _require_administrator(actor)
    at = _moment(moment)
    if meaning.status == STATUS_REJECTED:
        return meaning
    if not note.strip():
        raise ConciseMeaningRefused("撤回或拒绝必须写明理由。")
    entry = session.get(LexiconEntry, meaning.lexicon_entry_id)
    if entry is None:
        raise ConciseMeaningRefused("词条已不存在，无法撤回该简短释义。")
    _reject_row(session, row=meaning, entry=entry, actor=administrator,
                note=note.strip(), at=at)
    session.flush()
    return meaning


def _reject_row(
    session: Session,
    *,
    row: EntryConciseMeaning,
    entry: LexiconEntry,
    actor: User,
    note: str,
    at: datetime,
) -> None:
    row.status = STATUS_REJECTED
    row.updated_at = at
    session.add(row)
    session.flush()
    _record(session, row=row, entry=entry, action=ACTION_REJECTED, actor=actor, note=note, at=at)


def _require_evidence_belongs_to_entry(
    session: Session, entry: LexiconEntry, evidence_id: int | None
) -> EntrySourceEvidence | None:
    """A quote has to point at evidence of *this* word.

    Without this check a proposal could cite another word's evidence row and look
    sourced while saying nothing about this one.
    """
    if evidence_id is None:
        return None
    evidence = session.get(EntrySourceEvidence, evidence_id)
    if evidence is None:
        raise ConciseMeaningRefused(f"证据行 {evidence_id} 不存在。")
    if evidence.lexicon_entry_id != entry.id:
        raise ConciseMeaningRefused(
            f"证据行 {evidence_id} 不属于词条「{entry.word}」，不能作为它的来源。"
        )
    return evidence


def _record(
    session: Session,
    *,
    row: EntryConciseMeaning,
    entry: LexiconEntry,
    action: str,
    actor: User,
    note: str,
    at: datetime,
) -> EntryConciseMeaningRevision:
    """Append one history row. **Never** an UPDATE, and never a DELETE."""
    revision = EntryConciseMeaningRevision(
        lexicon_entry_id=entry.id,
        normalized_word=entry.normalized_word,
        concise_meaning_id=row.id,
        action=action,
        display_order=row.display_order,
        text=row.text,
        provenance_kind=row.provenance_kind,
        source_evidence_id=row.source_evidence_id,
        source_locator=row.source_locator,
        derivation_note=row.derivation_note,
        actor_user_id=actor.id,
        actor_username=actor.username,
        note=note,
        created_at=at,
    )
    session.add(revision)
    return revision


# --- the read path -----------------------------------------------------------


def load_concise_meanings(
    session: Session, entry_ids: Iterable[int]
) -> dict[int, list[EntryConciseMeaning]]:
    """Confirmed short meanings per entry, in display order.

    One query for all the entries in a response, so serving 50 words with short
    meanings costs one statement rather than fifty. Only ``confirmed`` rows are
    returned: this function is the single gate between the candidate store and every
    page, so there is no second place that has to remember the rule.
    """
    ids = list(dict.fromkeys(entry_ids))
    if not ids:
        return {}
    rows = session.scalars(
        select(EntryConciseMeaning)
        .where(
            EntryConciseMeaning.lexicon_entry_id.in_(ids),
            EntryConciseMeaning.status == STATUS_CONFIRMED,
        )
        .order_by(
            EntryConciseMeaning.lexicon_entry_id,
            EntryConciseMeaning.display_order,
        )
    ).all()
    grouped: dict[int, list[EntryConciseMeaning]] = {}
    for row in rows:
        grouped.setdefault(row.lexicon_entry_id, []).append(row)
    return grouped


def concise_meaning_dict(row: EntryConciseMeaning) -> dict[str, Any]:
    """What a client needs to show the value *and* to judge its provenance.

    ``is_source_verbatim`` is the field a client must branch on before presenting the
    text as the source's own words; it is derived from the stored kind rather than
    left for a client to infer from the label.
    """
    return {
        "text": row.text,
        "display_order": row.display_order,
        "provenance_kind": row.provenance_kind,
        "provenance_label": KIND_LABELS.get(row.provenance_kind, row.provenance_kind),
        "is_source_verbatim": row.provenance_kind == KIND_SOURCE,
        "is_supplement": row.provenance_kind == KIND_AI_SUPPLEMENT,
        "source_locator": row.source_locator,
        "derivation_note": row.derivation_note,
        "confirmed_by": row.confirmed_by_username,
        "confirmed_at": row.confirmed_at,
    }


def entry_short_meanings(
    session: Session, entry_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    """The serialised form the API returns, keyed by entry id."""
    return {
        entry_id: [concise_meaning_dict(row) for row in rows]
        for entry_id, rows in load_concise_meanings(session, entry_ids).items()
    }


def describe_entry(session: Session, entry: LexiconEntry) -> dict[str, Any]:
    """Everything a reviewer needs to judge one entry, for the CLI review step.

    Deliberately includes the untouched source fields beside the display values, so
    the reviewer can see for themselves that nothing was overwritten -- and that a
    long source meaning is still there under a short displayed one.
    """
    rows = session.scalars(
        select(EntryConciseMeaning)
        .where(EntryConciseMeaning.lexicon_entry_id == entry.id)
        .order_by(EntryConciseMeaning.display_order, EntryConciseMeaning.id)
    ).all()
    revisions = session.scalars(
        select(EntryConciseMeaningRevision)
        .where(EntryConciseMeaningRevision.lexicon_entry_id == entry.id)
        .order_by(EntryConciseMeaningRevision.id)
    ).all()
    return {
        "word": entry.word,
        "normalized_word": entry.normalized_word,
        "lexicon_entry_id": entry.id,
        "source_default_meanings": list(entry.source_meanings),
        "source_raw": entry.source_raw,
        "default_anchor": entry.default_anchor,
        "displayed": [concise_meaning_dict(row) for row in rows if row.is_displayable],
        "proposals": [
            {
                "id": row.id,
                "display_order": row.display_order,
                "text": row.text,
                "provenance_kind": row.provenance_kind,
                "provenance_label": KIND_LABELS.get(row.provenance_kind, row.provenance_kind),
                "source_locator": row.source_locator,
                "source_evidence_id": row.source_evidence_id,
                "derivation_note": row.derivation_note,
                "status": row.status,
                "proposed_by": row.proposed_by_username,
                "proposed_at": row.proposed_at,
                "confirmed_by": row.confirmed_by_username,
                "confirmed_at": row.confirmed_at,
            }
            for row in rows
        ],
        "history": [
            {
                "id": row.id,
                "action": row.action,
                "display_order": row.display_order,
                "text": row.text,
                "provenance_kind": row.provenance_kind,
                "actor": row.actor_username,
                "note": row.note,
                "created_at": row.created_at,
            }
            for row in revisions
        ],
    }


def find_entry(session: Session, lexicon_id: int, word: str) -> LexiconEntry | None:
    return session.scalar(
        select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon_id,
            LexiconEntry.normalized_word == normalize_word(word),
        )
    )
