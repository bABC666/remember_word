"""The operator CLI must take a password from the terminal and nowhere else.

A password in argv is readable by every other process on the machine (``ps``,
Task Manager, and the shell's own history), so the CLI must not offer a way to pass
one. These tests pin the three parts of that promise:

* neither subcommand accepts a ``--password`` option, and argparse refuses one;
* the password comes from ``getpass``, twice, and a mismatch changes nothing;
* nothing the CLI prints contains the password.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import User
from app.security import verify_password

PASSWORD = "cli-getpass-secret"


@pytest.fixture()
def cli_db(tmp_path, isolated_application_engine):
    """A throwaway database the CLI commands write to."""
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "app-data" / "cli-test-db.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    assert_safe_for_destructive_operation(database, action="build a CLI test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def answers(*values: str):
    """A getpass replacement that returns the given values in order."""
    remaining = list(values)
    seen: list[str] = []

    def fake(prompt: str = "") -> str:
        seen.append(prompt)
        if not remaining:
            raise AssertionError("the CLI asked for more input than the test provides")
        return remaining.pop(0)

    fake.seen = seen  # type: ignore[attr-defined]
    return fake


# --- the option is gone ----------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["set-password", "someone", "--password", "plaintext-in-argv"],
        ["create-user", "someone", "--password", "plaintext-in-argv"],
    ],
)
def test_no_subcommand_accepts_a_password_option(argv: list[str]) -> None:
    """argparse exits 2 before any database or prompt is touched."""
    from app import cli

    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(argv)
    assert error.value.code == 2


def test_the_parser_exposes_exactly_the_documented_options() -> None:
    """A guard against the option being reintroduced under another name."""
    import argparse

    from app import cli

    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    for name, subparser in subparsers.choices.items():
        for action in subparser._actions:
            for option in action.option_strings:
                assert "password" not in option.lower(), f"{name} accepts {option}"
                assert "secret" not in option.lower(), f"{name} accepts {option}"


# --- the password comes from getpass --------------------------------------


def test_set_password_reads_twice_with_getpass_and_prints_nothing(
    cli_db, monkeypatch, capsys
) -> None:
    from app import cli
    from app.security import hash_password

    with cli_db() as session:
        session.add(
            User(username="cli-user", display_name="cli-user", password_hash=hash_password("old"))
        )
        session.commit()

    fake = answers(PASSWORD, PASSWORD)
    monkeypatch.setattr("getpass.getpass", fake)
    monkeypatch.setattr(cli, "_open_session", lambda: cli_db())

    assert cli.main(["set-password", "cli-user"]) == 0

    captured = capsys.readouterr()
    assert PASSWORD not in captured.out
    assert PASSWORD not in captured.err
    assert len(fake.seen) == 2, "the password must be typed twice"

    with cli_db() as session:
        user = session.scalar(select(User).where(User.username == "cli-user"))
        assert verify_password(PASSWORD, user.password_hash)


def test_create_user_reads_with_getpass_and_creates_the_account(
    cli_db, monkeypatch, capsys
) -> None:
    from app import cli

    fake = answers(PASSWORD, PASSWORD)
    monkeypatch.setattr("getpass.getpass", fake)
    monkeypatch.setattr(cli, "_open_session", lambda: cli_db())

    assert cli.main(["create-user", "cli-new", "--role", "admin"]) == 0
    assert PASSWORD not in capsys.readouterr().out

    with cli_db() as session:
        user = session.scalar(select(User).where(User.username == "cli-new"))
        assert user is not None
        assert user.role == "admin"
        assert verify_password(PASSWORD, user.password_hash)


def test_a_mismatched_confirmation_changes_nothing(cli_db, monkeypatch) -> None:
    from app import cli

    monkeypatch.setattr("getpass.getpass", answers(PASSWORD, "something-else"))
    monkeypatch.setattr(cli, "_open_session", lambda: cli_db())

    with pytest.raises(SystemExit) as error:
        cli.main(["create-user", "cli-typo"])

    assert "不一致" in str(error.value)
    with cli_db() as session:
        assert session.scalar(select(User).where(User.username == "cli-typo")) is None


def test_a_short_password_changes_nothing(cli_db, monkeypatch) -> None:
    from app import cli
    from app.security import hash_password

    with cli_db() as session:
        session.add(
            User(username="cli-short", display_name="x", password_hash=hash_password("original-pw"))
        )
        session.commit()
        before = session.scalar(select(User).where(User.username == "cli-short")).password_hash

    monkeypatch.setattr("getpass.getpass", answers("short", "short"))
    monkeypatch.setattr(cli, "_open_session", lambda: cli_db())

    with pytest.raises(SystemExit) as error:
        cli.main(["set-password", "cli-short"])

    assert "至少" in str(error.value)
    with cli_db() as session:
        after = session.scalar(select(User).where(User.username == "cli-short")).password_hash
        assert after == before, "a rejected password must not be stored"


def test_an_empty_password_asks_again_and_refuses(cli_db, monkeypatch) -> None:
    """Pressing Enter twice must not be read as "leave it alone"."""
    from app import cli

    monkeypatch.setattr("getpass.getpass", answers("", ""))
    monkeypatch.setattr(cli, "_open_session", lambda: cli_db())

    with pytest.raises(SystemExit):
        cli.main(["create-user", "cli-empty"])
    with cli_db() as session:
        assert session.scalar(select(User).where(User.username == "cli-empty")) is None
