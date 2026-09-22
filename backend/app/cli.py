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
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory, verify_schema_revision
from app.models import User, UserSession, UserSettings
from app.security import hash_password, password_is_usable
from app.services.auth import normalize_username, prune_sessions

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    settings.ensure_directories()
    # Refuse to operate on a database whose schema does not match this code:
    # otherwise the CLI would fail with a confusing "no such table" error, or
    # worse, appear to succeed against the wrong database.
    verify_schema_revision(settings.database_path)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
