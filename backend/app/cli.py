"""Operator CLI.

Passwords are read with :func:`getpass.getpass` and from nowhere else. There is
deliberately no ``--password`` option: command-line arguments are readable by any
process on the machine (``ps``, Task Manager, the shell's own history), so a
password supplied that way is already disclosed before the program starts. The
same reasoning rules out an environment variable.

Account writes are available to tooling through :func:`set_password_for_user` and
:func:`create_user_account`, which take the password as a plain argument and are
never reachable from a command line.

Usage::

    python -m app.cli list-users
    python -m app.cli set-password admin
    python -m app.cli create-user userb
    python -m app.cli promote userb
    python -m app.cli prune-sessions
    python -m app.cli public-lexicon preview synthetic.csv --source-root sources --map word=head
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import get_settings
from app.db import (
    database_path_from_url,
    get_engine,
    get_session_factory,
    verify_schema_revision,
)
from app.history_retention_preview import preview_history_retention
from app.models import User, UserSession, UserSettings
from app.security import hash_password, password_is_usable
from app.services.auth import (
    normalize_username,
    prune_sessions,
    verify_user_password,
)
from app.services.concise_meaning import (
    CONCISE_MEANING_KINDS,
    CONCISE_MEANING_MAX_SLOTS,
    CONCISE_MEANING_POS_KEYS,
    POS_SOURCE_LABELS,
    ConciseMeaningCitationProposal,
    ConciseMeaningProposal,
    ConciseMeaningRefused,
)
from app.services.public_lexicon_joint_preview import preview_manifest
from app.services.public_lexicon_preview import PreviewMapping, preview_file
from app.testing_guards import assert_not_real_data

MIN_PASSWORD_LENGTH = 8


def read_new_password(prompt: str) -> str:
    """Read a new password from the terminal, twice, and check its length.

    Confirmation is asked for even when the first entry is empty, so an accidental
    Enter cannot be mistaken for "no change".
    """
    password = getpass.getpass(prompt)
    confirmation = getpass.getpass("请再次输入以确认：")
    if password != confirmation:
        raise SystemExit("两次输入不一致，未做任何修改。")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符，未做任何修改。")
    return password


def _open_session():
    # Uses the lazily created session factory so this works regardless of when
    # the engine was first bound.
    return get_session_factory()()


def _find_user(session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == normalize_username(username)))


def command_list_users(_args: argparse.Namespace) -> int:
    with _open_session() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        if not users:
            print("数据库中没有用户。")
            return 0
        print(f"{'id':<4} {'username':<20} {'role':<8} {'active':<7} password")
        for user in users:
            configured = "已设置" if password_is_usable(user.password_hash) else "未设置"
            print(
                f"{user.id:<4} {user.username:<20} {user.role:<8} "
                f"{user.is_active!s:<7} {configured}"
            )
    return 0


def set_password_for_user(session, username: str, password: str) -> bool:
    """Set an existing account's password. Returns False if it does not exist.

    The caller has already obtained ``password``; this function never prints it,
    logs it, or stores it anywhere but the Argon2id hash.
    """
    user = _find_user(session, username)
    if user is None:
        print(f"用户不存在：{username}", file=sys.stderr)
        return False
    user.password_hash = hash_password(password)
    session.add(user)
    session.commit()
    print(f"已为 {user.username} 设置密码（哈希已保存，明文未落库）。")
    return True


def create_user_account(
    session,
    username: str,
    password: str,
    *,
    role: str = "user",
    display_name: str = "",
) -> bool:
    """Create an account, or return False if the name is taken or empty."""
    normalized = normalize_username(username)
    if not normalized:
        print("用户名不能为空", file=sys.stderr)
        return False
    if _find_user(session, normalized) is not None:
        print(f"用户名已存在：{normalized}", file=sys.stderr)
        return False
    user = User(
        username=normalized,
        display_name=display_name or normalized,
        role=role,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    session.add(UserSettings(user_id=user.id))
    session.commit()
    print(f"已创建用户 {user.username}（角色 {user.role}，id {user.id}）。")
    return True


def command_set_password(args: argparse.Namespace) -> int:
    password = read_new_password(f"为 {args.username} 设置新密码：")
    with _open_session() as session:
        return 0 if set_password_for_user(session, args.username, password) else 1


def command_create_user(args: argparse.Namespace) -> int:
    username = normalize_username(args.username)
    if not username:
        print("用户名不能为空", file=sys.stderr)
        return 1
    password = read_new_password(f"为 {username} 设置初始密码：")
    with _open_session() as session:
        created = create_user_account(
            session,
            username,
            password,
            role=args.role,
            display_name=args.display_name,
        )
    return 0 if created else 1


def command_promote(args: argparse.Namespace) -> int:
    with _open_session() as session:
        user = _find_user(session, args.username)
        if user is None:
            print(f"用户不存在：{args.username}", file=sys.stderr)
            return 1
        user.role = args.role
        session.add(user)
        session.commit()
        print(f"{user.username} 的角色已设为 {user.role}。")
    return 0


def command_prune_sessions(_args: argparse.Namespace) -> int:
    """Delete sessions that can never be accepted again.

    The startup path already runs this; the command exists so an operator can do
    it deliberately, and so its effect can be seen rather than assumed. Valid
    sessions are never deleted -- see ``prune_sessions`` for the rule.
    """
    with _open_session() as session:
        removed = prune_sessions(session)
        remaining = session.scalar(select(func.count()).select_from(UserSession)) or 0
    print(f"已清理 {removed} 条失效会话（已撤销／已过期／闲置超时），保留 {remaining} 条。")
    return 0


def command_public_lexicon_preview(args: argparse.Namespace) -> int:
    """Print a source-file report without opening an application database."""
    columns: dict[str, str] = {}
    for item in args.field_map:
        field, separator, column = item.partition("=")
        if not separator or not field or not column or field in columns:
            print(f"无效或重复的字段映射：{item}", file=sys.stderr)
            return 2
        columns[field] = column
    try:
        mapping = PreviewMapping(
            columns=columns,
            required_fields=tuple(dict.fromkeys(("word", *args.required))),
            encoding=args.encoding,
            delimiter=args.delimiter,
        )
        report = preview_file(args.file, mapping, source_root=args.source_root)
    except (LookupError, OSError, ValueError) as error:
        print(f"公共词库预览失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_public_lexicon_preview_many(args: argparse.Namespace) -> int:
    """Print a deterministic joint report without opening an application database."""
    try:
        report = preview_manifest(args.manifest, source_root=args.source_root)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        print(f"公共词库联合预览失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_migration_status(args: argparse.Namespace) -> int:
    """Report whether the database matches this code, without touching either.

    Read-only by construction: the database is opened with ``mode=ro`` and nothing is
    created, not even a data directory. The launcher runs this before it starts the
    server, so that "the schema is behind the code and there are real rows in it" is
    a decision it can make and explain rather than a migration it performs blind.

    Prints JSON; exits 0 whenever a state could be established (including "refuse"),
    and 2 only when the inputs themselves were unusable.
    """
    from app.db import migration_state, schema_action

    path = args.database
    if path is None:
        try:
            base_dir = None
            if args.env_file is not None:
                from dotenv import load_dotenv

                env_file = args.env_file.resolve()
                if not env_file.is_file():
                    raise FileNotFoundError(env_file)
                load_dotenv(env_file, override=False)
                base_dir = env_file.parent
            path = _resolved_database_path(base_dir=base_dir)
        except Exception as error:  # noqa: BLE001 -- report, do not guess
            print(f"无法确定数据库路径：{error}", file=sys.stderr)
            return 2

    state = migration_state(path)
    action, reason = schema_action(state)
    payload = {**state.as_dict(), "action": action, "reason": reason}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _resolved_database_path(*, base_dir: Path | None = None) -> Path:
    """The database this checkout would actually use, without creating anything.

    Deliberately not ``get_settings()``: that creates the data directory, and a
    status check must be able to run against a path that does not exist yet.
    """
    project_root = Path(__file__).resolve().parents[2]
    base_dir = base_dir or Path.cwd()
    data_dir = Path(os.getenv("VOCAB_DATA_DIR", str(project_root / "data")))
    if not data_dir.is_absolute():
        data_dir = base_dir / data_dir
    data_dir = data_dir.resolve()
    explicit = os.getenv("VOCAB_DATABASE_PATH", "").strip()
    if explicit:
        database = Path(explicit)
        return (base_dir / database).resolve() if not database.is_absolute() else database.resolve()
    return data_dir / "vocab.db"


def command_public_lexicon_plan(args: argparse.Namespace) -> int:
    """Write the locked, read-only adjudication plan without opening a database.

    The plan is a review artifact, not an authorisation: this command cannot confirm an
    import, and it says out loud when a plan is not confirmable instead of leaving that
    to be inferred from a summary nobody re-reads.
    """
    from app.services.public_lexicon_plan import PlanError, build_plan, write_plan

    try:
        plan = build_plan(
            manifest_path=args.manifest,
            source_root=args.source_root,
            decisions_path=args.decisions,
            target_lexicon=args.target_lexicon,
        )
        write_plan(plan, args.plan_path)
    except (OSError, UnicodeError, ValueError, TypeError, PlanError) as error:
        print(f"公共词库锁定计划生成失败：{error}", file=sys.stderr)
        return 2

    summary = plan["summary"]
    print("公共词库锁定计划（只读）：本命令不写词条、不创建学习状态、不打开应用数据库")
    print(f"计划文件: {args.plan_path}")
    print(f"运行 ID: {plan['run_id']}")
    print(f"计划摘要 SHA-256: {plan['plan_sha256']}")
    print(f"来源数: {summary['sources']}；候选词条: {summary['candidate_entries']}"
          f"（可确认 {summary['ready_entries']} / 待裁定 {summary['blocked_entries']}"
          f" / 已排除 {summary['excluded_entries']}）")
    print(f"联合报告 SHA-256: {plan['joint_report_sha256']}")
    print(f"补充来源未匹配词: {summary['unmatched_supplement_words']}"
          f"；不可解析行: {summary['unreadable_rows']}")
    if plan["confirmation_ready"]:
        print("confirmation_ready: True —— 计划本身已裁定完毕，但确认入库仍需另一次"
              "管理员本人身份与当前口令复核")
        return 0
    print("confirmation_ready: False —— 本计划不可确认，先解决以下阻断项：")
    for blocker in plan["confirmation_blockers"]:
        print(f"  - {blocker}")
    if args.require_ready:
        return 1
    print("提示：加 --require-ready 可使未就绪的计划以非零退出码结束。")
    return 0


def command_public_lexicon_preflight_target(args: argparse.Namespace) -> int:
    """Print target differences without application setup or database writes."""
    import sqlite3

    from app.services.public_lexicon_confirm import ConfirmRefused
    from app.services.public_lexicon_plan import PlanError, load_plan
    from app.services.public_lexicon_target_preflight import preflight_target

    try:
        plan = load_plan(args.plan_path)
        report = preflight_target(args.database, plan=plan, source_root=args.source_root)
    except (
        OSError, ValueError, TypeError, KeyError, sqlite3.Error, PlanError, ConfirmRefused,
    ) as error:
        print(f"目标公共词库预检失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["technical_preflight_passed"] else 1


def command_public_lexicon_confirm(args: argparse.Namespace) -> int:
    """Confirm a locked plan as an administrator, writing public content only.

    The password is read with ``getpass`` and from nowhere else, for the reason this
    module's docstring gives: an argument or an environment variable is already
    disclosed before the program starts. There is no cross-invocation attempt budget
    here on purpose -- each run is a new process, so a counter would reset every time
    and prove nothing; the operator already has filesystem access to the database, and
    what this gate is for is making the confirmation deliberate and attributable.
    """
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import PlanError, load_plan

    try:
        plan = load_plan(args.plan_path)
    except (OSError, ValueError, PlanError) as error:
        print(f"计划文件无法读取或校验失败：{error}", file=sys.stderr)
        return 2

    summary = plan["summary"]
    print("公共词库管理员确认写入（只写公共内容层；不建学习状态；单事务）")
    print(f"计划文件: {args.plan_path}")
    print(f"计划摘要 SHA-256: {plan['plan_sha256']}")
    print(f"运行 ID: {plan['run_id']}")
    print(f"目标公共词库: {plan['target']['lexicon']}")
    print(f"候选词条: {summary['candidate_entries']}；本次可写入: {summary['ready_entries']}"
          f"（阻断 {summary['blocked_entries']} / 已排除 {summary['excluded_entries']}）")

    if not args.confirm:
        # Never started by a timer or a script: the operator types the run ID.
        print()
        print("拒绝执行：缺少 --confirm <运行 ID>。核对上面的计划后原样输入：")
        print(f"  --confirm {plan['run_id']}")
        return 1
    if args.confirm != plan["run_id"]:
        print()
        print("拒绝执行：--confirm 与计划中的运行 ID 不一致。未写入任何内容。")
        return 1
    if plan.get("confirmation_ready") is not True:
        print()
        print("拒绝执行：计划尚未就绪，先解决以下阻断项后重新预览与裁定：")
        for blocker in plan.get("confirmation_blockers") or []:
            print(f"  - {blocker}")
        return 1

    print()
    password = getpass.getpass(f"请输入管理员 {args.admin} 的当前口令：")
    try:
        database = _confirmed_database_path()
    except Exception as error:  # noqa: BLE001 -- a bad target must fail closed
        print(f"无法确认目标数据库：{error}", file=sys.stderr)
        return 2
    with _open_session() as session:
        user = _find_user(session, args.admin)
        # An unknown account spends the verification an existing one would, so the
        # time taken says nothing about whether the account exists.
        if user is None or not verify_user_password(user, password):
            print("管理员身份或口令不正确，未写入任何内容。", file=sys.stderr)
            return 1
        try:
            result = confirm_plan(
                session,
                plan=plan,
                administrator=user,
                source_root=args.source_root,
            )
        except ConfirmRefused as error:
            print()
            print(f"拒绝确认，未写入任何内容：{error}", file=sys.stderr)
            _write_failure_report(args.report_path, plan, args.admin, str(error))
            return 1
        except Exception as error:  # noqa: BLE001 -- report, then fail closed
            print()
            print(f"确认失败，事务已整体回滚，未写入任何内容：{error}", file=sys.stderr)
            _write_failure_report(args.report_path, plan, args.admin, repr(error))
            return 1

    print()
    if result["status"] == "already_applied":
        print(f"该计划此前已确认（运行 {result['run_id']}），本次未写入任何新内容。")
    else:
        print("已确认并提交。")
    print(f"运行 ID: {result['run_id']}；导入运行记录 id: {result['import_run_id']}")
    print(f"新建公共词条: {result['entries_created']}；"
          f"库内已存在（未覆盖）: {result['entries_matched']}")
    print(f"写入证据记录: {result['evidence_written']}"
          f"（跳过重复 {result.get('evidence', {}).get('skipped_existing', 0)}，"
          f"重新裁定 {result.get('evidence', {}).get('readjudicated', 0)}）")
    print(f"数据库: {database}")
    if result["conflicts"]:
        print(f"库内同词冲突 {len(result['conflicts'])} 项（既有释义未被改动）：")
        for conflict in result["conflicts"][:20]:
            print(f"  - {conflict['normalized_word']}"
                  f"（既有 lexicon_entry id={conflict['lexicon_entry_id']}）")
    return 0


# --- concise study meanings ---------------------------------------------------
#
# The administrator entry point for the short meanings the study page shows. The
# product rule is that a human confirms every displayed value, so the two write
# commands are separate on purpose: ``propose`` only ever creates invisible
# candidates, and ``confirm`` names the candidates a person has actually read. The
# operator reads ``status`` in between, which prints the untouched source fields
# beside the proposals so the review is a comparison rather than a memory test.


class ConciseMeaningFileError(Exception):
    """The proposal file is not usable. Nothing was read and nothing was written."""


#: Recognised keys, per level. An unknown key is an error rather than something to
#: ignore: a typo in ``provenance_kind`` should refuse the file, not silently default
#: a quoted value to a machine-written supplement.
#:
#: ``format_version`` 2 groups an entry's meanings by part of speech. Version 1 had a
#: flat ``meanings`` list capped at three per **word**, which cannot express ``play``
#: (three verb senses plus one noun sense), so it is refused rather than reinterpreted:
#: silently reading a flat list as one unnamed group would file every value under an
#: undetermined part of speech, and the point of this format is that a person states one.
_PROPOSAL_FILE_KEYS = frozenset({"format_version", "lexicon", "entries"})
_PROPOSAL_ENTRY_KEYS = frozenset({"word", "pos_groups"})
_PROPOSAL_GROUP_KEYS = frozenset(
    {
        "pos_key",
        "pos_label",
        "pos_order",
        "pos_source",
        "pos_evidence_locator",
        "pos_wikitext_line_id",
        "language",
        "meanings",
    }
)
_PROPOSAL_MEANING_KEYS = frozenset(
    {
        "text",
        "provenance_kind",
        "display_order",
        "source_locator",
        "derivation_note",
        "source_evidence_id",
        "primary_wikitext_line_id",
        "citations",
    }
)
_PROPOSAL_CITATION_KEYS = frozenset(
    {"citation_locator", "citation_order", "source_evidence_id", "wikitext_line_id"}
)
_PROPOSAL_FORMAT_VERSION = 2


def _reject_unknown_keys(payload: dict, allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConciseMeaningFileError(
            f"{where} 含未知字段 {'、'.join(unknown)}；"
            f"允许的字段为 {'、'.join(sorted(allowed))}。"
            "不忽略未知字段，以免拼写错误被当成默认值。"
        )


def load_proposal_file(path: Path, *, lexicon_name: str) -> list[dict]:
    """Read and validate the proposal file, returning per-entry proposals.

    Validation happens entirely before the database is opened, so a malformed file
    cannot leave a half-written review state behind.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConciseMeaningFileError(f"提案文件无法读取：{error}") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConciseMeaningFileError(f"提案文件不是合法 JSON：{error}") from error
    if not isinstance(payload, dict):
        raise ConciseMeaningFileError("提案文件的顶层必须是对象。")
    _reject_unknown_keys(payload, _PROPOSAL_FILE_KEYS, "提案文件顶层")
    if payload.get("format_version") != _PROPOSAL_FORMAT_VERSION:
        raise ConciseMeaningFileError(
            f"提案文件 format_version 必须是 {_PROPOSAL_FORMAT_VERSION}，"
            f"实际为 {payload.get('format_version')!r}。"
        )
    declared = payload.get("lexicon")
    if declared is not None and str(declared).strip() != lexicon_name:
        raise ConciseMeaningFileError(
            f"提案文件声明的词库 {declared!r} 与 --lexicon {lexicon_name!r} 不一致。"
        )

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConciseMeaningFileError("提案文件的 entries 必须是非空数组。")

    prepared: list[dict] = []
    for index, entry in enumerate(entries):
        where = f"entries[{index}]"
        if not isinstance(entry, dict):
            raise ConciseMeaningFileError(f"{where} 必须是对象。")
        _reject_unknown_keys(entry, _PROPOSAL_ENTRY_KEYS, where)
        word = str(entry.get("word") or "").strip()
        if not word:
            raise ConciseMeaningFileError(f"{where} 缺少 word。")
        groups = entry.get("pos_groups")
        if not isinstance(groups, list) or not groups:
            raise ConciseMeaningFileError(
                f"{where}（{word}）的 pos_groups 必须是非空数组；"
                "每个词性组至少要有 1 条释义。词性是每组都要写明的字段，"
                "不能留给程序推断。"
            )
        group_orders: list[int] = []
        proposed: list[ConciseMeaningProposal] = []
        for group_index, group in enumerate(groups):
            group_where = f"{where}.pos_groups[{group_index}]"
            if not isinstance(group, dict):
                raise ConciseMeaningFileError(f"{group_where} 必须是对象。")
            _reject_unknown_keys(group, _PROPOSAL_GROUP_KEYS, group_where)

            pos_key = str(group.get("pos_key") or "").strip()
            pos_source = str(group.get("pos_source") or "").strip()
            if not pos_key:
                raise ConciseMeaningFileError(
                    f"{group_where} 缺少 pos_key。词性是必填的闭集键之一"
                    f"（{'、'.join(CONCISE_MEANING_POS_KEYS)}）；"
                    "要提交一个尚未确定词性的候选，请显式写 pos_source=\"none\" 并省略 pos_key。"
                )
            if not pos_source:
                raise ConciseMeaningFileError(
                    f"{group_where} 缺少 pos_source；必须写明词性依据是"
                    " pos_section（来源小节标题）还是 reviewer（人工试判）。"
                    "词性不会由程序推断。"
                )
            order = group.get("pos_order")
            if order is None:
                order = group_index + 1
            if not isinstance(order, int) or isinstance(order, bool) or order < 1:
                raise ConciseMeaningFileError(
                    f"{group_where} 的 pos_order 必须是 ≥1 的整数（可省略，默认按顺序）。"
                )
            group_orders.append(order)
            language = str(group.get("language") or "").strip()
            pos_line_id = group.get("pos_wikitext_line_id")
            if pos_line_id is not None and (type(pos_line_id) is not int or pos_line_id < 1):
                raise ConciseMeaningFileError(f"{group_where} 的 pos_wikitext_line_id 必须是正整数。")

            meanings = group.get("meanings")
            if not isinstance(meanings, list) or not meanings:
                raise ConciseMeaningFileError(f"{group_where} 的 meanings 必须是非空数组。")
            if len(meanings) > CONCISE_MEANING_MAX_SLOTS:
                raise ConciseMeaningFileError(
                    f"{group_where} 提交了 {len(meanings)} 个义项，"
                    f"每个词性组最多显示 {CONCISE_MEANING_MAX_SLOTS} 个。"
                )
            for position, meaning in enumerate(meanings, start=1):
                meaning_where = f"{group_where}.meanings[{position - 1}]"
                if not isinstance(meaning, dict):
                    raise ConciseMeaningFileError(f"{meaning_where} 必须是对象。")
                _reject_unknown_keys(meaning, _PROPOSAL_MEANING_KEYS, meaning_where)
                kind = str(meaning.get("provenance_kind") or "").strip()
                if not kind:
                    raise ConciseMeaningFileError(
                        f"{meaning_where} 缺少 provenance_kind"
                        f"（{'、'.join(CONCISE_MEANING_KINDS)} 之一）。"
                    )
                # Absent means "the position in the group", which is what a reviewer
                # reads anyway; an explicit value must still be an integer in range.
                display_order = meaning.get("display_order", position)
                if not isinstance(display_order, int) or isinstance(display_order, bool):
                    raise ConciseMeaningFileError(
                        f"{meaning_where} 的 display_order 必须是整数。"
                    )
                evidence = meaning.get("source_evidence_id")
                primary_line_id = meaning.get("primary_wikitext_line_id")
                if primary_line_id is not None and (type(primary_line_id) is not int or primary_line_id < 1):
                    raise ConciseMeaningFileError(f"{meaning_where} 的 primary_wikitext_line_id 必须是正整数。")
                if evidence is not None and (
                    not isinstance(evidence, int) or isinstance(evidence, bool)
                ):
                    raise ConciseMeaningFileError(
                        f"{meaning_where} 的 source_evidence_id 必须是整数或省略。"
                    )
                proposed.append(
                    ConciseMeaningProposal(
                        text=str(meaning.get("text") or ""),
                        provenance_kind=kind,
                        display_order=display_order,
                        source_locator=str(meaning.get("source_locator") or ""),
                        derivation_note=str(meaning.get("derivation_note") or ""),
                        source_evidence_id=evidence,
                        primary_wikitext_line_id=primary_line_id,
                        pos_wikitext_line_id=pos_line_id,
                        pos_key=pos_key,
                        pos_label=str(group.get("pos_label") or ""),
                        pos_order=order,
                        pos_source=pos_source,
                        pos_evidence_locator=str(group.get("pos_evidence_locator") or ""),
                        language=language,
                        citations=_load_citations(
                            meaning.get("citations"), where=meaning_where
                        ),
                    )
                )
        if len(set(group_orders)) != len(group_orders):
            raise ConciseMeaningFileError(
                f"{where}（{word}）的 pos_order 有重复；同一词条内每个词性组必须各有组序。"
            )
        prepared.append({"word": word, "proposals": proposed})
    return prepared


def _load_citations(raw: Any, *, where: str) -> tuple[ConciseMeaningCitationProposal, ...]:
    """Read the optional ``citations`` list of one meaning."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConciseMeaningFileError(f"{where} 的 citations 必须是数组。")
    citations: list[ConciseMeaningCitationProposal] = []
    for index, item in enumerate(raw):
        citation_where = f"{where}.citations[{index}]"
        if not isinstance(item, dict):
            raise ConciseMeaningFileError(f"{citation_where} 必须是对象。")
        _reject_unknown_keys(item, _PROPOSAL_CITATION_KEYS, citation_where)
        locator = str(item.get("citation_locator") or "").strip()
        if not locator:
            raise ConciseMeaningFileError(f"{citation_where} 缺少 citation_locator。")
        order = item.get("citation_order", index + 1)
        if not isinstance(order, int) or isinstance(order, bool):
            raise ConciseMeaningFileError(
                f"{citation_where} 的 citation_order 必须是整数。"
            )
        evidence = item.get("source_evidence_id")
        line_id = item.get("wikitext_line_id")
        if line_id is not None and (type(line_id) is not int or line_id < 1):
            raise ConciseMeaningFileError(f"{citation_where} 的 wikitext_line_id 必须是正整数。")
        if evidence is not None and (
            not isinstance(evidence, int) or isinstance(evidence, bool)
        ):
            raise ConciseMeaningFileError(
                f"{citation_where} 的 source_evidence_id 必须是整数或省略。"
            )
        citations.append(
            ConciseMeaningCitationProposal(
                citation_locator=locator,
                citation_order=order,
                source_evidence_id=evidence,
                wikitext_line_id=line_id,
            )
        )
    return tuple(citations)


def _resolve_system_lexicon(session, name: str):
    """Exactly one system public lexicon with this name, or a readable refusal."""
    from app.models import Lexicon

    matches = session.scalars(
        select(Lexicon).where(
            Lexicon.owner_user_id.is_(None),
            Lexicon.visibility == "public",
            Lexicon.name == name,
        )
    ).all()
    if not matches:
        raise ConciseMeaningRefused(f"找不到名为 {name!r} 的系统公共词库。")
    if len(matches) > 1:
        raise ConciseMeaningRefused(
            f"有 {len(matches)} 个系统公共词库都叫 {name!r}，无法确定目标。"
        )
    return matches[0]


def _resolve_administrator(session, username: str):
    """The named account, verified with its own current password.

    The password is prompted for and never accepted as an argument, for the reason
    this module's docstring gives: an argument or an environment variable is already
    disclosed before the program starts.
    """
    password = getpass.getpass(f"请输入管理员 {username} 的当前口令：")
    user = _find_user(session, username)
    # An unknown account spends the verification an existing one would, so the time
    # taken says nothing about whether the account exists.
    if user is None or not verify_user_password(user, password):
        raise ConciseMeaningRefused("管理员身份或口令不正确，未写入任何内容。")
    return user


def command_concise_meaning_status(args: argparse.Namespace) -> int:
    """Read-only: what is displayed, what is proposed, and what the source says."""
    from app.models import LexiconEntry
    from app.services.concise_meaning import describe_entry, find_entry

    with _open_session() as session:
        try:
            lexicon = _resolve_system_lexicon(session, args.lexicon)
        except ConciseMeaningRefused as error:
            print(f"无法定位词库：{error}", file=sys.stderr)
            return 2
        if args.word:
            entry = find_entry(session, lexicon.id, args.word)
            if entry is None:
                print(f"词库 {lexicon.name!r} 中没有词条 {args.word!r}。", file=sys.stderr)
                return 2
            entries = [entry]
        else:
            entries = session.scalars(
                select(LexiconEntry)
                .where(LexiconEntry.lexicon_id == lexicon.id)
                .order_by(LexiconEntry.sequence, LexiconEntry.word)
            ).all()
        report = {
            "lexicon": {"id": lexicon.id, "name": lexicon.name},
            "entries": [describe_entry(session, entry) for entry in entries],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def command_concise_meaning_propose(args: argparse.Namespace) -> int:
    """Append candidates from a file. Nothing becomes visible to any user."""
    from app.services.concise_meaning import find_entry, propose

    try:
        prepared = load_proposal_file(args.file, lexicon_name=args.lexicon)
    except ConciseMeaningFileError as error:
        print(f"提案文件不可用：{error}", file=sys.stderr)
        return 2

    try:
        database = _confirmed_database_path()
    except Exception as error:  # noqa: BLE001 -- a bad target must fail closed
        print(f"无法确认目标数据库：{error}", file=sys.stderr)
        return 2

    print("简短学习释义 · 提交候选（只写候选，不改变任何用户看到的内容）")
    print(f"目标公共词库: {args.lexicon}")

    with _open_session() as session:
        written = 0
        missing: list[str] = []
        try:
            lexicon = _resolve_system_lexicon(session, args.lexicon)
            administrator = _resolve_administrator(session, args.admin)
            for item in prepared:
                entry = find_entry(session, lexicon.id, item["word"])
                if entry is None:
                    missing.append(item["word"])
                    continue
                created = propose(
                    session, entry=entry, proposals=item["proposals"],
                    actor=administrator,
                )
                written += len(created)
                for row in created:
                    print(f"  候选 id={row.id}  {entry.word}"
                          f"  [{row.pos_key or '（词性未定）'} 第{row.display_order}位]"
                          f"  {row.text}  [{row.provenance_kind}]"
                          f"  依据={POS_SOURCE_LABELS.get(row.pos_source, row.pos_source)}"
                          f"  语言={row.language or '（未记录）'}"
                          + (f"  附加引用={len(row.citations)}" if row.citations else ""))
            if missing:
                # Refuse the whole file rather than write half of it: a proposal that
                # silently skipped words would look complete at review time.
                raise ConciseMeaningRefused(
                    "以下词不在目标词库中，整份提案未写入："
                    + "、".join(missing)
                    + "。请核对词形（身份规则为去首尾空白 + casefold）。"
                )
            session.commit()
        except ConciseMeaningRefused as error:
            session.rollback()
            print()
            print(f"拒绝写入，未做任何修改：{error}", file=sys.stderr)
            return 1
        except Exception as error:  # noqa: BLE001 -- report, then fail closed
            session.rollback()
            print()
            print(f"写入失败，事务已整体回滚，未做任何修改：{error}", file=sys.stderr)
            return 1

    print()
    print(f"已写入 {written} 条候选，状态均为 candidate。")
    print("候选不会出现在任何学习页面上；请用 `concise-meaning status` 审阅后，"
          "再用 `concise-meaning confirm --id ...` 逐条确认。")
    print(f"数据库: {database}")
    return 0


def command_concise_meaning_confirm(args: argparse.Namespace) -> int:
    """Confirm named candidates as a human, which is what puts them on the page."""
    from app.models import EntryConciseMeaning, LexiconEntry
    from app.services.concise_meaning import confirm

    try:
        database = _confirmed_database_path()
    except Exception as error:  # noqa: BLE001 -- a bad target must fail closed
        print(f"无法确认目标数据库：{error}", file=sys.stderr)
        return 2

    ids = list(dict.fromkeys(args.id))
    print("简短学习释义 · 人工确认（确认后学习页才会显示这些值）")
    print(f"目标公共词库: {args.lexicon}")
    print(f"待确认候选 id: {', '.join(str(value) for value in ids)}")

    with _open_session() as session:
        confirmed = 0
        try:
            lexicon = _resolve_system_lexicon(session, args.lexicon)
            administrator = _resolve_administrator(session, args.admin)
            for meaning_id in ids:
                row = session.get(EntryConciseMeaning, meaning_id)
                if row is None:
                    raise ConciseMeaningRefused(f"候选 id={meaning_id} 不存在。")
                entry = session.get(LexiconEntry, row.lexicon_entry_id)
                if entry is None or entry.lexicon_id != lexicon.id:
                    raise ConciseMeaningRefused(
                        f"候选 id={meaning_id} 不属于词库 {lexicon.name!r}。"
                    )
                confirm(session, meaning=row, confirmer=administrator, note=args.note)
                confirmed += 1
                print(f"  已确认 id={row.id}  {entry.word}"
                      f"  [{row.pos_key} 第{row.display_order}位]"
                      f"  {row.text}  [{row.provenance_kind}]"
                      f"  依据={POS_SOURCE_LABELS.get(row.pos_source, row.pos_source)}"
                      f"@{row.pos_evidence_locator}  语言={row.language}")
            session.commit()
        except ConciseMeaningRefused as error:
            session.rollback()
            print()
            print(f"拒绝确认，未做任何修改：{error}", file=sys.stderr)
            return 1
        except Exception as error:  # noqa: BLE001 -- report, then fail closed
            session.rollback()
            print()
            print(f"确认失败，事务已整体回滚，未做任何修改：{error}", file=sys.stderr)
            return 1

    print()
    print(f"已确认 {confirmed} 条。原始来源文本（source_raw / source_meanings）未做任何改动。")
    print(f"数据库: {database}")
    return 0


def command_concise_meaning_reject(args: argparse.Namespace) -> int:
    """Withdraw a candidate or a displayed value, with a reason on the record."""
    from app.models import EntryConciseMeaning
    from app.services.concise_meaning import reject

    try:
        database = _confirmed_database_path()
    except Exception as error:  # noqa: BLE001 -- a bad target must fail closed
        print(f"无法确认目标数据库：{error}", file=sys.stderr)
        return 2

    print("简短学习释义 · 撤回/拒绝（记录理由，不删除任何历史）")
    with _open_session() as session:
        try:
            administrator = _resolve_administrator(session, args.admin)
            row = session.get(EntryConciseMeaning, args.id)
            if row is None:
                raise ConciseMeaningRefused(f"候选 id={args.id} 不存在。")
            reject(session, meaning=row, actor=administrator, note=args.note)
            session.commit()
        except ConciseMeaningRefused as error:
            session.rollback()
            print()
            print(f"拒绝执行，未做任何修改：{error}", file=sys.stderr)
            return 1
        except Exception as error:  # noqa: BLE001 -- report, then fail closed
            session.rollback()
            print()
            print(f"撤回失败，事务已整体回滚，未做任何修改：{error}", file=sys.stderr)
            return 1
        withdrawn_text = row.text
        withdrawn_order = row.display_order
        withdrawn_pos = row.pos_key or "（词性未定）"
    print(f"  已撤回 id={args.id}（原 {withdrawn_pos} 组第{withdrawn_order}位"
          f"「{withdrawn_text}」），该组该位置已空出。")
    print(f"数据库: {database}")
    return 0


def _confirmed_database_path() -> Path:
    """Resolve the target database and refuse unless its revision matches the code.

    The confirmation paths write tables that only migrations ``0008`` and ``0009``
    create, so running one against a database that has not been migrated must fail
    here with a clear message rather than halfway through a transaction. This is the
    same check the application performs at startup (``verify_schema_revision``,
    fail-closed); unlike the read-only preview commands, these are *meant* to run
    against whatever database the configuration points at, so they do not refuse the
    real one.
    """
    url = str(get_engine().url)
    path = database_path_from_url(url)
    if not path:
        raise RuntimeError(f"数据库 URL 不是文件型 SQLite：{url}")
    resolved = Path(path).resolve()
    verify_schema_revision(resolved)
    return resolved


def _write_failure_report(
    report_path: Path | None, plan: dict, administrator: str, error: str
) -> None:
    """Write the failure report outside the rolled-back transaction.

    The transaction is gone by the time this runs, so the report is the only record
    of what was attempted; it is created exclusively so an earlier report is never
    overwritten.
    """
    if report_path is None:
        return
    try:
        with report_path.open("x", encoding="utf-8") as target:
            json.dump({
                "plan_sha256": plan.get("plan_sha256"),
                "run_id": plan.get("run_id"),
                "administrator": administrator,
                "error": error,
                "committed": False,
            }, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(f"失败报告已写入: {report_path}", file=sys.stderr)
    except OSError as write_error:
        print(f"失败报告无法写入：{write_error}", file=sys.stderr)


def _preview_database_path() -> Path:
    """Resolve the database without get_settings(), which creates directories."""
    project_root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.getenv("VOCAB_DATA_DIR", str(project_root / "data"))).resolve()
    explicit = os.getenv("VOCAB_DATABASE_PATH", "").strip()
    path = Path(explicit).resolve() if explicit else data_dir / "vocab.db"
    assert_not_real_data(path, action="preview history retention in")
    return path


def _default_baseline_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "recovery" / "baseline.json"


def _read_plan(path: Path) -> dict:
    from app.history_retention import RetentionError, load_plan

    try:
        return load_plan(path)
    except RetentionError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"计划文件无法读取：{path}（{error}）")


def command_history_retention_preview(args: argparse.Namespace) -> int:
    """Read-only preview. ``--plan`` additionally writes the locked candidate plan."""
    from datetime import UTC, datetime

    from app.history_retention import RetentionError, retention_policy

    policy = retention_policy()
    # One instant for both artifacts: the summary an operator reads and the plan the
    # apply flow must match have to describe the same cutoff.
    moment = datetime.now(UTC)
    report = preview_history_retention(
        args.database_path, now=moment, retention_days=policy.retention_days
    )
    print("G6 history_event 保留策略：在线保留 365 天（已确认），仅四类事件可归档")
    print("生产执行未批准：本命令只读；apply 需要负责人针对具体计划与运行 ID 另行批准")
    print(f"窗口: {report['retention_days']} 天；UTC cutoff: {report['cutoff_utc']}（严格早于）")
    print(f"数据库: {args.database_path}")
    print(f"总数: {report['total_count']}; 候选: {report['candidate_count']}")
    print(f"候选 ID 集合 SHA-256: {report['candidate_ids_sha256']}")
    for event_type, counts in report["event_counts"].items():
        print(f"{event_type}: 总数 {counts['total']}, 候选 {counts['candidate']}")
    print("跳过原因: " + ", ".join(f"{key}={value}" for key, value in report["skipped"].items()))
    print(f"始终保留的最高 ID: {report['max_history_event_id']}")
    if args.json_path is not None:
        # Exclusive creation is atomic and refuses to overwrite previous evidence.
        with args.json_path.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(f"JSON 报告已写入: {args.json_path}")
    if args.plan_path is not None:
        from app.history_retention_preview import build_plan, write_plan

        try:
            plan = build_plan(
                args.database_path,
                baseline_path=args.baseline,
                now=moment,
                retention_days=policy.retention_days,
            )
            write_plan(plan, args.plan_path)
        except RetentionError as error:
            print(f"无法生成计划：{error}", file=sys.stderr)
            return 1
        print()
        print(f"候选计划已写入: {args.plan_path}")
        print(f"运行 ID（apply --confirm 需要原样输入）: {plan['run_id']}")
        print(f"计划摘要 SHA-256: {plan['plan_sha256']}")
        print(f"候选 ID: {plan['candidate_ids']}")
    elif report["candidate_count"]:
        print()
        print("提示：加 --plan <新文件> 生成可复核的候选计划；apply 只接受计划文件。")
    return 0


def command_history_retention_apply(args: argparse.Namespace) -> int:
    """Archive and then remove exactly the planned rows, on an explicit confirmation."""
    from app.history_retention import RetentionError, apply_plan

    plan = _read_plan(args.plan_path)
    run_id = plan["run_id"]
    print("G6 history_event 保留策略 apply（归档 + 按明确 ID 清理）")
    print(f"计划: {args.plan_path}")
    print(f"运行 ID: {run_id}")
    print(f"目标数据库: {args.database_path}")
    print(f"cutoff: {plan['cutoff_utc']}；窗口: {plan['retention_days']} 天")
    print(f"本次将删除 {plan['candidate_count']} 行: {plan['candidate_ids']}")
    if not args.confirm:
        # Nothing runs without the operator typing the run ID: this command is never
        # started by a timer, a script or an automatic path.
        print()
        print(
            "拒绝执行：缺少 --confirm <运行 ID>。请核对上面的计划后，"
            f"原样输入 --confirm {run_id}"
        )
        return 1
    if args.confirm != run_id:
        print()
        print(
            "拒绝执行：--confirm 与计划中的运行 ID 不一致。"
            "计划未被执行，数据库未改动。"
        )
        return 1
    evidence_dir = args.evidence_dir or args.baseline.parent / "history-retention"
    try:
        result = apply_plan(
            args.database_path,
            plan,
            baseline_path=args.baseline,
            evidence_dir=evidence_dir,
            drafts_dir=args.drafts_dir,
            allow_production=args.allow_production,
            log=print,
        )
    except RetentionError as error:
        print()
        print(f"本次运行已停止且未提交清理：{error}", file=sys.stderr)
        print(
            "故障关闭：不要把这次失败当作完成。若上面提示事务已提交但凭证未发布，"
            "请先停止写入，再从该次运行的 <运行 ID>.before.db 恢复到隔离副本并核验。",
            file=sys.stderr,
        )
        return 1
    print()
    print(f"已发布并核验完成：删除 {result['deleted']} 行，run {result['run_id']}")
    print(f"清理前备份: {result['pre_backup']}")
    print(f"归档: {result['archive']}")
    print(f"committed 凭证: {result['committed_manifest']}")
    print("核验方式: python tools/verify_backup.py <database> --baseline <baseline.json>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="拾词管理命令（不开放公众注册）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    from app.services.backup import configure_parser

    configure_parser(sub.add_parser("backup", help="校验备份及每日备份保留策略"))

    list_parser = sub.add_parser("list-users", help="列出所有用户")
    list_parser.set_defaults(func=command_list_users)

    # No --password on any subcommand: see the module docstring. The password is
    # prompted for interactively and never appears in argv.
    set_password = sub.add_parser("set-password", help="设置或重置某个用户的密码（交互输入）")
    set_password.add_argument("username")
    set_password.set_defaults(func=command_set_password)

    create_user = sub.add_parser("create-user", help="创建新用户（交互输入密码）")
    create_user.add_argument("username")
    create_user.add_argument("--display-name", default="")
    create_user.add_argument("--role", choices=["admin", "user"], default="user")
    create_user.set_defaults(func=command_create_user)

    promote = sub.add_parser("promote", help="调整用户角色")
    promote.add_argument("username")
    promote.add_argument("--role", choices=["admin", "user"], default="admin")
    promote.set_defaults(func=command_promote)

    prune = sub.add_parser("prune-sessions", help="清理已撤销／已过期／闲置超时的会话")
    prune.set_defaults(func=command_prune_sessions)

    migration_status = sub.add_parser(
        "migration-status",
        help="只读报告数据库 revision 与代码 head 的关系（启动器据此决定是否迁移）",
    )
    migration_status.add_argument(
        "--database", type=Path, default=None,
        help="要检查的数据库；默认按 VOCAB_DATABASE_PATH / VOCAB_DATA_DIR 解析",
    )
    migration_status.add_argument(
        "--env-file", type=Path, default=None,
        help="按 uvicorn 的规则加载 .env，再解析数据库路径",
    )
    migration_status.set_defaults(func=command_migration_status, inspect_schema_state=True)

    public_lexicon = sub.add_parser("public-lexicon", help="公共词库文件工具")
    public_sub = public_lexicon.add_subparsers(dest="public_command", required=True)
    file_preview = public_sub.add_parser("preview", help="只读校验分隔文本文件")
    file_preview.add_argument("file", type=Path)
    file_preview.add_argument(
        "--source-root", type=Path, required=True,
        help="管理员控制、预览期间不变的本地来源目录；文件解析后不得越界",
    )
    file_preview.add_argument(
        "--map", dest="field_map", action="append", required=True,
        metavar="FIELD=COLUMN", help="规范字段到原文件列名的映射，可重复",
    )
    file_preview.add_argument(
        "--required", action="append", default=[], metavar="FIELD",
        help="要求非空的字段，word 始终必填，可重复",
    )
    file_preview.add_argument("--encoding", default="utf-8-sig")
    file_preview.add_argument("--delimiter", default=",")
    file_preview.set_defaults(func=command_public_lexicon_preview, file_only_preview=True)

    joint_preview = public_sub.add_parser("preview-many", help="按清单联合只读预览多个来源")
    joint_preview.add_argument("manifest", type=Path, help="来源根目录内的 JSON 清单")
    joint_preview.add_argument("--source-root", type=Path, required=True)
    joint_preview.set_defaults(func=command_public_lexicon_preview_many, file_only_preview=True)

    locked_plan = public_sub.add_parser(
        "plan", help="按人工裁定写出只读的锁定计划（不确认、不写库）"
    )
    locked_plan.add_argument("manifest", type=Path, help="来源根目录内的 JSON 清单")
    locked_plan.add_argument("--source-root", type=Path, required=True)
    locked_plan.add_argument(
        "--decisions", type=Path, required=True, help="来源根目录内的人工裁定 JSON"
    )
    locked_plan.add_argument(
        "--plan", dest="plan_path", type=Path, required=True,
        help="写出新计划文件（确认流程的唯一输入），拒绝覆盖",
    )
    locked_plan.add_argument(
        "--target-lexicon", required=True,
        help="本计划指向的公共词库名称；只读切片无法核对它是否存在，确认时另行校验",
    )
    locked_plan.add_argument(
        "--require-ready", action="store_true",
        help="计划未就绪（confirmation_ready=False）时以非零退出码结束",
    )
    locked_plan.set_defaults(func=command_public_lexicon_plan, file_only_preview=True)

    target_preflight = public_sub.add_parser(
        "preflight-target", help="对目标公共词库做只读差异预检"
    )
    target_preflight.add_argument("--plan", dest="plan_path", type=Path, required=True)
    target_preflight.add_argument("--source-root", type=Path, required=True)
    target_preflight.add_argument(
        "--database", type=Path, required=True,
        help="显式指定待预检的 SQLite 库路径；仅以只读模式打开",
    )
    target_preflight.set_defaults(
        func=command_public_lexicon_preflight_target, file_only_preview=True
    )

    confirm = public_sub.add_parser(
        "confirm", help="管理员确认锁定计划并写入公共内容层（要求本人当前口令）"
    )
    confirm.add_argument("--plan", dest="plan_path", type=Path, required=True)
    confirm.add_argument(
        "--source-root", type=Path, required=True,
        help="管理员控制、确认期间不变的本地来源目录；文件会被重新读取核对",
    )
    confirm.add_argument(
        "--confirm", default="",
        help="原样输入计划里的运行 ID；缺少或不一致即拒绝执行，不写入任何内容",
    )
    confirm.add_argument(
        "--admin", required=True, help="执行本次确认的管理员用户名（口令交互输入）",
    )
    confirm.add_argument(
        "--report", dest="report_path", type=Path, default=None,
        help="失败时写出新的 JSON 失败报告（拒绝覆盖既有报告）",
    )
    confirm.set_defaults(func=command_public_lexicon_confirm)

    # The study page's short meanings. Three write verbs and one read-only review:
    # a candidate is inert until a person confirms it by id, and the review command
    # prints the untouched source fields beside the proposals so the confirmation is
    # a comparison rather than a memory test.
    concise = sub.add_parser(
        "concise-meaning", help="学习页简短释义：候选、人工确认与撤回"
    )
    concise_sub = concise.add_subparsers(dest="concise_command", required=True)

    concise_status = concise_sub.add_parser(
        "status", help="只读查看某词库的展示值、候选与完整历史"
    )
    concise_status.add_argument("--lexicon", required=True, help="系统公共词库名称")
    concise_status.add_argument("--word", default="", help="只看某一个词")
    concise_status.set_defaults(func=command_concise_meaning_status)

    concise_propose = concise_sub.add_parser(
        "propose", help="按 JSON 文件提交候选（不会出现在任何学习页面上）"
    )
    concise_propose.add_argument("--file", type=Path, required=True, help="候选 JSON 文件")
    concise_propose.add_argument("--lexicon", required=True, help="目标系统公共词库名称")
    concise_propose.add_argument(
        "--admin", required=True, help="执行本次提交的管理员用户名（口令交互输入）"
    )
    concise_propose.set_defaults(func=command_concise_meaning_propose)

    concise_confirm = concise_sub.add_parser(
        "confirm", help="人工确认指定候选（确认后学习页才会显示）"
    )
    concise_confirm.add_argument(
        "--id", type=int, action="append", required=True,
        help="要确认的候选 id，可重复；id 由 status 命令给出",
    )
    concise_confirm.add_argument("--lexicon", required=True, help="目标系统公共词库名称")
    concise_confirm.add_argument("--note", default="", help="确认备注（可选，写入历史）")
    concise_confirm.add_argument(
        "--admin", required=True, help="执行本次确认的管理员用户名（口令交互输入）"
    )
    concise_confirm.set_defaults(func=command_concise_meaning_confirm)

    concise_reject = concise_sub.add_parser(
        "reject", help="撤回或拒绝一条候选/展示值（必须写明理由）"
    )
    concise_reject.add_argument("--id", type=int, required=True, help="候选 id")
    concise_reject.add_argument("--note", required=True, help="理由；会写入历史")
    concise_reject.add_argument(
        "--admin", required=True, help="执行本次撤回的管理员用户名（口令交互输入）"
    )
    concise_reject.set_defaults(func=command_concise_meaning_reject)

    retention = sub.add_parser("history-retention", help="history_event 保留策略（已确认 365 天）")
    retention_sub = retention.add_subparsers(dest="retention_command", required=True)
    preview = retention_sub.add_parser("preview", help="只读预览候选事件，不执行清理")
    preview.add_argument("--json", dest="json_path", type=Path, help="写入新 JSON 报告，拒绝覆盖")
    preview.add_argument(
        "--plan", dest="plan_path", type=Path, default=None,
        help="写入新的候选计划（apply 的唯一输入），拒绝覆盖",
    )
    preview.add_argument(
        "--baseline", type=Path, default=None, help="baseline 快照（默认 data/recovery/baseline.json）"
    )
    preview.set_defaults(func=command_history_retention_preview, read_only_preview=True)

    apply_parser = retention_sub.add_parser(
        "apply", help="按已复核的计划归档并清理（要求 --confirm 输入运行 ID）"
    )
    apply_parser.add_argument("--plan", dest="plan_path", type=Path, required=True)
    apply_parser.add_argument(
        "--confirm", default="", help="原样输入计划里的运行 ID；缺少即拒绝执行"
    )
    apply_parser.add_argument(
        "--baseline", type=Path, default=None, help="baseline 快照（默认 data/recovery/baseline.json）"
    )
    apply_parser.add_argument(
        "--evidence-dir", dest="evidence_dir", type=Path, default=None,
        help="凭证目录（默认 <baseline 目录>/history-retention）",
    )
    apply_parser.add_argument(
        "--drafts-dir", dest="drafts_dir", type=Path, default=None,
        help="本次运行的草稿目录（默认 <凭证目录>/../history-retention-drafts）",
    )
    apply_parser.add_argument(
        "--allow-production",
        action="store_true",
        help="明确允许对受保护数据库（生产库/备份/恢复副本）执行；需要负责人批准",
    )
    apply_parser.set_defaults(func=command_history_retention_apply, read_only_preview=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "file_only_preview", False):
        return int(args.func(args))
    if getattr(args, "inspect_schema_state", False):
        # Deliberately skips verify_schema_revision, and calls no get_settings():
        # this command's whole job is to report a mismatch to the launcher, so the
        # gate would refuse before it could answer. It also must not create a data
        # directory, because the launcher runs it before anything exists.
        return int(args.func(args))
    if getattr(args, "read_only_preview", False):
        args.database_path = _preview_database_path()
        if getattr(args, "baseline", None) is None:
            args.baseline = _default_baseline_path()
        verify_schema_revision(args.database_path)
        return int(args.func(args))
    settings = get_settings()
    settings.ensure_directories()
    # Refuse to operate on a database whose schema does not match this code:
    # otherwise the CLI would fail with a confusing "no such table" error, or
    # worse, appear to succeed against the wrong database.
    verify_schema_revision(settings.database_path)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
