"""Create or change an account in a *staging* clone, without exposing the password.

Why this exists
---------------
``python -m app.cli`` reads passwords with ``getpass`` and from nowhere else, on
purpose: a password in argv is readable by every process on the machine. That makes
the operator CLI impossible to drive from a script on Windows, where ``getpass``
always reads the console through ``msvcrt.getwch()`` -- it only falls back to stdin
when ``sys.stdin`` has been *replaced*, so a piped caller blocks forever instead of
reading the pipe.

This helper is the scripted path for staging automation, and nothing else. It reads
the password from **stdin** -- not argv, not the environment -- and calls the same
``app.cli`` functions the interactive command calls, so there is exactly one
implementation of how an account is written.

Refuses to run against anything that is not inside a ``staging`` directory, so it
can never be pointed at production by mistake.

Usage (with ``VOCAB_DATA_DIR`` and ``VOCAB_DATABASE_PATH`` pointing at a staging
clone, as ``tools/staging_*.py`` do)::

    python -m tools.staging_accounts set-password admin <<< 'secret'
    python -m tools.staging_accounts create-user userb --role user <<< 'secret'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.cli import create_user_account, set_password_for_user
from app.config import get_settings
from app.db import get_session_factory, verify_schema_revision

#: A database is only eligible when one of its parent directories is named this.
DISPOSABLE_DIR_NAME = "staging"


def read_password() -> str:
    """Read the password from stdin, or refuse.

    Reading a *line* from stdin is deliberate: the password never reaches argv, the
    environment, or a temporary file.
    """
    line = sys.stdin.readline()
    password = line.rstrip("\r\n")
    if not password:
        print("REFUSING: no password on stdin.", file=sys.stderr)
        raise SystemExit(2)
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.staging_accounts")
    parser.add_argument("command", choices=["set-password", "create-user"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "user"], default="user")
    parser.add_argument("--display-name", default="")
    args = parser.parse_args(argv)

    settings = get_settings()
    database = settings.database_path
    if DISPOSABLE_DIR_NAME not in database.parts:
        # Checked on the resolved path, before anything is opened.
        print(
            f"REFUSING: {database} is not inside a '{DISPOSABLE_DIR_NAME}' directory.\n"
            "This helper only ever writes to a disposable staging clone.",
            file=sys.stderr,
        )
        return 2

    password = read_password()
    verify_schema_revision(database)

    with get_session_factory()() as session:
        if args.command == "create-user":
            ok = create_user_account(
                session,
                args.username,
                password,
                role=args.role,
                display_name=args.display_name,
            )
        else:
            ok = set_password_for_user(session, args.username, password)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
