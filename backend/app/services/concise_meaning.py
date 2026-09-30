"""Short study meanings: who proposed them, who confirmed them, what is shown.

The study page prefers one to three short, common, simplified senses **per part of
speech** over the full source text. Those senses are a **display value**, and this
module is the only place that writes them. Four rules shape everything below.

**Nothing is shown until a human confirms it.** A proposal is stored as a
``candidate``; the read path filters on ``confirmed``. So "an unconfirmed candidate
cannot reach a page" is not a habit any caller has to remember -- there is no code
path from a candidate to a response, and the database refuses to store a confirmed
row without a named person and a timestamp behind it.

**A position is a pair, not a number.** ``display_order`` is 1..3 *within a part of
speech*, so a slot is ``(pos_key, display_order)``. ``play`` is the case that forced
this: three verb senses plus one noun sense is four values, and the old per-word
three-slot shape could not hold them. Slot lookup, the supersede path and the
withdrawal path all key on the pair, so the same position in two groups is two slots
rather than a conflict.

**A part of speech is never inferred.** ``pos_source`` says where one came from: a
heading in the pinned revision (``pos_section``, with the heading's position recorded)
or a named human's judgement (``reviewer``, with the gloss line recorded). The
undetermined state is ``''``/``none``, and this module never fills it in from anything
else -- not from the text, not from the other proposals in the same call, and not from
a guess. ``confirm`` refuses an undetermined part of speech outright, so a candidate
proposed before this rule existed stays a candidate until a person classifies it.

**The source's own words are never overwritten.** Proposing and confirming touch
``entry_concise_meaning`` and its citations only. ``lexicon_entry.source_raw``,
``source_meanings`` and ``default_anchor`` are never written here, and neither is
``entry_source_evidence``: the point of the display value is that a reader who doubts
it can still go and read what the source actually said.

Wording carries its own provenance. ``source`` means the text is the source's own
value at a named position; ``derived`` means it is a documented change to one
(traditional to simplified, noise removed, re-worded, one sense extracted), which has
to say *what* was changed; ``ai_supplement`` means no source says this, which has to
say why it was added and is forbidden from carrying a source pointer at all -- neither
the primary locator nor a citation row. A supplement therefore cannot masquerade as a
quotation even if a later caller tries, because the shape is not representable.

Change is a withdrawal, not an edit. Replacing a displayed meaning is ``reject`` (with
a reason) followed by a fresh ``propose`` and ``confirm``. Nothing overwrites a value
in place, and every step appends a row to ``entry_concise_meaning_revision``, which this
module never updates or deletes. A revision snapshots the grouping that was in force,
so moving a value between groups shows up in the history rather than only in the
current row.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CONCISE_MEANING_KINDS,
    CONCISE_MEANING_LANGUAGES,
    CONCISE_MEANING_MAX_LENGTH,
    CONCISE_MEANING_MAX_SLOTS,
    CONCISE_MEANING_POS_KEYS,
    CONCISE_MEANING_POS_SOURCES,
    CONCISE_MEANING_POS_UNDETERMINED,
    EntryConciseMeaning,
    EntryConciseMeaningCitation,
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

POS_SOURCE_NONE = "none"
POS_SOURCE_SECTION = "pos_section"
POS_SOURCE_REVIEWER = "reviewer"

#: The language a displayed sense has to be in. These entries are English words, so a
#: sense taken from another language's section of the same source page must not be
#: displayable -- ``mutter`` carries Danish, Norwegian and Swedish noun senses beside
#: its English ones, and ``fertiliser``'s only Chinese gloss sits in a French verb
#: section. The empty string means "not recorded", which is also not a claim that the
#: sense is English, so confirmation requires the target language outright.
TARGET_LANGUAGE = "en"

#: What the study page calls each kind. Kept here rather than in the frontend so the
#: label and the stored value cannot drift apart.
KIND_LABELS: dict[str, str] = {
    KIND_SOURCE: "来源原文",
    KIND_DERIVED: "据来源改写",
    KIND_AI_SUPPLEMENT: "自拟补充",
}

#: What each way of establishing a part of speech is called on the review page.
POS_SOURCE_LABELS: dict[str, str] = {
    POS_SOURCE_NONE: "未定",
    POS_SOURCE_SECTION: "来源小节标题",
    POS_SOURCE_REVIEWER: "人工试判",
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
class ConciseMeaningCitationProposal:
    """One additional source position a proposed value rests on.

    Ordered from 1. A value may rest on more than one position and on more than one
    source: the trial record's ``decrease`` merges a zh.wiktionary line with a WikDict
    value. Each citation carries its own ``source_evidence_id``, which is the reason
    this is a list of its own rows rather than a delimiter-joined string.
    """

    citation_locator: str
    citation_order: int = 1
    source_evidence_id: int | None = None


@dataclass(frozen=True)
class ConciseMeaningProposal:
    """One short value proposed for one display slot of one part-of-speech group.

    ``source_locator`` is the human-readable source position
    (``zhwiktionary:9576029:15``) and is required for everything except a supplement.
    ``source_evidence_id`` is optional because it only exists once an import has
    recorded evidence for this entry; the locator is what keeps the value checkable
    either way.

    The part-of-speech fields default to "undetermined" rather than to a guess. A
    caller that omits them gets a storable candidate that ``confirm`` will refuse, not
    a value silently filed under a part of speech nobody chose.
    """

    text: str
    provenance_kind: str
    display_order: int
    source_locator: str = ""
    derivation_note: str = ""
    source_evidence_id: int | None = None
    pos_key: str = CONCISE_MEANING_POS_UNDETERMINED
    pos_label: str = ""
    pos_order: int = 1
    pos_source: str = POS_SOURCE_NONE
    pos_evidence_locator: str = ""
    language: str = ""
    citations: tuple[ConciseMeaningCitationProposal, ...] = field(default_factory=tuple)



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


@dataclass(frozen=True)
class _Checked:
    """One proposal after validation: trimmed, and with its group resolved.

    A named record rather than a growing tuple, because every one of these fields is
    read by both the validation and the write and a positional mix-up between
    ``source_locator`` and ``pos_evidence_locator`` would be invisible.
    """

    text: str
    locator: str
    note: str
    pos_key: str
    pos_label: str
    pos_order: int
    pos_source: str
    pos_evidence_locator: str
    language: str
    citations: tuple[ConciseMeaningCitationProposal, ...]


def _validate_pos(proposal: ConciseMeaningProposal) -> tuple[str, str, int, str, str]:
    """Check the part-of-speech group of one proposal.

    The undetermined state is legal here and only here: a candidate may be stored
    without a part of speech so that a reviewer has something to classify, and
    ``confirm`` is what refuses to display it. Nothing in this function ever fills the
    state in, which is the whole point -- a part of speech that nobody stated must not
    become a fact because a value needed one.
    """
    pos_key = proposal.pos_key.strip()
    pos_source = proposal.pos_source.strip()
    locator = proposal.pos_evidence_locator.strip()

    if pos_key not in ("", *CONCISE_MEANING_POS_KEYS):
        raise ConciseMeaningRefused(
            f"未知的词性 {pos_key!r}；只接受未定（空字符串）或闭集内的键："
            + "、".join(CONCISE_MEANING_POS_KEYS)
            + "。自由文本会让「名詞」「名词」「n.」「noun」变成四个组，"
            "同一词性就会被拆开显示。"
        )
    if pos_source not in CONCISE_MEANING_POS_SOURCES:
        raise ConciseMeaningRefused(
            f"未知的词性依据 {pos_source!r}；只接受 "
            + "、".join(CONCISE_MEANING_POS_SOURCES)
            + "。"
        )
    # The two are one state seen twice, so they have to agree in both directions: a key
    # with no stated source is a part of speech nobody vouched for, and a source with no
    # key is a provenance for nothing.
    if (pos_key == "") != (pos_source == POS_SOURCE_NONE):
        raise ConciseMeaningRefused(
            f"词性与词性依据不一致：pos_key={pos_key!r}、pos_source={pos_source!r}。"
            "未定词性必须同时是 pos_source='none'，已定词性必须说明依据"
            "（pos_section 或 reviewer）；不能只给其中一半。"
        )
    if pos_source == POS_SOURCE_NONE:
        if locator:
            raise ConciseMeaningRefused(
                "未定词性不得携带词性依据位置：没有依据，就不能写成有依据的样子。"
            )
    elif not locator:
        raise ConciseMeaningRefused(
            f"词性依据「{POS_SOURCE_LABELS[pos_source]}」必须记录位置："
            "pos_section 记来源小节的标题行（如 zhwiktionary:9576029:12），"
            "reviewer 记该释义本身的行；否则词性无法回查。"
        )
    if proposal.pos_order < 1:
        raise ConciseMeaningRefused(
            f"词性组序 {proposal.pos_order} 越界；组序从 1 开始，没有上界。"
        )
    return pos_key, proposal.pos_label.strip(), proposal.pos_order, pos_source, locator


def _validate_citations(
    proposal: ConciseMeaningProposal,
) -> tuple[ConciseMeaningCitationProposal, ...]:
    """Check the additional citations of one proposal.

    A supplement may not carry any: the primary locator is already forbidden for it, and
    a citation row would be the same claim made in a second place -- "no source says
    this" dressed as a quotation. The database constrains the primary pointer but cannot
    see across tables, so this check is the one that closes it.
    """
    citations = tuple(proposal.citations)
    if proposal.provenance_kind == KIND_AI_SUPPLEMENT and citations:
        raise ConciseMeaningRefused(
            "自拟补充不得携带附加引用：它没有来源原文，任何指向来源的引用都会把它"
            "写成引文。请去掉 citations。"
        )
    orders = []
    for citation in citations:
        locator = citation.citation_locator.strip()
        if not locator:
            raise ConciseMeaningRefused("附加引用必须记录来源位置，否则无法回查。")
        if citation.citation_order < 1:
            raise ConciseMeaningRefused(
                f"附加引用序号 {citation.citation_order} 越界；序号从 1 开始。"
            )
        orders.append(citation.citation_order)
    if len(set(orders)) != len(orders):
        raise ConciseMeaningRefused("同一条释义的附加引用序号不能重复。")
    return tuple(
        ConciseMeaningCitationProposal(
            citation_locator=citation.citation_locator.strip(),
            citation_order=citation.citation_order,
            source_evidence_id=citation.source_evidence_id,
        )
        for citation in citations
    )


def _validate(proposal: ConciseMeaningProposal) -> _Checked:
    """Check one proposal, returning it trimmed with its group resolved."""
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
            f"{text[:20]}…。学习页每个词性只显示 1–{CONCISE_MEANING_MAX_SLOTS} 个常见义项，"
            "完整释义请留在来源记录里。"
        )
    if not 1 <= proposal.display_order <= CONCISE_MEANING_MAX_SLOTS:
        raise ConciseMeaningRefused(
            f"展示位置 {proposal.display_order} 越界；每组只允许 1–{CONCISE_MEANING_MAX_SLOTS}。"
        )
    language = proposal.language.strip()
    if language not in CONCISE_MEANING_LANGUAGES:
        raise ConciseMeaningRefused(
            f"未知的语言标记 {language!r}；只接受空字符串（未记录）或 "
            + "、".join(repr(item) for item in CONCISE_MEANING_LANGUAGES if item)
            + "。其它语言的释义不得存为这个英文词的义项。"
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
    pos_key, pos_label, pos_order, pos_source, pos_evidence_locator = _validate_pos(proposal)
    return _Checked(
        text=text,
        locator=locator,
        note=note,
        pos_key=pos_key,
        pos_label=pos_label,
        pos_order=pos_order,
        pos_source=pos_source,
        pos_evidence_locator=pos_evidence_locator,
        language=language,
        citations=_validate_citations(proposal),
    )


def _refusal_reason(
    *,
    confirmed: bool,
    pos_key: str,
    pos_source: str,
    pos_evidence_locator: str,
    language: str,
    provenance_kind: str,
    citation_count: int,
) -> str:
    """The display rule, as a function of values rather than of a row.

    Deliberately values and not a model instance: the confirmation path has to ask the
    same question about a row that is still a candidate, and the obvious way to do that
    -- temporarily setting ``row.status`` and putting it back -- mutates a mapped
    attribute. SQLAlchemy then autoflushes that temporary value on the next query and
    the database refuses the write for a reason that has nothing to do with the value
    being judged. Taking the fields keeps the one rule in one place without touching
    state to ask about it.
    """
    if not confirmed:
        return "尚未人工确认"
    if pos_key.strip() == "" or pos_source == POS_SOURCE_NONE:
        return "词性未定"
    if pos_source not in CONCISE_MEANING_POS_SOURCES:
        return f"词性依据无效（{pos_source!r}）"
    if len(pos_evidence_locator.strip()) == 0:
        return "词性依据没有位置"
    if language != TARGET_LANGUAGE:
        return "语言未记录" if language == "" else f"语言不是目标语言（{language}）"
    if provenance_kind == KIND_AI_SUPPLEMENT and citation_count:
        return "自拟补充带着来源引用"
    return ""


def display_refusal_reason(row: EntryConciseMeaning) -> str:
    """Why this row must not be displayed, or ``""`` when it may be.

    The single statement of the display rule, used by both ends of the pipe: the read
    path withholds a row for exactly these reasons, and ``confirm`` refuses to create
    one. Two copies of the rule is how a row could be refused at confirmation and then
    shown anyway by a read path that had drifted.

    The last cases can only be reached by a row written before this rule existed or by a
    direct database write, which is why the read path checks them at all rather than
    trusting that confirmation already did.
    """
    return _refusal_reason(
        confirmed=row.status == STATUS_CONFIRMED,
        pos_key=row.pos_key,
        pos_source=row.pos_source,
        pos_evidence_locator=row.pos_evidence_locator,
        language=row.language,
        provenance_kind=row.provenance_kind,
        citation_count=len(row.citations),
    )


def display_refusal_reason_staged(row: EntryConciseMeaning) -> str:
    """The same rule applied to a row as though it had just been confirmed.

    Only the status is supplied rather than read: everything else -- undetermined part
    of speech, an invalid basis, a basis with no position, a language that is not the
    target, a supplement carrying citations -- is judged exactly as the read path judges
    it, so a row cannot be refused here and withheld there for a different reason.
    """
    return _refusal_reason(
        confirmed=True,
        pos_key=row.pos_key,
        pos_source=row.pos_source,
        pos_evidence_locator=row.pos_evidence_locator,
        language=row.language,
        provenance_kind=row.provenance_kind,
        citation_count=len(row.citations),
    )


def is_displayable(row: EntryConciseMeaning) -> bool:
    """True only when :func:`display_refusal_reason` has nothing to say."""
    return display_refusal_reason(row) == ""


def _slots(session: Session, entry_id: int) -> dict[tuple[str, int], EntryConciseMeaning]:
    """Every live (non-rejected) row of one entry, by ``(pos_key, display_order)``.

    The pair, not the number: ``display_order`` is a position *within* a part of speech,
    so a word's second group may legitimately use position 1. Keying on the number alone
    would make ``play``'s noun slot collide with its first verb slot.
    """
    rows = session.scalars(
        select(EntryConciseMeaning).where(
            EntryConciseMeaning.lexicon_entry_id == entry_id,
            EntryConciseMeaning.status != STATUS_REJECTED,
        )
    ).all()
    return {(row.pos_key, row.display_order): row for row in rows}


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

    The cap is ``CONCISE_MEANING_MAX_SLOTS`` **per part of speech**, and there is no cap
    on the call or on the word. ``play`` is why: three verb senses and one noun sense is
    four values for one word, and a per-call cap of three would either refuse a real
    word or make the caller drop one of its groups.

    Every check runs **before the first write**. ``ConciseMeaningRefused`` promises
    that a refusal leaves the database exactly as it was, and that has to include the
    other proposals of the same call: validating the first proposal, writing it, and
    then refusing the second would leave half of a rejected proposal behind -- and
    when the second one collides with a displayed value, would have carried out the
    withdrawal before discovering it had to refuse.
    """
    administrator = _require_administrator(actor)
    at = _moment(moment)

    checked = [_validate(proposal) for proposal in proposals]
    # A position is only a position within its group, so this is uniqueness per group
    # rather than across the word.
    slots = [(item.pos_key, proposal.display_order) for proposal, item in zip(proposals, checked)]
    if len(set(slots)) != len(slots):
        raise ConciseMeaningRefused(
            "同一个词性组内展示位置不能重复；不同词性组可以各自从第 1 位开始。"
        )
    per_group: dict[str, int] = {}
    for item in checked:
        per_group[item.pos_key] = per_group.get(item.pos_key, 0) + 1
    for pos_key, count in per_group.items():
        if count > CONCISE_MEANING_MAX_SLOTS:
            # Unreachable as the rules stand: ``display_order`` is already checked to be
            # 1..3 and the pair is already checked to be unique within the call, so a
            # fourth value in one group trips one of those first. It is kept anyway so
            # that widening the position rule -- the one product decision this cap
            # encodes -- cannot silently widen the group cap with it.
            label = pos_key or "（词性未定）"
            raise ConciseMeaningRefused(
                f"词性组 {label} 一次提交了 {count} 个义项，"
                f"每组最多 {CONCISE_MEANING_MAX_SLOTS} 个。"
            )

    occupied = _slots(session, entry.id)
    _require_group_agrees(session, entry, proposals=proposals, checked=checked)
    #: proposal, its checked form, and the live row in its slot (if any).
    prepared: list[tuple[ConciseMeaningProposal, _Checked, EntryConciseMeaning | None]] = []
    for proposal, item in zip(proposals, checked):
        evidence = _require_evidence_belongs_to_entry(
            session, entry, proposal.source_evidence_id
        )
        if proposal.provenance_kind == KIND_SOURCE:
            recorded_texts = (
                [evidence.raw_text] if evidence is not None
                else [*entry.source_meanings, entry.source_raw]
            )
            if not any(item.text in recorded for recorded in recorded_texts if recorded):
                raise ConciseMeaningRefused(
                    f"「{item.text}」未见于该词的来源原文，不能标为来源逐字内容。"
                    "若经过改写，请用 derived 并说明改动；若没有来源，请用 ai_supplement。"
                )
        for citation in item.citations:
            _require_evidence_belongs_to_entry(session, entry, citation.source_evidence_id)
        existing = occupied.get((item.pos_key, proposal.display_order))
        if existing is not None and existing.status == STATUS_CONFIRMED:
            label = item.pos_key or "（词性未定）"
            raise ConciseMeaningRefused(
                f"{label}组的第 {proposal.display_order} 位已经有一条已确认的释义"
                f"「{existing.text}」，不能直接覆盖。"
                "请先拒绝（撤回）它，再重新提交候选并确认。"
            )
        prepared.append((proposal, item, existing))

    created: list[EntryConciseMeaning] = []
    for proposal, item, existing in prepared:
        if existing is not None:
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
            text=item.text,
            provenance_kind=proposal.provenance_kind,
            source_evidence_id=proposal.source_evidence_id,
            source_locator=item.locator,
            derivation_note=item.note,
            status=STATUS_CANDIDATE,
            proposed_by_username=administrator.username,
            proposed_at=at,
            created_at=at,
            updated_at=at,
            pos_key=item.pos_key,
            pos_label=item.pos_label,
            pos_order=item.pos_order,
            pos_source=item.pos_source,
            pos_evidence_locator=item.pos_evidence_locator,
            language=item.language,
        )
        session.add(row)
        session.flush()
        for citation in item.citations:
            session.add(
                EntryConciseMeaningCitation(
                    concise_meaning_id=row.id,
                    citation_order=citation.citation_order,
                    citation_locator=citation.citation_locator,
                    source_evidence_id=citation.source_evidence_id,
                    created_at=at,
                )
            )
        session.flush()
        _record(
            session,
            row=row,
            entry=entry,
            action=ACTION_PROPOSED,
            actor=administrator,
            note=item.note,
            at=at,
        )
        created.append(row)
    session.flush()
    return created


def _require_group_agrees(
    session: Session,
    entry: LexiconEntry,
    *,
    proposals: Sequence[ConciseMeaningProposal],
    checked: Sequence[_Checked],
) -> None:
    """One part-of-speech group has one order, one stated basis and one language.

    ``pos_order`` lives on every row rather than in a group table, so this consistency
    is not something the database can express: two rows could claim the same
    ``pos_key`` at different positions and no constraint would object, which would make
    "which group shows first" depend on insertion order. It is therefore checked here,
    against both the other proposals in the call and the rows already stored for the
    entry -- otherwise a second ``propose`` call could silently renumber a group.
    """
    stated: dict[str, tuple[int, str, str, str]] = {}
    group_fields = ("pos_order", "pos_source", "pos_evidence_locator", "language")
    for item in checked:
        group = (
            item.pos_order,
            item.pos_source,
            item.pos_evidence_locator,
            item.language,
        )
        prior = stated.get(item.pos_key)
        if prior is not None and prior != group:
            field, old, new = next(
                (name, old, new)
                for name, old, new in zip(group_fields, prior, group)
                if old != new
            )
            raise ConciseMeaningRefused(
                f"词性组 {item.pos_key or '（词性未定）'} 本次提案内部不一致："
                f"{field} 前一条为 {old!r}，当前为 {new!r}。"
            )
        if prior is None:
            stated[item.pos_key] = group
    for pos_key, group in stated.items():
        if not pos_key:
            continue
        for other, other_group in stated.items():
            if other == pos_key:
                continue
            if other_group[0] == group[0]:
                raise ConciseMeaningRefused(
                    f"词性组序冲突：{pos_key} 与 {other} 都写第 {group[0]} 组。"
                    "同一词条内不同词性组必须各有不同组序。"
                )

    stored = session.scalars(
        select(EntryConciseMeaning).where(
            EntryConciseMeaning.lexicon_entry_id == entry.id,
            EntryConciseMeaning.status != STATUS_REJECTED,
        )
    ).all()
    for row in stored:
        group = stated.get(row.pos_key)
        if group is None or not row.pos_key:
            continue
        if (row.pos_order, row.pos_source, row.pos_evidence_locator, row.language) != group:
            raise ConciseMeaningRefused(
                f"词性组 {row.pos_key} 的既有行与本提案不一致："
                f"已存组序 {row.pos_order}／依据 {row.pos_source}／语言 {row.language!r}，"
                f"本次为 {group[0]}／{group[1]}／{group[3]!r}。"
                "同一组必须共用组序、依据与语言；要改变它们，请先撤回该组已显示的值。"
            )


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

    # Confirmation is where the display rules become a decision rather than a default.
    # The same function the read path uses decides, so a row cannot be refused here and
    # then withheld there for a different reason -- or worse, displayed because the two
    # rules had drifted apart.
    #
    # A row that is already confirmed is a no-op above, deliberately *before* this
    # check: withdrawing a displayed value would otherwise need a way to re-confirm it,
    # and re-running a review pass must stay safe.
    reason = display_refusal_reason_staged(meaning)
    if reason:
        raise ConciseMeaningRefused(_refusal_advice(meaning, reason))

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


def _refusal_advice(row: EntryConciseMeaning, reason: str) -> str:
    """A refusal that says what to do, not only what is wrong."""
    label = row.pos_key or "（未定）"
    advice = {
        "词性未定": (
            "该候选的词性未定，无法确认——学习页不显示没有词性的释义。"
            "请重新提交候选并写明 pos_key 与 pos_source"
            "（pos_section 需给出小节标题行，reviewer 表示人工试判并给出释义行）。"
            "本服务不会替你推断词性。"
        ),
        "词性依据没有位置": (
            f"词性 {label} 没有记录依据位置，无法回查。"
            "请重新提交候选并补上 pos_evidence_locator。"
        ),
        "语言未记录": (
            "该候选没有记录语言，无法确认——来源页面可能夹带其它语言的释义"
            "（丹麦语、法语等），没有语言标记就无法排除。"
            "请重新提交候选并写明 language='en'。"
        ),
        "自拟补充带着来源引用": (
            "自拟补充带着附加引用，无法确认：它没有来源原文，任何引用都会把它写成引文。"
            "请去掉 citations。"
        ),
    }
    if reason in advice:
        return advice[reason]
    if reason.startswith("语言不是目标语言"):
        return (
            f"该候选声明的语言不是目标语言（{row.language!r}），无法确认——"
            "本词条的释义必须是英文义项，其它语言小节的内容不得显示。"
        )
    if reason.startswith("词性依据无效"):
        return (
            f"词性依据 {row.pos_source!r} 不在允许集合内，无法确认。"
            "只接受 pos_section 或 reviewer。"
        )
    return f"该候选不满足展示条件（{reason}），无法确认。"


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
    """Append one history row. **Never** an UPDATE, and never a DELETE.

    The grouping is snapshotted alongside the wording. A value that moves from the noun
    group to the verb group changes what the study page shows, so the append-only record
    has to answer "who moved it, and when" -- reading only the current row would show
    the new group with no trace of the old one.
    """
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
        pos_key=row.pos_key,
        pos_order=row.pos_order,
        pos_source=row.pos_source,
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
    """Displayable short meanings per entry, in group then slot order.

    One query for all the entries in a response, so serving 50 words with short
    meanings costs one statement rather than fifty. This function is the single gate
    between the candidate store and every page, so there is no second place that has to
    remember the rule.

    A row is returned only when :func:`display_refusal_reason` has nothing to say about
    it. That is stricter than "status is confirmed", on purpose: a row can be confirmed
    and still not be displayable -- an undetermined part of speech, a basis with no
    position, a language that is not the target, a supplement carrying citations -- and
    the conservative answer for all of them is to show nothing rather than to show a
    value whose provenance cannot be checked. Rows written before this rule existed are
    exactly the ones that land here, so the read path is where the rule has to hold even
    if some other writer skipped it.

    Ordering is ``(pos_order, display_order, id)``. The pair is what makes the order
    defined: two groups may each have a first slot, and a client that sorts on
    ``display_order`` alone needs the server's order to already put the first group
    first, or the tie would be resolved by nothing.

    Citations are eager-loaded with ``selectinload`` so this stays a **fixed** number of
    statements -- two, however many entries the response carries -- rather than one
    statement per row for its citations. The docstring above said "one query" before
    citations existed; the property worth keeping is that the cost does not grow with
    the number of words, and lazy loading would have broken exactly that.
    """
    ids = list(dict.fromkeys(entry_ids))
    if not ids:
        return {}
    rows = session.scalars(
        select(EntryConciseMeaning)
        .options(selectinload(EntryConciseMeaning.citations))
        .where(
            EntryConciseMeaning.lexicon_entry_id.in_(ids),
            EntryConciseMeaning.status == STATUS_CONFIRMED,
        )
        .order_by(
            EntryConciseMeaning.lexicon_entry_id,
            EntryConciseMeaning.pos_order,
            EntryConciseMeaning.display_order,
            EntryConciseMeaning.id,
        )
    ).all()
    grouped: dict[int, list[EntryConciseMeaning]] = {}
    for row in rows:
        if display_refusal_reason(row):
            continue
        grouped.setdefault(row.lexicon_entry_id, []).append(row)
    return grouped


def concise_meaning_citation_dict(citation: EntryConciseMeaningCitation) -> dict[str, Any]:
    """One additional source position, with the position it names.

    ``source_evidence_id`` is the row the position resolved to at import time, or
    ``None``. It is reported as it was stored rather than resolved again: resolving it
    is the source block's job, and a second place that did it would be a second place
    that could pick the wrong row.
    """
    return {
        "citation_order": citation.citation_order,
        "citation_locator": citation.citation_locator,
        "source_evidence_id": citation.source_evidence_id,
    }


def concise_meaning_dict(row: EntryConciseMeaning) -> dict[str, Any]:
    """What a client needs to show one value *and* to judge its provenance.

    ``is_source_verbatim`` is the field a client must branch on before presenting the
    text as the source's own words; it is derived from the stored kind rather than
    left for a client to infer from the label.

    ``source_locator`` / ``source_evidence_id`` are the **primary** citation, and
    ``citations`` carries every additional one in order. A value can rest on more than
    one position and on more than one source -- ``decrease`` merges a zh.wiktionary line
    with a WikDict value -- so reporting only the primary would drop a position the
    reader needs in order to check the value. A self-authored supplement has neither,
    and that is what ``is_supplement`` tells a client to expect.
    """
    return {
        "text": row.text,
        "display_order": row.display_order,
        "provenance_kind": row.provenance_kind,
        "provenance_label": KIND_LABELS.get(row.provenance_kind, row.provenance_kind),
        "is_source_verbatim": row.provenance_kind == KIND_SOURCE,
        "is_supplement": row.provenance_kind == KIND_AI_SUPPLEMENT,
        "source_locator": row.source_locator,
        "source_evidence_id": row.source_evidence_id,
        "citations": [concise_meaning_citation_dict(item) for item in row.citations],
        "derivation_note": row.derivation_note,
        "confirmed_by": row.confirmed_by_username,
        "confirmed_at": row.confirmed_at,
    }


def concise_meaning_group_dict(rows: Sequence[EntryConciseMeaning]) -> dict[str, Any]:
    """One part-of-speech group, with its values in display order.

    The group is keyed on ``pos_key`` and ordered by ``pos_order``; a later group is a
    later element of the list rather than a field a client has to sort on.

    ``pos_label`` falls back to ``pos_key`` when nothing was recorded, so the promise
    "every group carries a display label" holds even for a group proposed without one.
    That is a presentation fallback for a value that is already the group's identity,
    not an inferred part of speech: the gate has already refused any row whose part of
    speech is undetermined.

    If rows of one ``pos_key`` ever disagreed about ``pos_order`` -- impossible through
    the service, which refuses a second call that would renumber a stored group -- the
    smallest order wins. The alternative, emitting the same key twice, would let one
    part of speech appear as two groups in the study page's list.
    """
    first = rows[0]
    pos_order = min(row.pos_order for row in rows)
    ordered = sorted(rows, key=lambda row: (row.display_order, row.id))
    return {
        "pos_key": first.pos_key,
        "pos_label": first.pos_label or first.pos_key,
        "pos_source": first.pos_source,
        "pos_source_label": POS_SOURCE_LABELS.get(first.pos_source, first.pos_source),
        "pos_order": pos_order,
        "meanings": [concise_meaning_dict(row) for row in ordered],
    }


def concise_meaning_groups(rows: Iterable[EntryConciseMeaning]) -> list[dict[str, Any]]:
    """The grouped form one entry's display values take in a response.

    Grouped by ``pos_key`` and sorted by the group's position, so the order is the
    server's decision rather than something each client has to re-derive.
    """
    buckets: dict[str, list[EntryConciseMeaning]] = {}
    for row in rows:
        buckets.setdefault(row.pos_key, []).append(row)
    groups = [concise_meaning_group_dict(group_rows) for group_rows in buckets.values()]
    groups.sort(key=lambda group: (group["pos_order"], group["pos_key"]))
    return groups


def entry_short_meanings(
    session: Session, entry_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    """The serialised form the API returns, keyed by entry id.

    Each entry maps to a list of part-of-speech groups; an entry with nothing
    displayable is simply absent, and every caller turns that into an empty list rather
    than filling it from ``source_meanings``.
    """
    return {
        entry_id: concise_meaning_groups(rows)
        for entry_id, rows in load_concise_meanings(session, entry_ids).items()
    }


def concise_meaning_review_dict(row: EntryConciseMeaning) -> dict[str, Any]:
    """The reviewer's view of one row: the display fields **and** its group.

    Separate from :func:`concise_meaning_dict` on purpose. That one is the API contract
    the study page already consumes, and this slice deliberately does not change the
    response shape -- so the part-of-speech fields are added here, where the only reader
    is the administrator's own review output.

    Not called ``review_dict``: ``app.api.helpers`` already exports a ``review_dict`` for
    a user's review event, and two functions with that name would be read as the same
    thing.
    """
    return {
        "id": row.id,
        "pos_key": row.pos_key,
        "pos_label": row.pos_label,
        "pos_order": row.pos_order,
        "pos_source": row.pos_source,
        "pos_source_label": POS_SOURCE_LABELS.get(row.pos_source, row.pos_source),
        "pos_evidence_locator": row.pos_evidence_locator,
        "language": row.language,
        "slot": [row.pos_key, row.display_order],
        "display_order": row.display_order,
        "text": row.text,
        "provenance_kind": row.provenance_kind,
        "provenance_label": KIND_LABELS.get(row.provenance_kind, row.provenance_kind),
        "source_locator": row.source_locator,
        "source_evidence_id": row.source_evidence_id,
        "derivation_note": row.derivation_note,
        "citations": [
            {
                "citation_order": citation.citation_order,
                "citation_locator": citation.citation_locator,
                "source_evidence_id": citation.source_evidence_id,
            }
            for citation in row.citations
        ],
        "status": row.status,
        "proposed_by": row.proposed_by_username,
        "proposed_at": row.proposed_at,
        "confirmed_by": row.confirmed_by_username,
        "confirmed_at": row.confirmed_at,
        "display_refusal_reason": display_refusal_reason(row),
    }


def describe_entry(session: Session, entry: LexiconEntry) -> dict[str, Any]:
    """Everything a reviewer needs to judge one entry, for the CLI review step.

    Deliberately includes the untouched source fields beside the display values, so
    the reviewer can see for themselves that nothing was overwritten -- and that a
    long source meaning is still there under a short displayed one.

    ``withheld`` is the part that makes the conservative read path reviewable rather
    than mysterious: a confirmed row the study page is *not* showing appears there with
    the reason, instead of silently vanishing from the report.
    """
    rows = session.scalars(
        select(EntryConciseMeaning)
        .where(EntryConciseMeaning.lexicon_entry_id == entry.id)
        .order_by(
            EntryConciseMeaning.pos_order,
            EntryConciseMeaning.display_order,
            EntryConciseMeaning.id,
        )
    ).all()
    revisions = session.scalars(
        select(EntryConciseMeaningRevision)
        .where(EntryConciseMeaningRevision.lexicon_entry_id == entry.id)
        .order_by(EntryConciseMeaningRevision.id)
    ).all()
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        if row.status == STATUS_REJECTED:
            continue
        key = (row.pos_key, row.pos_order)
        group = groups.setdefault(
            key,
            {
                "pos_key": row.pos_key,
                "pos_order": row.pos_order,
                "pos_source": row.pos_source,
                "pos_source_label": POS_SOURCE_LABELS.get(row.pos_source, row.pos_source),
                "pos_evidence_locator": row.pos_evidence_locator,
                "language": row.language,
                "slots_used": [],
                "confirmed_slots": [],
            },
        )
        group["slots_used"].append(row.display_order)
        if row.status == STATUS_CONFIRMED:
            group["confirmed_slots"].append(row.display_order)
    for group in groups.values():
        group["slots_used"].sort()
        group["confirmed_slots"].sort()
    return {
        "word": entry.word,
        "normalized_word": entry.normalized_word,
        "lexicon_entry_id": entry.id,
        "source_default_meanings": list(entry.source_meanings),
        "source_raw": entry.source_raw,
        "default_anchor": entry.default_anchor,
        "displayed": [concise_meaning_dict(row) for row in rows if is_displayable(row)],
        "withheld": [
            {"id": row.id, "text": row.text, "reason": display_refusal_reason(row)}
            for row in rows
            if row.status == STATUS_CONFIRMED and not is_displayable(row)
        ],
        "pos_groups": [groups[key] for key in sorted(groups)],
        "proposals": [concise_meaning_review_dict(row) for row in rows],
        "history": [
            {
                "id": row.id,
                "action": row.action,
                "pos_key": row.pos_key,
                "pos_order": row.pos_order,
                "pos_source": row.pos_source,
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
