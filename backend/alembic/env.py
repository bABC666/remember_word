import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, event, pool

import app.models  # noqa: F401
from alembic import context
from app.config import get_settings
from app.db import Base, database_path_from_url
from app.testing_guards import UnsafeDatabasePathError, assert_downgrade_allowed

config = context.config

# Tests pass an explicit database with ``-x db_url=...`` so that a migration can
# be exercised against a throwaway database instead of the live one.
_overridden_url = context.get_x_argument(as_dictionary=True).get("db_url")
config.set_main_option("sqlalchemy.url", _overridden_url or get_settings().database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _is_destructive_command() -> bool:
    """True when this run is a downgrade.

    ``alembic downgrade`` runs ``env.py`` through ``ScriptDirectory.run_env()``
    with the original command line still in ``sys.argv``, so the intent is
    visible here, before any revision is applied.
    """
    argv = [argument.lower() for argument in sys.argv]
    return "downgrade" in argv


def _guard_destructive_command() -> None:
    """Refuse a downgrade against anything but a disposable copy.

    Checked from the **final resolved database file**, so an environment variable
    that merely claims "this is staging" cannot unlock the live database.
    """
    if not _is_destructive_command():
        return
    url = config.get_main_option("sqlalchemy.url") or ""
    database_file = database_path_from_url(url)
    if database_file is None:
        if context.is_offline_mode():
            return
        raise UnsafeDatabasePathError(
            "REFUSING to run a downgrade: the target database could not be "
            f"determined from {url!r}, so it cannot be proven disposable."
        )
    assert_downgrade_allowed(database_file, action="downgrade")


_guard_destructive_command()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    if connectable.dialect.name == "sqlite":

        @event.listens_for(connectable, "connect")
        def _disable_foreign_key_enforcement(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            # SQLite cannot add or drop a foreign key in place, so a migration
            # that touches constraints rebuilds the table: create a new table,
            # copy every row, drop the old one and rename. Dropping the old table
            # while enforcement is on makes SQLite run an implicit DELETE that
            # fires the children's ON DELETE actions -- the rebuild becomes
            # silent data loss.
            #
            # This HAS to happen here, on the raw DBAPI connection before any
            # statement runs. ``PRAGMA foreign_keys`` is a silent no-op inside a
            # transaction, and setting it from inside a migration is therefore
            # unreliable (it also cannot be undone from there).
            #
            # This matches what migrations already did by accident: a fresh
            # SQLite connection defaults to foreign_keys=OFF. Stating it
            # explicitly makes the state a decision instead of a coincidence.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
