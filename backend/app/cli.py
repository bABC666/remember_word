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

from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory, verify_schema_revision
from app.history_retention_preview import preview_history_retention
from app.models import User, UserSession, UserSettings
from app.security import hash_password, password_is_usable
from app.services.auth import normalize_username, prune_sessions
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
