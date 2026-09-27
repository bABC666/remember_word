"""The locked adjudication plan for a public lexicon import.

The joint preview (``app.services.public_lexicon_joint_preview``) answers "what do
these files contain, and where do they disagree". It is deliberately *not* a plan: it
does not tell a primary word list apart from a supplementary source, it does not
decide which of two conflicting meanings is authoritative, and its hash is not an
authorisation to write anything.

This module turns one joint preview plus one human adjudication file into a **locked
plan**: a single reproducible JSON artifact that

* freezes every input fingerprint -- manifest bytes, each source file's bytes, its
  field mapping, and the versions of the rules applied;
* fixes the candidate word set and its order from the ``primary`` source alone, so a
  supplementary source can never add a word to the public lexicon;
* records, per field, which evidence a human selected as the default -- or that a
  human explicitly declined to select one -- with a locator for every value;
* keeps every source's original text verbatim and grouped by source: two sources that
  disagree stay two pieces of evidence, and nothing is concatenated or overwritten;
* names the idempotency keys a later confirmation must use, so confirming the same
  file and mapping twice can only report "already imported";
* states plainly whether it may be confirmed at all, and why not.

It writes no database row, no ``LexiconEntry``, no ``UserWordState`` and no
``ReviewEvent``. It reads source files and one decisions file and writes one JSON
plan file. Confirmation is a separate, later slice that must re-verify all of this and
require an administrator's own current password; nothing produced here is an approval,
and ``plan_sha256`` is a content fingerprint rather than a credential.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.public_lexicon_joint_preview import (
    FIELD_ORDER,
    PROVENANCE_FIELDS,
    ManifestSpecs,
    load_manifest,
    missing_provenance_fields,
    preview_sources,
)
from app.services.public_lexicon_preview import FIELDS

PLAN_TYPE = "public_lexicon_import"
PLAN_FORMAT_VERSION = 1
DECISIONS_FORMAT_VERSION = 1

#: Rule versions are frozen into the plan: changing a rule changes what a plan means,
#: so a plan built under an older rule set must not be confirmed as if it were current.
NORMALIZATION_VERSION = "strip_casefold_v1"
CONFLICT_DETECTION_VERSION = "strip_distinct_v1"
CONFLICT_FIELDS = ("meaning", "phonetic", "part_of_speech")
MEMBERSHIP_RULE_VERSION = "primary_source_first_occurrence_v1"
SINGLE_VALUE_DEFAULT_RULE_VERSION = "agreeing_evidence_all_cited_v1"
SOURCE_RAW_RULE_VERSION = "primary_row_raw_line_v1"

#: Every planned source must declare one of these roles. ``exam_corpus`` is a real
#: role in the design but deliberately absent here: 2.9-B (考研真题词频) has neither a
#: verified corpus nor an agreed statistics contract, so letting a corpus file into a
#: plan would imply frequency data had been accepted when none has been.
ROLES = ("primary", "meaning", "phonetic", "exam_corpus")
PLANNED_ROLES = ("primary", "meaning", "phonetic")
PRIMARY_ROLE = "primary"

ACTION_SELECT = "select"
ACTION_NO_DEFAULT = "no_default"
ACTION_DEFER = "defer"
ACTION_EXCLUDE_WORD = "exclude_word"
ACTION_EXCLUDE_ROW = "exclude_row"
FIELD_ACTIONS = (ACTION_SELECT, ACTION_NO_DEFAULT, ACTION_DEFER)
DECISIONS = FIELD_ACTIONS + (ACTION_EXCLUDE_WORD, ACTION_EXCLUDE_ROW)

#: Only meanings may select more than one piece of evidence, because a word genuinely
#: has several senses. Phonetic and part-of-speech defaults stay single-valued.
MULTI_SELECT_FIELDS = frozenset({"meaning"})

#: Row-level issues that make a row unreadable: the row cannot be interpreted at all,
#: so it can only be acknowledged and excluded, never silently skipped.
UNREADABLE_ROW_CODES = frozenset({"csv_error", "column_count", "invalid_source_revision"})

MAX_DECISIONS_BYTES = 4 * 1024 * 1024
MAX_PLAN_BYTES = 32 * 1024 * 1024
MAX_REPORTED_DECISION_ERRORS = 20

#: Fields covered by the plan digest. Everything that decides *what would be written*
#: has to be in here, or editing it would not invalidate the digest. ``created_utc``,
#: ``run_id`` and ``manifest_file`` are deliberately outside:
#:
#: * two runs over identical inputs must produce the same ``plan_sha256``, which is
#:   what makes a repeated confirmation idempotent;
#: * what a manifest *means* -- each source's role, file bytes and mapping -- is
#:   already fully described by ``sources``. Hashing the manifest's own bytes as well
#:   would make re-ordering the source list, or re-indenting the JSON, look like a
#:   different import even though the write set is identical.
_DIGEST_FIELDS = (
    "format_version",
    "plan_type",
    "rule_versions",
    "sources",
    "joint_report_sha256",
    "required_fields",
    "target",
    "idempotency",
    "summary",
    "confirmation_ready",
    "confirmation_blockers",
    "unmatched_supplement_words",
    "entries",
)

_NOTES = [
    "本计划只读：它不打开应用数据库，不写 LexiconEntry、UserWordState 或 ReviewEvent。",
    "成员与顺序只来自 primary 来源；补充来源未匹配的词只出现在 unmatched_supplement_words，",
    "  不新增词条；同词不同释义按来源分组保留，既不拼接也不覆盖。",
    "默认值只由人工裁定（select）或唯一取值的无歧义证据产生；无歧义时引用该取值的全部证据，",
    "  不在地来源之间做静默取舍。未决冲突、缺必填字段、不可解析行都会阻断 confirmation_ready。",
    "plan_sha256 只覆盖决定“会写什么”的字段，因此相同输入重复生成得到相同摘要；它不含",
    "  created_utc/run_id，也不是管理员授权凭据——确认必须另行验证管理员本人身份与当前口令。",
    "manifest_file 只记录清单文件本身，不参与摘要：清单的语义（每个来源的角色、文件字节与映射）",
    "  已完整出现在 sources 里，重复计入会让重排来源顺序被误判成另一次导入。",
]


class PlanError(ValueError):
    """The plan cannot be built, or the artifact on disk is not a usable plan."""


def canonical_bytes(value: object) -> bytes:
    """Canonical JSON bytes: the one serialisation every digest here uses.

    The joint preview hashes its report the same way, so a plan can recompute the
    report hash it froze and prove the two agree.
    """
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(UTC)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _new_run_id(moment: datetime) -> str:
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"lex-{stamp}-{secrets.token_hex(4)}"


def _resolve_under_root(path: Path, *, source_root: Path, label: str) -> Path:
    """Resolve a CLI input inside the administrator's source directory.

    The same boundary the file preview enforces: the command must not be usable as a
    reader of arbitrary paths, and a symlink or junction that leaves the directory is
    refused rather than followed.
    """
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid_source_root: expected a directory")
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"outside_source_root: {label} leaves the source directory")
    if not resolved.is_file():
        raise ValueError(f"invalid_{label}: expected a regular file")
    return resolved


def mapping_sha256(mapping: dict[str, Any]) -> str:
    """Fingerprint one source's field mapping.

    The mapping decides which source column becomes which canonical field, so a plan
    is only reproducible while this hash is unchanged; the same file read through a
    different mapping is a different input and needs its own plan.

    Public because the confirmation step must recompute it from the source it
    re-reads and compare: the two hashes are only meaningful if both sides use the
    same serialisation.
    """
    return hashlib.sha256(canonical_bytes(mapping)).hexdigest()


def _source_idempotency_key(
    *, source_id: str, role: str, file_sha256: str, mapping_sha256: str
) -> str:
    return hashlib.sha256(canonical_bytes({
        "source_id": source_id,
        "role": role,
        "file_sha256": file_sha256,
        "mapping_sha256": mapping_sha256,
    })).hexdigest()


def evidence_idempotency_key(
    *, file_sha256: str, mapping_sha256: str, line: int, field: str, raw_value: str
) -> str:
    """Fingerprint one piece of evidence.

    The key is the design's unique key -- same file, same locator, same field, same
    value -- and deliberately does *not* contain ``source_id``. ``source_id`` is a
    label from a local manifest and proves nothing about a source's version or licence;
    keying on it would let the same bytes be imported twice under two labels.

    Public because the confirmation step records this exact value as
    ``entry_source_evidence.evidence_sha256``: the plan's key and the stored key have
    to be the same string for a re-import to be recognised as one.
    """
    return hashlib.sha256(canonical_bytes({
        "file_sha256": file_sha256,
        "mapping_sha256": mapping_sha256,
        "line": line,
        "field": field,
        "raw_value": raw_value,
    })).hexdigest()


def _idempotency_contract(target: str) -> dict[str, Any]:
    """The declared keys a confirmation must use, written into the plan itself.

    Stating them here rather than only in a document keeps the contract where the
    implementation that must honour it can read it, and lets a test assert the keys
    really are the ones this plan recorded.
    """
    return {
        "run": {
            "key": "plan_sha256",
            "value_is_in": "plan_sha256",
            "rule": "相同输入得到相同 plan_sha256；重试同一个摘要必须返回原结果，不重复写入",
        },
        "source": {
            "key_fields": ["source_id", "role", "file.sha256", "mapping_sha256"],
            "rule": "同一文件字节与同一映射已入库时只能报告跳过或冲突，不能再次添加证据",
        },
        "entry": {
            "key_fields": ["target.lexicon", "normalized_word"],
            "rule": "公共词条身份沿用 (lexicon_id, normalized_word) 唯一约束",
        },
        "evidence": {
            "key_fields": ["file.sha256", "mapping_sha256", "line", "field", "raw_value"],
            "rule": "不含 source_id：清单标识不证明来源版本，不能靠换标签重复入库",
        },
        "backstop": (
            "数据库唯一约束只是并发兜底，不代替冲突报告；同一文件或规则变化必须重新预览。"
        ),
        "target_lexicon": target,
    }


def load_decisions(path: Path, *, source_root: Path) -> dict[str, Any]:
    """Read the operator's adjudication file: the human half of the plan."""
    resolved = _resolve_under_root(path, source_root=source_root, label="decisions")
    with resolved.open("rb") as handle:
        raw = handle.read(MAX_DECISIONS_BYTES + 1)
    if len(raw) > MAX_DECISIONS_BYTES:
        raise ValueError(f"decisions_too_large: more than {MAX_DECISIONS_BYTES} bytes")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid_decisions: not readable JSON ({error})") from error
    if not isinstance(document, dict) or not isinstance(document.get("decisions"), list):
        raise TypeError("invalid_decisions: top level needs a decisions list")
    if document.get("format_version") != DECISIONS_FORMAT_VERSION:
        raise ValueError(
            "invalid_decisions: unsupported format_version "
            f"{document.get('format_version')!r}"
        )
    return document


def _validated_target(target_lexicon: str) -> str:
    if not isinstance(target_lexicon, str) or not target_lexicon.strip():
        raise ValueError("invalid_target_lexicon: name the public lexicon this plan targets")
    name = target_lexicon.strip()
    if len(name) > 120 or any(ord(char) < 32 for char in name):
        raise ValueError("invalid_target_lexicon: name is too long or contains controls")
    return name


def _roles_by_source(specs: ManifestSpecs) -> dict[str, str]:
    """Read the declared role of every source, requiring exactly one primary.

    The role is what keeps membership honest: only the primary source may create a
    word, and a supplementary source that disagrees stays evidence instead of becoming
    a second definition. A missing or unknown role is refused rather than defaulted,
    because "assume primary" would silently promote a supplement to a word list.
    """
    roles: dict[str, str] = {}
    for spec in specs.sources:
        if spec.role is None:
            raise ValueError(f"missing_role: source {spec.source_id!r} must declare a role")
        if spec.role not in ROLES:
            raise ValueError(
                f"invalid_role: source {spec.source_id!r} has unknown role {spec.role!r}"
            )
        if spec.role not in PLANNED_ROLES:
            raise ValueError(
                f"exam_corpus_not_supported: source {spec.source_id!r} declares role "
                "'exam_corpus', but 2.9-B (考研真题词频) has no verified corpus or "
                "agreed statistics contract and cannot take part in a plan"
            )
        roles[spec.source_id] = spec.role
    primary = [source_id for source_id, role in roles.items() if role == PRIMARY_ROLE]
    if len(primary) != 1:
        raise ValueError(
            "primary_source_count: expected exactly one source with role 'primary', "
            f"found {len(primary)}"
        )
    return roles


def _unreadable_rows(preview: dict[str, Any]) -> list[int]:
    lines: list[int] = []
    for row in preview["rows"]:
        if {issue["code"] for issue in row["issues"]} & UNREADABLE_ROW_CODES:
            lines.append(row["line"])
    return lines


def _distinct_values(evidence: list[dict[str, Any]]) -> list[str]:
    """The distinct non-empty values of one field, in first-seen order.

    ``strip()`` and exact comparison: the rule the joint preview already applies to
    meanings. Two spellings that differ only in surrounding whitespace are one value;
    anything else stays a conflict for a human to settle.
    """
    return list(dict.fromkeys(
        item["raw_value"].strip() for item in evidence if item["raw_value"].strip()
    ))


def _public_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """The decision as it is recorded in the plan: what was decided, and why."""
    return {
        key: decision[key]
        for key in ("action", "field", "evidence", "source_id", "line", "note")
        if key in decision
    }


class _DecisionBook:
    """The validated decisions file, indexed for lookup while entries are built.

    Validation collects every problem and raises once: an operator fixing an
    adjudication file should learn all of its mistakes in one run, not one per run.
    """

    def __init__(self, decisions: list[Any]) -> None:
        self.errors: list[str] = []
        self.words: dict[str, dict[str, Any]] = {}
        self.fields: dict[tuple[str, str], dict[str, Any]] = {}
        self.rows: dict[tuple[str, int], dict[str, Any]] = {}
        for index, decision in enumerate(decisions):
            self._accept(index, decision)

    def _reject(self, index: int, message: str) -> None:
        self.errors.append(f"decisions[{index}]: {message}")

    def _accept(self, index: int, decision: Any) -> None:
        if not isinstance(decision, dict):
            self._reject(index, "must be an object")
            return
        action = decision.get("action")
        if action not in DECISIONS:
            self._reject(index, f"unknown action {action!r}")
            return
        if action == ACTION_EXCLUDE_ROW:
            self._accept_row(index, decision, action)
            return
        word = decision.get("normalized_word")
        if not isinstance(word, str) or not word:
            self._reject(index, "needs a nonempty normalized_word")
            return
        if action == ACTION_EXCLUDE_WORD:
            self._accept_word(index, decision, word, action)
            return
        self._accept_field(index, decision, word, action)

    def _accept_row(self, index: int, decision: dict[str, Any], action: str) -> None:
        source_id = decision.get("source_id")
        line = decision.get("line")
        if not isinstance(source_id, str) or not source_id:
            self._reject(index, "exclude_row needs a nonempty source_id")
            return
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            self._reject(index, "exclude_row needs a positive integer line")
            return
        if not str(decision.get("note", "")).strip():
            self._reject(index, "exclude_row needs a note saying why the row is excluded")
            return
        key = (source_id, line)
        if key in self.rows:
            self._reject(index, f"duplicate exclude_row for {source_id} line {line}")
            return
        self.rows[key] = {"action": action, "source_id": source_id, "line": line,
                          "note": decision["note"]}

    def _accept_word(
        self, index: int, decision: dict[str, Any], word: str, action: str
    ) -> None:
        if "field" in decision or "evidence" in decision:
            self._reject(index, "exclude_word must not name a field or evidence")
            return
        if not str(decision.get("note", "")).strip():
            self._reject(index, "exclude_word needs a note saying why the word is excluded")
            return
        if word in self.words:
            self._reject(index, f"duplicate exclude_word for {word!r}")
            return
        self.words[word] = {"action": action, "note": decision["note"]}

    def _accept_field(
        self, index: int, decision: dict[str, Any], word: str, action: str
    ) -> None:
        field = decision.get("field")
        if field not in FIELDS:
            self._reject(index, f"unknown field {field!r}")
            return
        key = (word, field)
        if key in self.fields:
            self._reject(index, f"duplicate decision for {word!r} field {field!r}")
            return
        note = decision.get("note", "")
        if not isinstance(note, str):
            self._reject(index, "note must be a string")
            return
        record: dict[str, Any] = {"action": action, "field": field, "note": note}
        if action == ACTION_SELECT:
            locators = decision.get("evidence")
            if not isinstance(locators, list) or not locators:
                self._reject(index, "select needs a nonempty evidence list")
                return
            if field not in MULTI_SELECT_FIELDS and len(locators) != 1:
                self._reject(index, f"select on {field!r} takes exactly one evidence entry")
                return
            record["evidence"] = locators
        elif "evidence" in decision:
            self._reject(index, f"{action} must not carry evidence")
            return
        elif action == ACTION_NO_DEFAULT and field == "word":
            self._reject(index, "the 'word' field always has the primary spelling")
            return
        self.fields[key] = record

    def check_references(
        self,
        *,
        known_words: set[str],
        evidence_at: dict[tuple[str, str], set[tuple[str, int]]],
        unreadable_at: dict[str, set[int]],
    ) -> None:
        """Second pass: a locator can only be checked once the evidence is known.

        Stale references are errors rather than no-ops. A decision that silently did
        nothing would leave a word undecided while looking adjudicated, which is the
        one failure this artifact exists to prevent.
        """
        for word in sorted(self.words):
            if word not in known_words:
                self.errors.append(
                    f"exclude_word: {word!r} is not a word of the primary source"
                )
            if any(decision_word == word for decision_word, _field in self.fields):
                self.errors.append(
                    f"{word!r} is both excluded and adjudicated field by field"
                )
        for (source_id, line) in sorted(self.rows):
            if source_id not in unreadable_at:
                self.errors.append(f"exclude_row: {source_id!r} is not a declared source")
            elif line not in unreadable_at[source_id]:
                self.errors.append(
                    f"exclude_row: {source_id} line {line} is not an unreadable row"
                )
        for (word, field), record in sorted(self.fields.items()):
            if word not in known_words:
                self.errors.append(
                    f"{record['action']}: {word!r} is not a word of the primary source"
                )
                continue
            if record["action"] != ACTION_SELECT:
                continue
            available = evidence_at.get((word, field), set())
            seen: set[tuple[str, int]] = set()
            for locator in record["evidence"]:
                if not isinstance(locator, dict):
                    self.errors.append(f"select on {word!r}/{field}: evidence must be objects")
                    continue
                source_id, line = locator.get("source_id"), locator.get("line")
                if not isinstance(source_id, str) or not isinstance(line, int) or isinstance(line, bool):
                    self.errors.append(
                        f"select on {word!r}/{field}: evidence needs source_id and line"
                    )
                    continue
                key = (source_id, line)
                if key in seen:
                    self.errors.append(
                        f"select on {word!r}/{field}: {source_id} line {line} listed twice"
                    )
                    continue
                seen.add(key)
                if key not in available:
                    self.errors.append(
                        f"select on {word!r}/{field}: no non-empty evidence at "
                        f"{source_id} line {line}"
                    )

    def raise_for_errors(self) -> None:
        if not self.errors:
            return
        unique = sorted(set(self.errors))
        shown = unique[:MAX_REPORTED_DECISION_ERRORS]
        suffix = "" if len(unique) <= MAX_REPORTED_DECISION_ERRORS else "; ..."
        raise ValueError("invalid_decisions: " + "; ".join(shown) + suffix)


def plan_digest(plan: dict[str, Any]) -> str:
    """SHA-256 over the fields that decide what this plan would write."""
    body = {field: plan.get(field) for field in _DIGEST_FIELDS}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def build_plan(
    *,
    manifest_path: Path,
    source_root: Path,
    decisions_path: Path,
    target_lexicon: str,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the locked plan an operator reviews and a confirmation must match."""
    moment = _require_aware(now or datetime.now(UTC))
    target = _validated_target(target_lexicon)
    specs = load_manifest(manifest_path, source_root=source_root)
    roles = _roles_by_source(specs)
    report = preview_sources(
        specs.sources, source_root=source_root, required_fields=specs.required_fields
    )
    document = load_decisions(decisions_path, source_root=source_root)

    previews = {item["source_id"]: item["preview"] for item in report["sources"]}
    sources, duplicate_fingerprints = _source_blocks(specs, roles, previews)
    source_meta = {
        block["source_id"]: {
            "file_sha256": block["file"]["sha256"],
            "mapping_sha256": block["mapping_sha256"],
        }
        for block in sources
    }
    primary_id = next(
        source_id for source_id, role in roles.items() if role == PRIMARY_ROLE
    )
    ordered_words, primary_rows = _primary_membership(previews[primary_id], primary_id)

    #: Which revision each source row was read at. Empty for a manifest whose sources
    #: declare none, which is what keeps this invisible to every older plan.
    revisions = _row_revisions(previews)

    joint_entries = {entry["normalized_word"]: entry for entry in report["entries"]}
    evidence_at: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for word in ordered_words:
        for field in FIELD_ORDER:
            evidence_at[(word, field)] = {
                (item["source_id"], item["line"])
                for item in joint_entries[word]["fields"][field]
                if item["raw_value"].strip()
            }
    unreadable_at = {
        block["source_id"]: set(block["unreadable_rows"]) for block in sources
    }
    book = _DecisionBook(document["decisions"])
    book.check_references(
        known_words=set(ordered_words),
        evidence_at=evidence_at,
        unreadable_at=unreadable_at,
    )
    book.raise_for_errors()

    entries = [
        _entry(
            word=word,
            sequence=sequence,
            primary_row=primary_rows[word],
            joint_entry=joint_entries[word],
            source_meta=source_meta,
            revisions=revisions,
            book=book,
            required_fields=specs.required_fields,
        )
        for sequence, word in enumerate(ordered_words, start=1)
    ]
    unmatched = sorted(set(joint_entries) - set(ordered_words))

    plan: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "plan_type": PLAN_TYPE,
        "created_utc": _iso_z(moment),
        "run_id": run_id or _new_run_id(moment),
        "rule_versions": {
            "plan_format": PLAN_FORMAT_VERSION,
            "normalization": NORMALIZATION_VERSION,
            "conflict_detection": CONFLICT_DETECTION_VERSION,
            "membership": MEMBERSHIP_RULE_VERSION,
            "single_value_default": SINGLE_VALUE_DEFAULT_RULE_VERSION,
            "source_raw": SOURCE_RAW_RULE_VERSION,
        },
        "manifest_file": specs.file,
        "sources": sources,
        "joint_report_sha256": report["report_sha256"],
        "required_fields": [field for field in FIELD_ORDER if field in specs.required_fields],
        "target": {
            "lexicon": target,
            # The read-only slice never opens the database, so it cannot claim the
            # target exists, is public, or is empty. Confirmation has to check.
            "verified_against_database": False,
        },
        "idempotency": _idempotency_contract(target),
        "unmatched_supplement_words": unmatched,
        "entries": entries,
    }
    plan["summary"] = _summary(entries, sources, unmatched)
    plan["confirmation_blockers"] = _blockers(entries, sources, duplicate_fingerprints, book)
    if not plan["summary"]["ready_entries"] and not plan["confirmation_blockers"]:
        plan["confirmation_blockers"] = ["no_ready_entries"]
    plan["confirmation_ready"] = not plan["confirmation_blockers"]
    plan["notes"] = _NOTES
    plan["plan_sha256"] = plan_digest(plan)

    size = len(json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8"))
    if size > MAX_PLAN_BYTES:
        raise ValueError(f"plan_too_large: {size} bytes exceeds {MAX_PLAN_BYTES}")
    return plan


def _source_blocks(
    specs: ManifestSpecs, roles: dict[str, str], previews: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """One frozen block per source, plus any two that are literally the same input.

    Two sources reading the same bytes through the same mapping would contribute the
    same evidence twice; file fingerprints, not file names, are what identify that.
    """
    blocks: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for spec in sorted(specs.sources, key=lambda item: item.source_id):
        preview = previews[spec.source_id]
        mapping = preview["mapping"]
        mapping_sha = mapping_sha256(mapping)
        file_sha = str(preview["file"]["sha256"])
        fingerprint = (file_sha, mapping_sha)
        if fingerprint in seen:
            duplicates.append(
                f"duplicate_source_fingerprint: {spec.source_id!r} and "
                f"{seen[fingerprint]!r} read the same file bytes through the same mapping"
            )
        else:
            seen[fingerprint] = spec.source_id
        blocks.append({
            "source_id": spec.source_id,
            "role": roles[spec.source_id],
            # The manifest-relative path, not just the basename: the confirmation
            # step has to re-open the same file, and a manifest may name one in a
            # subdirectory. Recorded here because the plan is the confirm contract.
            "declared_path": str(spec.path),
            # The declared provenance, frozen into the digest. A plan is not only
            # "which values would be written" but "on whose authority", so changing a
            # licence declaration has to invalidate it. Missing fields are recorded
            # as empty strings and reported as a blocker rather than raising: the
            # plan stays a reviewable artifact that says what is still owed.
            "provenance": {
                field: (spec.provenance or {}).get(field, "")
                for field in PROVENANCE_FIELDS
            },
            "missing_provenance": missing_provenance_fields(spec.provenance),
            "file": dict(preview["file"]),
            "mapping": mapping,
            "mapping_sha256": mapping_sha,
            "summary": dict(preview["summary"]),
            "file_level_issues": list(preview["issues"]),
            "unreadable_rows": _unreadable_rows(preview),
            "idempotency_key": _source_idempotency_key(
                source_id=spec.source_id, role=roles[spec.source_id],
                file_sha256=file_sha, mapping_sha256=mapping_sha,
            ),
        })
    return blocks, duplicates


def _primary_membership(
    preview: dict[str, Any], primary_id: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """The word set and order, taken from the primary source alone.

    A repeated word keeps the *first* row's position, so a duplicated line cannot
    reorder the list; both rows still contribute evidence, and if they disagree the
    entry is blocked until a human decides.
    """
    ordered: list[str] = []
    meta: dict[str, dict[str, Any]] = {}
    for row in preview["rows"]:
        word = row["normalized_word"]
        if not word or word in meta:
            continue
        ordered.append(word)
        meta[word] = {"source_id": primary_id, "line": row["line"], "raw": row["raw"]}
    return ordered, meta


def _row_revisions(
    previews: dict[str, dict[str, Any]]
) -> dict[tuple[str, int], str]:
    """The pinned revision of every source row that has one, keyed by its locator.

    Read from what the file preview already recorded rather than from the manifest:
    the preview is where the declaration and the file were combined, so a revision
    here is one that was actually read out of the bytes this plan froze.

    A source that declares no revision contributes **no keys at all**, and that is what
    keeps a plan built from an old manifest byte-identical to what it was before this
    existed. An empty *value* under a declared revision is kept, because "this row has
    no revision" is a fact the plan has to carry and is not the same fact as "this
    source has none" -- the first still says which column to trust, the second says
    there is no column.
    """
    return {
        (source_id, row["line"]): row["source_revision"]
        for source_id, preview in previews.items()
        for row in preview["rows"]
        if "source_revision" in row
    }


def _entry(
    *,
    word: str,
    sequence: int,
    primary_row: dict[str, Any],
    joint_entry: dict[str, Any],
    source_meta: dict[str, dict[str, str]],
    revisions: dict[tuple[str, int], str],
    book: _DecisionBook,
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    """One candidate entry: evidence, the human's decisions, and what is still open."""
    evidence: dict[str, list[dict[str, Any]]] = {}
    for field in FIELD_ORDER:
        items: list[dict[str, Any]] = []
        for item in joint_entry["fields"][field]:
            located: dict[str, Any] = {
                "source_id": item["source_id"],
                "line": item["line"],
                "raw_value": item["raw_value"],
                "idempotency_key": evidence_idempotency_key(
                    file_sha256=source_meta[item["source_id"]]["file_sha256"],
                    mapping_sha256=source_meta[item["source_id"]]["mapping_sha256"],
                    line=item["line"], field=field, raw_value=item["raw_value"],
                ),
            }
            revision = revisions.get((item["source_id"], item["line"]))
            if revision is not None:
                # Frozen beside the value it belongs to, and therefore covered by
                # ``plan_sha256``: which revision a value came from is part of what this
                # plan claims, so editing it has to invalidate the plan rather than
                # change the claim quietly. A declaration-free source adds no key at
                # all, so an old manifest keeps producing exactly the plan it did.
                located["source_revision"] = revision
            items.append(located)
        evidence[field] = items
    conflicts = [
        {
            "field": field,
            "raw_values": _distinct_values(evidence[field]),
            "evidence": [
                {"source_id": item["source_id"], "line": item["line"]}
                for item in evidence[field]
                if item["raw_value"].strip()
            ],
        }
        for field in CONFLICT_FIELDS
        if len(_distinct_values(evidence[field])) > 1
    ]
    primary_locator = {
        "source_id": primary_row["source_id"], "line": primary_row["line"]
    }
    decisions = [
        _public_decision(record)
        for (_decision_word, _field), record in sorted(book.fields.items())
        if _decision_word == word
    ]

    exclusion = book.words.get(word)
    if exclusion is not None:
        return {
            "normalized_word": word,
            "sequence": sequence,
            "status": "excluded",
            "block_reasons": [],
            "primary": dict(primary_locator),
            "conflicts": conflicts,
            "decisions": [_public_decision(exclusion), *decisions],
            "default_snapshot": None,
            "default_evidence": {**{field: [] for field in FIELD_ORDER},
                                 "source_raw": None},
            "evidence": evidence,
        }

    primary_evidence = _primary_evidence(evidence["word"], primary_locator)
    if primary_evidence is None:
        # Membership came from this row, so its word evidence must exist; if it does
        # not, the preview and the plan disagree and writing anything would be unsafe.
        raise PlanError(
            f"internal_inconsistency: no word evidence for {word!r} at the primary row"
        )
    defaults, default_evidence, block_reasons = _resolve_defaults(
        word=word, evidence=evidence, conflicts=conflicts, book=book,
        required_fields=required_fields,
        primary_word=str(primary_evidence["raw_value"]),
    )
    default_evidence["source_raw"] = dict(primary_locator)
    return {
        "normalized_word": word,
        "sequence": sequence,
        "status": "blocked" if block_reasons else "ready",
        "block_reasons": sorted(block_reasons),
        "primary": dict(primary_locator),
        "conflicts": conflicts,
        "decisions": decisions,
        "default_snapshot": {
            "word": defaults["word"][0],
            "phonetic": defaults["phonetic"][0] if defaults["phonetic"] else "",
            "part_of_speech": (
                defaults["part_of_speech"][0] if defaults["part_of_speech"] else ""
            ),
            "source_meanings": list(defaults["meaning"]),
            # The original text of the primary row: never rewritten, never assembled
            # from several sources. Which row it is stays visible in default_evidence.
            "source_raw": primary_row["raw"],
        },
        "default_evidence": default_evidence,
        "evidence": evidence,
    }


def _primary_evidence(
    evidence: list[dict[str, Any]], locator: dict[str, Any]
) -> dict[str, Any] | None:
    for item in evidence:
        if item["source_id"] == locator["source_id"] and item["line"] == locator["line"]:
            return item
    return None


def _resolve_defaults(
    *,
    word: str,
    evidence: dict[str, list[dict[str, Any]]],
    conflicts: list[dict[str, Any]],
    book: _DecisionBook,
    required_fields: tuple[str, ...],
    primary_word: str,
) -> tuple[dict[str, list[str]], dict[str, Any], list[str]]:
    """Turn decisions and unambiguous evidence into per-field default values.

    A field only auto-selects when every non-empty value agrees: with one candidate
    there is nothing to choose, so citing all of its rows makes no silent pick between
    sources. Two different values never resolve themselves -- that is the conflict a
    human has to settle.
    """
    conflict_fields = {conflict["field"] for conflict in conflicts}
    defaults: dict[str, list[str]] = {field: [] for field in FIELD_ORDER}
    defaults["word"] = [primary_word]
    default_evidence: dict[str, Any] = {field: [] for field in FIELD_ORDER}
    default_evidence["source_raw"] = None
    block_reasons: list[str] = []

    for field in FIELD_ORDER:
        values = _distinct_values(evidence[field])
        if field == "word":
            # The primary source's own spelling is the default; a human may override it
            # with any spelling that actually appears as evidence.
            default_evidence["word"] = [{"source_id": item["source_id"], "line": item["line"]}
                                        for item in evidence["word"]
                                        if item["raw_value"] == primary_word]
            continue
        decision = book.fields.get((word, field))
        if decision is not None:
            action = decision["action"]
            if action == ACTION_DEFER:
                block_reasons.append(f"deferred_decision:{field}")
                continue
            if action == ACTION_NO_DEFAULT:
                if field in required_fields:
                    block_reasons.append(f"no_default_on_required_field:{field}")
                continue
            locators = {(item["source_id"], item["line"]): item for item in evidence[field]}
            for locator in decision["evidence"]:
                chosen = locators[(locator["source_id"], locator["line"])]
                value = chosen["raw_value"].strip()
                if value not in defaults[field]:
                    defaults[field].append(value)
                default_evidence[field].append(
                    {"source_id": chosen["source_id"], "line": chosen["line"]}
                )
            continue
        if field in conflict_fields:
            block_reasons.append(f"undecided_conflict:{field}")
            continue
        if not values:
            if field in required_fields:
                block_reasons.append(f"missing_required_field:{field}")
            continue
        defaults[field] = [values[0]]
        default_evidence[field] = [
            {"source_id": item["source_id"], "line": item["line"]}
            for item in evidence[field] if item["raw_value"].strip() == values[0]
        ]

    override = book.fields.get((word, "word"))
    if override is not None and override["action"] == ACTION_SELECT:
        chosen = override["evidence"][0]
        selected = next(
            item for item in evidence["word"]
            if item["source_id"] == chosen["source_id"] and item["line"] == chosen["line"]
        )
        defaults["word"] = [selected["raw_value"]]
        default_evidence["word"] = [
            {"source_id": selected["source_id"], "line": selected["line"]}
        ]
    elif override is not None and override["action"] == ACTION_DEFER:
        block_reasons.append("deferred_decision:word")
    return defaults, default_evidence, block_reasons


def _summary(
    entries: list[dict[str, Any]], sources: list[dict[str, Any]], unmatched: list[str]
) -> dict[str, Any]:
    return {
        "sources": len(sources),
        "candidate_entries": len(entries),
        "ready_entries": sum(entry["status"] == "ready" for entry in entries),
        "blocked_entries": sum(entry["status"] == "blocked" for entry in entries),
        "excluded_entries": sum(entry["status"] == "excluded" for entry in entries),
        "entries_with_conflicts": sum(bool(entry["conflicts"]) for entry in entries),
        "unmatched_supplement_words": len(unmatched),
        "unreadable_rows": sum(len(source["unreadable_rows"]) for source in sources),
        "sources_with_file_level_issues": sum(
            bool(source["file_level_issues"]) for source in sources
        ),
    }


def _blockers(
    entries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    duplicate_fingerprints: list[str],
    book: _DecisionBook,
) -> list[str]:
    """Everything that must stop a confirmation, in machine-readable form.

    The rules are the design's: an unadjudicated conflict, a missing required field,
    an unusable source and an unacknowledged bad row all block by default. A human may
    settle a conflict, or exclude a word or a row deliberately; nothing is skipped
    merely because skipping would let the import finish.
    """
    blockers = list(duplicate_fingerprints)
    for source in sources:
        if source["file_level_issues"]:
            blockers.append(f"source_file_unusable:{source['source_id']} produced no rows")
        if source["missing_provenance"]:
            # The design is explicit that an import without a settled licence may be
            # evaluated locally but must not be published. Blocks by default, exactly
            # like an unadjudicated conflict.
            blockers.append(
                f"incomplete_provenance:{source['source_id']} missing "
                + ", ".join(source["missing_provenance"])
            )
        unacknowledged = [
            line for line in source["unreadable_rows"]
            if (source["source_id"], line) not in book.rows
        ]
        if unacknowledged:
            blockers.append(
                f"unacknowledged_bad_rows:{source['source_id']} "
                f"{len(unacknowledged)} row(s) need an exclude_row decision"
            )
    blocked = sum(entry["status"] == "blocked" for entry in entries)
    if blocked:
        blockers.append(f"blocked_entries: {blocked} entr(y/ies) still need a decision")
    return sorted(blockers)


def write_plan(plan: dict[str, Any], path: Path) -> Path:
    """Write the plan once, refusing to overwrite previous evidence."""
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise PlanError("plan digest does not match its contents")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as target:
        json.dump(plan, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    return path


def load_plan(path: Path) -> dict[str, Any]:
    """Read a plan back and prove it is unmodified before anyone acts on it."""
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanError(f"plan file cannot be read: {path} ({error})") from error
    if not isinstance(plan, dict):
        raise PlanError("not a plan: top level must be an object")
    if plan.get("plan_type") != PLAN_TYPE:
        raise PlanError("not a public lexicon import plan")
    if plan.get("format_version") != PLAN_FORMAT_VERSION:
        raise PlanError(f"unsupported plan format {plan.get('format_version')!r}")
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise PlanError("plan digest does not match its contents")
    return plan
