"""Level 2 staging acceptance: three-account end-to-end isolation (Phase 2.8 T8).

A fresh, uniquely named copy of the live database is taken with the SQLite **online
backup API** from a read-only connection, verified against the original baseline, and
then the *real application* is started against that copy on its own port with an
explicit ``VOCAB_DATABASE_PATH``. Every account, session and business write of this
check happens in the copy; the live database is only ever read.

The matrix is the one the roadmap's T8 asks for (``docs/PROJECT_ROADMAP.md`` §6.1):

* anonymous and normal-user access to administrator endpoints is refused;
* administrator, user A and user B each log in, and private entries, learning state,
  articles and review history stay inside their owner -- a cross-user read or write and
  a genuinely missing row answer with the *same* 404;
* the shared public lexicon is readable by all three, while a normal user cannot change
  public content or read/change another user's private lexicon;
* the lexicon delete guard answers 409, and the refusal changes nothing;
* instance-level settings and backups are administrator-only;
* sessions and the daily new-word queue never mix data between A and B;
* the S-1 re-auth contract (``current_password``) and the CSRF same-origin rule are
  exercised at the HTTP level.

Secrets: the administrator password is generated here and handed to
``tools/staging_accounts.py`` on **stdin**; A and B are created through the API with
``current_password`` in the JSON body. No password reaches argv, the report or the
logs.

Usage::

    backend\\.venv\\Scripts\\python.exe tools\\staging_three_user_check.py
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "vocab.db"
DEFAULT_BASELINE = PROJECT_ROOT / "data" / "recovery" / "baseline.json"
DEFAULT_STAGING_DIR = PROJECT_ROOT / "data" / "staging"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "recovery"

#: Tables whose row counts and fingerprints are recorded before and after the matrix.
KEY_TABLES = (
    "user",
    "user_settings",
    "user_session",
    "lexicon",
    "lexicon_entry",
    "user_lexicon",
    "user_word_state",
    "review_event",
    "article",
    "article_word_exposure",
    "article_word_lookup",
    "history_event",
)

#: Every requirement the roadmap's T8 names, paired with a substring of the check name
#: that proves it ran. The report records the result, so "the matrix was complete" is
#: evidence rather than an assurance -- and a matrix that quietly lost a case fails.
REQUIRED_MATRIX: tuple[tuple[str, str], ...] = (
    ("anonymous cannot use admin endpoints", "anonymous GET /api/users is refused (401)"),
    ("anonymous writes are refused", "anonymous POST /api/settings/backup is refused (401)"),
    ("a normal user cannot list accounts", "GET /api/users is refused (403)"),
    ("a normal user cannot create accounts", "POST /api/users is refused (403)"),
    ("a normal user cannot update another account", "PATCH /api/users/{t8-beta} is refused (403)"),
    ("the three accounts log in", "admin logs in"),
    ("private entries and learning state are per account", "sees exactly its own"),
    ("cross-user and missing word detail share one 404", "denied and missing word details"),
    ("cross-user and missing article share one 404", "denied and missing articles"),
    ("a cross-user review is refused", "reviewing another account's word is 404"),
    ("a cross-user article write is refused", "completing another account's article is 404"),
    ("the public lexicon is readable by all three", "can read the public lexicon"),
    ("a normal user cannot change public content", "cannot rename public content (403)"),
    ("a private lexicon cannot be read by another user", "another user's private lexicon is 404"),
    ("a private lexicon cannot be changed by another user", "cannot be renamed (404)"),
    ("the delete guard answers 409", "holds learning records is refused (409)"),
    ("the 409 changed nothing", "the refused delete kept the lexicon"),
    ("instance settings are administrator-only", "cannot change an instance setting (403)"),
    ("backups are administrator-only", "POST /api/settings/backup is refused (403)"),
    ("sessions never mix between accounts", "only its own sessions"),
    ("the daily new-word queue never mixes accounts", "budget is untouched by"),
    ("the S-1 re-auth contract is enforced", "without current_password is rejected (422)"),
    ("the CSRF same-origin contract is enforced", "a cross-origin write is refused (403)"),
    ("the live database, WAL and SHM are untouched", "its WAL and its SHM are byte-identical"),
)
USER_A = "t8-alpha"
USER_B = "t8-beta"
WORD_A = "t8-alpha-word-"
WORD_B = "t8-beta-word-"

#: Set by ``main`` before anything runs; the helpers below read them.
STAGING_DB = Path()
STAGING_DIR = Path()
PORT = 0
SEEDED: dict[str, Any] = {}


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verified_db = load_tool("verified_db")


# --- result collection -------------------------------------------------------


class Results:
    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: object = "") -> bool:
        self.checks.append(
            {"name": name, "passed": bool(condition), "detail": "" if condition else detail}
        )
        print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + ("" if condition else f"  -> {detail}"))
        if not condition:
            self.failures.append(f"{name}: {detail}")
        return bool(condition)

    def equal(self, name: str, actual: object, expected: object) -> bool:
        return self.check(name, actual == expected, f"expected {expected!r}, got {actual!r}")


results = Results()


# --- HTTP client -------------------------------------------------------------

#: ``Origin`` is required on every unsafe method: the S-2 same-origin check compares
#: the browser's origin with the host the request was addressed to. Reads need none,
#: and "no origin at all" is expressed as ``origin=None``.
USE_OWN_ORIGIN = object()


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.own_origin = base
        self.cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        origin: object = USE_OWN_ORIGIN,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            if origin is USE_OWN_ORIGIN:
                request.add_header("Origin", self.own_origin)
            elif origin is not None:
                request.add_header("Origin", str(origin))
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8")
                set_cookie = response.headers.get("set-cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                return error.code, (json.loads(body) if body else None)
            except json.JSONDecodeError:
                return error.code, {"raw": body}

    def login(self, username: str, password: str) -> tuple[int, Any]:
        return self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )


# --- database helpers (read-only on the copy) --------------------------------


def read_only(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)


def scalar(database: Path, sql: str, parameters: tuple = ()) -> Any:
    with contextlib.closing(read_only(database)) as connection:
        row = connection.execute(sql, parameters).fetchone()
    return row[0] if row else None


def rows(database: Path, sql: str, parameters: tuple = ()) -> list[tuple]:
    with contextlib.closing(read_only(database)) as connection:
        return list(connection.execute(sql, parameters))


def table_counts(database: Path) -> dict[str, int]:
    return {
        table: int(scalar(database, f'select count(*) from "{table}"') or 0)
        for table in KEY_TABLES
    }


def table_fingerprints(database: Path) -> dict[str, object]:
    """A reproducible digest per key table: row count plus a hash of the row hashes."""
    with contextlib.closing(read_only(database)) as connection:
        fingerprint: dict[str, object] = {}
        for table in KEY_TABLES:
            with_hash = verified_db.table_fingerprint(connection, table)
            digest = hashlib.sha256(
                json.dumps(with_hash["row_hashes"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            fingerprint[table] = {"rows": with_hash["rows"], "row_hash_digest": digest}
        return fingerprint


def database_files(database: Path) -> dict[str, object]:
    """Size, mtime and hash of a database plus its sidecars: "was it written?" evidence."""
    fingerprint: dict[str, object] = {}
    for candidate in (database, *(database.with_name(database.name + s) for s in ("-wal", "-shm"))):
        if candidate.exists():
            stat = candidate.stat()
            fingerprint[candidate.name] = {
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": verified_db.sha256_file(candidate),
            }
        else:
            fingerprint[candidate.name] = None
    return fingerprint


def online_backup(source: Path, target: Path) -> Path:
    """A consistent copy of the live database; refuses to overwrite anything."""
    if target.exists():
        raise SystemExit(f"refusing to overwrite an existing file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # ``with sqlite3.connect(...)`` commits but does not close, which would keep the
    # copy's -wal locked; every connection here is closed explicitly.
    with (
        contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as origin,
        contextlib.closing(sqlite3.connect(target)) as copy,
    ):
        origin.backup(copy)
        copy.execute("pragma journal_mode=delete")
    return target


# --- application-side helpers ------------------------------------------------


def app_environment(database: Path, data_dir: Path) -> dict[str, str]:
    """Environment that points every child process at the staging copy only."""
    return {
        **os.environ,
        "VOCAB_DATA_DIR": str(data_dir),
        "VOCAB_DATABASE_PATH": str(database),
        "VOCAB_REAL_DATA_DIR": str(data_dir),
        "VOCAB_ENABLE_OCR": "false",
        "VOCAB_COOKIE_SECURE": "false",
    }


def free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def staging_account(command: str, username: str, password: str, *extra: str) -> str:
    """Create an account or set a password in the copy, with the secret on stdin."""
    result = subprocess.run(
        [sys.executable, "-m", "tools.staging_accounts", command, username, *extra],
        cwd=PROJECT_ROOT,
        input=f"{password}\n",
        capture_output=True,
        text=True,
        check=False,
        env=app_environment(STAGING_DB, STAGING_DIR),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"staging_accounts {command} {username} failed: {result.stderr.strip()}"
        )
    return (result.stdout or "").strip()


def start_server(port: int, log_path: Path):
    """Start the application against the copy, logging to a file.

    The server's output goes to a file, never to an unread pipe: uvicorn logs every
    request, and a full pipe buffer blocks the server mid-matrix -- which is exactly
    how this checker first hung. The file also keeps the evidence.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "backend",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(PROJECT_ROOT),
        env=app_environment(STAGING_DB, STAGING_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                if response.status == 200:
                    return process
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if process.poll() is not None:
                raise AssertionError(
                    f"the staging server exited early:\n{tail_of(log_path)}"
                )
    raise AssertionError(f"the staging server did not become healthy:\n{tail_of(log_path)}")


def tail_of(path: Path, lines: int = 25) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return f"<cannot read {path}: {error}>"
    return "\n".join(content[-lines:])


def seed_world(user_a: int, user_b: int, lexicon_a: int, lexicon_b: int) -> dict[str, Any]:
    """Entries, learning state, an article and an exposure for A and for B.

    Written with ``app.models`` so the rows are exactly what the application writes --
    a raw INSERT would have to guess how JSON columns are stored. The engine is bound
    first and its resolved path is asserted to be the copy, so this function cannot
    reach the live database even if it were pointed at it by mistake.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.config import get_settings
    from app.db import (
        database_path_from_url,
        get_session_factory,
        make_engine,
        set_engine,
    )
    from app.models import Article, ArticleWordExposure, LexiconEntry, UserWordState

    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{STAGING_DB.as_posix()}")
    resolved = Path(database_path_from_url(str(engine.url)) or "").resolve()
    if resolved != STAGING_DB.resolve():
        raise SystemExit(f"refusing to seed {resolved}: not the staging copy")
    set_engine(engine)

    seeded: dict[str, Any] = {}
    with get_session_factory()() as session:
        for key, user_id, lexicon_id, prefix in (
            ("a", user_a, lexicon_a, WORD_A),
            ("b", user_b, lexicon_b, WORD_B),
        ):
            entry_ids: list[int] = []
            state_ids: list[int] = []
            for index in range(3):
                entry = LexiconEntry(
                    lexicon_id=lexicon_id,
                    word=f"{prefix}{index}",
                    normalized_word=f"{prefix}{index}".casefold(),
                    source_meanings=[f"T8 私有释义 {index}"],
                    source_raw=f"{prefix}{index}",
                    default_anchor=f"t8-anchor-{index}",
                )
                session.add(entry)
                session.flush()
                entry_ids.append(entry.id)
                state = UserWordState(
                    user_id=user_id, lexicon_entry_id=entry.id, status="new"
                )
                session.add(state)
                session.flush()
                state_ids.append(state.id)
            article = Article(
                user_id=user_id,
                title=f"T8 article for {key}",
                content=f"T8 isolation fixture for {key}.",
                target_words=[f"{prefix}0"],
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleWordExposure(
                    article_id=article.id,
                    lexicon_entry_id=entry_ids[0],
                    context=f"T8 context for {key}",
                    exposure_count=1,
                )
            )
            session.commit()
            seeded[key] = {
                "entry_ids": entry_ids,
                "state_ids": state_ids,
                "article_id": article.id,
            }
    return seeded


# --- the matrix --------------------------------------------------------------


def missing_matrix_items(executed_names: list[str]) -> list[str]:
    """Which required T8 items no executed check covered."""
    return [
        label
        for label, needle in REQUIRED_MATRIX
        if not any(needle in name for name in executed_names)
    ]


def run_matrix(admin_pw: str, pass_a: str, pass_b: str, backup_dir: Path) -> None:
    base = f"http://127.0.0.1:{PORT}"
    anonymous = Client(base)
    admin = Client(base)
    user_a = Client(base)
    user_b = Client(base)

    # --- anonymous --------------------------------------------------------
    print("\n== anonymous ==")
    for path in ("/api/users", "/api/auth/me", "/api/settings", "/api/words", "/api/lexicons"):
        results.equal(f"anonymous GET {path} is refused (401)", anonymous.request("GET", path)[0], 401)
    results.equal(
        "anonymous POST /api/settings/backup is refused (401)",
        anonymous.request("POST", "/api/settings/backup")[0],
        401,
    )
    results.equal(
        "anonymous POST /api/users is refused (401)",
        anonymous.request(
            "POST",
            "/api/users",
            {
                "username": "t8-anon",
                "password": "x" * 12,
                "role": "admin",
                "current_password": "x",
            },
        )[0],
        401,
    )
    results.equal(
        "the anonymous request created no account",
        scalar(STAGING_DB, "select count(*) from user where username = 't8-anon'"),
        0,
    )

    # --- logins -----------------------------------------------------------
    print("\n== the three accounts log in ==")
    status, payload = admin.login("admin", admin_pw)
    results.equal("admin logs in", status, 200)
    results.check("the admin session reports the admin role", bool(payload and payload.get("is_admin")))
    status, me_admin = admin.request("GET", "/api/auth/me")
    results.equal("admin /api/auth/me", (status, (me_admin or {}).get("username")), (200, "admin"))
    admin_id = me_admin["id"]

    status, payload = user_a.login(USER_A, pass_a)
    results.equal(f"{USER_A} logs in", status, 200)
    results.check(f"{USER_A} is not an admin", bool(payload) and payload.get("is_admin") is False)
    status, me_a = user_a.request("GET", "/api/auth/me")
    results.equal(f"{USER_A} /api/auth/me", (status, (me_a or {}).get("username")), (200, USER_A))
    user_a_id = me_a["id"]

    status, payload = user_b.login(USER_B, pass_b)
    results.equal(f"{USER_B} logs in", status, 200)
    status, me_b = user_b.request("GET", "/api/auth/me")
    results.equal(f"{USER_B} /api/auth/me", (status, (me_b or {}).get("username")), (200, USER_B))
    user_b_id = me_b["id"]
    results.check(
        "the three accounts are distinct",
        len({admin_id, user_a_id, user_b_id}) == 3,
        (admin_id, user_a_id, user_b_id),
    )

    # --- normal users versus administrator endpoints ----------------------
    print("\n== a normal user may not use administrator endpoints ==")
    results.equal(
        f"{USER_A} GET /api/users is refused (403)", user_a.request("GET", "/api/users")[0], 403
    )
    results.equal(
        f"{USER_A} POST /api/users is refused (403)",
        user_a.request(
            "POST",
            "/api/users",
            {
                "username": "t8-by-alpha",
                "password": "x" * 12,
                "role": "admin",
                "current_password": pass_a,
            },
        )[0],
        403,
    )
    results.equal(
        "the refused create added no account",
        scalar(STAGING_DB, "select count(*) from user where username = 't8-by-alpha'"),
        0,
    )
    results.equal(
        f"{USER_A} PATCH /api/users/{{{USER_B}}} is refused (403)",
        user_a.request(
            "PATCH", f"/api/users/{user_b_id}", {"role": "admin", "current_password": pass_a}
        )[0],
        403,
    )
    results.equal(
        f"the refused PATCH left {USER_B}'s role alone",
        scalar(STAGING_DB, "select role from user where id = ?", (user_b_id,)),
        "user",
    )
    backups_before = sorted(path.name for path in backup_dir.glob("*.db"))
    results.equal(
        f"{USER_A} POST /api/settings/backup is refused (403)",
        user_a.request("POST", "/api/settings/backup")[0],
        403,
    )
    results.equal(
        "the refused backup created no file",
        sorted(path.name for path in backup_dir.glob("*.db")),
        backups_before,
    )
    results.equal(
        f"{USER_A} PUT /api/settings with an instance key is refused (403)",
        user_a.request("PUT", "/api/settings", {"deepseek_model": "t8-not-allowed"})[0],
        403,
    )
    results.check(
        "the instance model is unchanged",
        "t8-not-allowed" not in json.dumps(user_a.request("GET", "/api/settings")[1], ensure_ascii=False),
    )
    status, listed = admin.request("GET", "/api/users")
    results.check(
        "admin GET /api/users lists all three accounts",
        status == 200
        and isinstance(listed, list)
        and {item["username"] for item in listed} >= {"admin", USER_A, USER_B},
        (status, listed if status != 200 else [item["username"] for item in listed]),
    )

    # --- private content, list counts, cross-user reads -------------------
    print("\n== private entries, learning state and list counts ==")
    a_states = int(
        scalar(STAGING_DB, "select count(*) from user_word_state where user_id = ?", (user_a_id,)) or 0
    )
    b_states = int(
        scalar(STAGING_DB, "select count(*) from user_word_state where user_id = ?", (user_b_id,)) or 0
    )
    admin_states = int(
        scalar(STAGING_DB, "select count(*) from user_word_state where user_id = ?", (admin_id,)) or 0
    )
    words_a_items = user_a.request("GET", "/api/words")[1]["words"]
    results.equal(f"{USER_A} sees exactly its own {a_states} words", len(words_a_items), a_states)
    results.check(
        f"{USER_A}'s list contains only {USER_A}'s words",
        all(item["word"].startswith(WORD_A) for item in words_a_items),
        [item["word"] for item in words_a_items if not item["word"].startswith(WORD_A)],
    )
    words_b_items = user_b.request("GET", "/api/words")[1]["words"]
    results.equal(f"{USER_B} sees exactly its own {b_states} words", len(words_b_items), b_states)
    results.check(
        f"{USER_B}'s list contains only {USER_B}'s words",
        all(item["word"].startswith(WORD_B) for item in words_b_items),
        [item["word"] for item in words_b_items if not item["word"].startswith(WORD_B)],
    )
    results.equal(
        "admin sees exactly its own words",
        len(admin.request("GET", "/api/words")[1]["words"]),
        admin_states,
    )
    results.check(
        "the three word lists do not intersect",
        not ({item["word"] for item in words_a_items} & {item["word"] for item in words_b_items}),
    )

    seeded_a = SEEDED["a"]
    results.equal(
        f"{USER_A} reads its own word detail",
        user_a.request("GET", f"/api/words/state/{seeded_a['state_ids'][0]}")[0],
        200,
    )
    status_cross, body_cross = user_b.request("GET", f"/api/words/state/{seeded_a['state_ids'][1]}")
    missing_state = int(scalar(STAGING_DB, "select max(id) + 1000 from user_word_state") or 0)
    status_missing, body_missing = user_b.request("GET", f"/api/words/state/{missing_state}")
    results.equal("a cross-user word detail is 404", status_cross, 404)
    results.equal("a missing word detail is also 404", status_missing, 404)
    results.check(
        "denied and missing word details are indistinguishable",
        body_cross == body_missing,
        (body_cross, body_missing),
    )

    admin_legacy = int(scalar(STAGING_DB, "select min(id) from word") or 0)
    status_cross, body_cross = user_a.request("GET", f"/api/words/{admin_legacy}")
    status_missing, body_missing = user_a.request("GET", "/api/words/999999")
    results.equal("reading another account's legacy word id is 404", status_cross, 404)
    results.check(
        "denied and missing legacy ids are indistinguishable",
        body_cross == body_missing,
        (body_cross, body_missing),
    )

    # --- articles ---------------------------------------------------------
    print("\n== articles stay inside their owner ==")
    a_article = seeded_a["article_id"]
    b_article = SEEDED["b"]["article_id"]
    status, articles_a = user_a.request("GET", "/api/articles")
    results.check(
        f"{USER_A} lists only its own article",
        status == 200 and [item["id"] for item in articles_a] == [a_article],
        (status, articles_a),
    )
    status_cross, body_cross = user_a.request("GET", f"/api/articles/{b_article}")
    status_missing, body_missing = user_a.request("GET", "/api/articles/999999")
    results.equal("reading another account's article is 404", status_cross, 404)
    results.check(
        "denied and missing articles are indistinguishable",
        body_cross == body_missing,
        (body_cross, body_missing),
    )
    results.equal(
        "completing another account's article is 404",
        user_a.request("POST", f"/api/articles/{b_article}/complete")[0],
        404,
    )
    status, detail_b = user_b.request("GET", f"/api/articles/{b_article}")
    results.check(
        "the refused write left that article untouched",
        status == 200 and detail_b.get("completed") is False,
        detail_b,
    )

    # --- reviews ----------------------------------------------------------
    print("\n== review records are per account ==")
    b_state = SEEDED["b"]["state_ids"][0]
    results.equal(
        "reviewing another account's word is 404",
        user_a.request(
            "POST",
            f"/api/study/word-states/{b_state}/review",
            {"result": "know", "source": "daily", "review_type": "recall"},
        )[0],
        404,
    )
    results.equal(
        "the refused review wrote nothing",
        int(scalar(STAGING_DB, "select count(*) from review_event where user_id = ?", (user_b_id,)) or 0),
        0,
    )
    a_state = seeded_a["state_ids"][0]
    results.equal(
        "a user can review its own word",
        user_a.request(
            "POST",
            f"/api/study/word-states/{a_state}/review",
            {"result": "know", "source": "daily", "review_type": "recall"},
        )[0],
        200,
    )
    results.equal(
        "the review landed on the reviewer's account",
        int(scalar(STAGING_DB, "select count(*) from review_event where user_id = ?", (user_a_id,)) or 0),
        1,
    )
    status, detail_a = user_a.request("GET", f"/api/words/state/{a_state}")
    results.check(
        "the reviewer sees its own review history",
        status == 200 and len(detail_a.get("review_history") or []) == 1,
        (status, (detail_a or {}).get("review_history")),
    )
    results.equal(
        "the other account still has no review history",
        int(scalar(STAGING_DB, "select count(*) from review_event where user_id = ?", (user_b_id,)) or 0),
        0,
    )
    results.equal(
        "the exposure is visible to its owner",
        len((detail_a or {}).get("article_exposures") or []),
        1,
    )
    status, detail_b_own = user_b.request("GET", f"/api/words/state/{b_state}")
    results.equal(
        "and only to its owner",
        len((detail_b_own or {}).get("article_exposures") or []),
        1,
    )

    # --- dashboard counts -------------------------------------------------
    print("\n== dashboard counts are per account ==")
    midnight = datetime.now(UTC).strftime("%Y-%m-%d 00:00:00")
    for client, user_id, label in ((user_a, user_a_id, USER_A), (user_b, user_b_id, USER_B)):
        status, stats = client.request("GET", "/api/dashboard")
        expected = int(
            scalar(
                STAGING_DB,
                "select count(*) from user_word_state where user_id = ? and first_seen >= ?",
                (user_id, midnight),
            )
            or 0
        )
        results.check(
            f"{label}'s dashboard counts only its own new words",
            status == 200 and stats.get("today_new") == expected,
            (status, (stats or {}).get("today_new"), expected),
        )
    global_today_new = int(
        scalar(
            STAGING_DB,
            "select count(*) from user_word_state where first_seen >= ?",
            (midnight,),
        )
        or 0
    )
    stats_a = user_a.request("GET", "/api/dashboard")[1]
    results.check(
        "the dashboard count is not the whole-instance count",
        stats_a["today_new"] == a_states and stats_a["today_new"] < global_today_new,
        (stats_a["today_new"], global_today_new),
    )

    # --- the public lexicon ----------------------------------------------
    print("\n== the shared public lexicon ==")
    public_id = int(scalar(STAGING_DB, "select min(id) from lexicon where owner_user_id is null") or 0)
    results.check("the instance has a system lexicon", public_id > 0, public_id)
    for client, label in ((admin, "admin"), (user_a, USER_A), (user_b, USER_B)):
        status, listing = client.request("GET", "/api/lexicons")
        entry = next((item for item in (listing or []) if item.get("id") == public_id), None)
        results.check(
            f"{label} can list the public lexicon",
            status == 200 and entry is not None and entry.get("is_system") is True,
            (status, entry),
        )
        results.equal(
            f"{label} can read the public lexicon",
            client.request("GET", f"/api/lexicons/{public_id}")[0],
            200,
        )
    name_before = scalar(STAGING_DB, "select name from lexicon where id = ?", (public_id,))
    results.equal(
        "a normal user cannot rename public content (403)",
        user_a.request("PATCH", f"/api/lexicons/{public_id}", {"name": "t8-hacked"})[0],
        403,
    )
    results.equal(
        "the public lexicon is unchanged",
        scalar(STAGING_DB, "select name from lexicon where id = ?", (public_id,)),
        name_before,
    )
    results.equal(
        "a normal user cannot delete the system lexicon (403)",
        user_a.request("DELETE", f"/api/lexicons/{public_id}")[0],
        403,
    )
    results.equal(
        "even an admin cannot delete the system lexicon (403)",
        admin.request("DELETE", f"/api/lexicons/{public_id}")[0],
        403,
    )
    results.equal(
        "the system lexicon still exists",
        int(scalar(STAGING_DB, "select count(*) from lexicon where id = ?", (public_id,)) or 0),
        1,
    )

    # --- private lexicons -------------------------------------------------
    print("\n== private lexicons stay private ==")
    lexicon_a = SEEDED["lexicons"]["a"]
    other_id = int(scalar(STAGING_DB, "select max(id) + 500 from lexicon") or 0)
    status_cross, body_cross = user_b.request("GET", f"/api/lexicons/{lexicon_a}")
    status_missing, body_missing = user_b.request("GET", f"/api/lexicons/{other_id}")
    results.equal("another user's private lexicon is 404", status_cross, 404)
    results.check(
        "denied and missing lexicons are indistinguishable",
        body_cross == body_missing,
        (body_cross, body_missing),
    )
    listing_b = user_b.request("GET", "/api/lexicons")[1]
    results.check(
        "another user's private lexicon is not listed",
        lexicon_a not in {item["id"] for item in listing_b},
        listing_b,
    )
    results.equal(
        "another user's private lexicon cannot be renamed (404)",
        user_b.request("PATCH", f"/api/lexicons/{lexicon_a}", {"name": "t8-taken"})[0],
        404,
    )
    results.equal(
        "another user's private lexicon cannot be deleted (404)",
        user_b.request("DELETE", f"/api/lexicons/{lexicon_a}")[0],
        404,
    )
    results.equal(
        "another user's private lexicon cannot be enabled (404)",
        user_b.request("POST", f"/api/lexicons/{lexicon_a}/enable")[0],
        404,
    )
    results.equal(
        "the private lexicon is unchanged",
        scalar(STAGING_DB, "select name from lexicon where id = ?", (lexicon_a,)),
        "T8 alpha private",
    )
    results.equal(
        "no membership row was created for the intruder",
        int(
            scalar(
                STAGING_DB,
                "select count(*) from user_lexicon where user_id = ? and lexicon_id = ?",
                (user_b_id, lexicon_a),
            )
            or 0
        ),
        0,
    )
    results.equal(
        "the owner can update its own private lexicon",
        user_a.request(
            "PATCH", f"/api/lexicons/{lexicon_a}", {"description": "T8 alpha private, described"}
        )[0],
        200,
    )

    # --- the delete guard -------------------------------------------------
    print("\n== the lexicon delete guard (409) ==")
    entries_before = int(
        scalar(STAGING_DB, "select count(*) from lexicon_entry where lexicon_id = ?", (lexicon_a,)) or 0
    )
    status, body = user_a.request("DELETE", f"/api/lexicons/{lexicon_a}")
    results.equal("deleting a lexicon that holds learning records is refused (409)", status, 409)
    results.check(
        "the refusal names the learning records",
        isinstance(body, dict) and "学习记录" in str(body.get("detail", "")),
        body,
    )
    results.equal(
        "the refused delete kept the lexicon",
        int(scalar(STAGING_DB, "select count(*) from lexicon where id = ?", (lexicon_a,)) or 0),
        1,
    )
    results.equal(
        "the refused delete kept its entries",
        int(scalar(STAGING_DB, "select count(*) from lexicon_entry where lexicon_id = ?", (lexicon_a,)) or 0),
        entries_before,
    )
    results.equal(
        "the refused delete kept the owner's learning state",
        int(scalar(STAGING_DB, "select count(*) from user_word_state where user_id = ?", (user_a_id,)) or 0),
        a_states,
    )
    results.equal(
        "the owner's word list is unchanged after the refusal",
        len(user_a.request("GET", "/api/words")[1]["words"]),
        a_states,
    )
    empty_lexicon = SEEDED["lexicons"]["a_empty"]
    results.equal(
        "an empty private lexicon can still be deleted",
        user_a.request("DELETE", f"/api/lexicons/{empty_lexicon}")[0],
        200,
    )
    results.equal(
        "the empty lexicon is gone",
        int(scalar(STAGING_DB, "select count(*) from lexicon where id = ?", (empty_lexicon,)) or 0),
        0,
    )

    # --- instance settings and backups -----------------------------------
    print("\n== instance settings and backups are administrator-only ==")
    b_setting_before = scalar(
        STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (user_b_id,)
    )
    admin_setting_before = scalar(
        STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (admin_id,)
    )
    results.equal(
        "a normal user can save its own preference",
        user_a.request("PUT", "/api/settings", {"daily_new_words": 5})[0],
        200,
    )
    results.equal(
        "the preference landed on that account only",
        scalar(STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (user_a_id,)),
        5,
    )
    results.equal(
        "the other account's preference is untouched",
        scalar(STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (user_b_id,)),
        b_setting_before,
    )
    results.equal(
        "the administrator's preference is untouched",
        scalar(STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (admin_id,)),
        admin_setting_before,
    )
    setting_before = scalar(STAGING_DB, "select value from app_setting where key = 'ocr_language'")
    results.equal(
        "a normal user cannot change an instance setting (403)",
        user_a.request("PUT", "/api/settings", {"ocr_language": "t8-language"})[0],
        403,
    )
    results.equal(
        "the instance setting is unchanged",
        scalar(STAGING_DB, "select value from app_setting where key = 'ocr_language'"),
        setting_before,
    )
    results.equal(
        "an admin can create a backup",
        admin.request("POST", "/api/settings/backup")[0],
        200,
    )
    created = sorted(path.name for path in backup_dir.glob("*.db"))
    results.check(
        "the admin backup is a new file in the run's own directory",
        len(created) == len(backups_before) + 1,
        created,
    )

    # --- sessions ---------------------------------------------------------
    print("\n== sessions never mix between accounts ==")
    sessions_a = {
        int(row[0])
        for row in rows(STAGING_DB, "select id from user_session where user_id = ?", (user_a_id,))
    }
    sessions_b = {
        int(row[0])
        for row in rows(STAGING_DB, "select id from user_session where user_id = ?", (user_b_id,))
    }
    status, listed = user_a.request("GET", "/api/auth/sessions")
    listed_ids = {int(item["id"]) for item in (listed or {}).get("sessions", [])}
    results.check(
        "a user lists only its own sessions",
        status == 200 and listed_ids and listed_ids <= sessions_a,
        (status, sorted(listed_ids), sorted(sessions_a)),
    )
    results.check(
        "the listing exposes none of the other account's sessions",
        not (listed_ids & sessions_b),
        sorted(listed_ids & sessions_b),
    )
    results.check("the two accounts' sessions are disjoint", not (sessions_a & sessions_b))
    victim = min(sessions_b)
    results.equal(
        "a user cannot revoke another account's session (404)",
        user_a.request("DELETE", f"/api/auth/sessions/{victim}", {"current_password": pass_a})[0],
        404,
    )
    results.check(
        "the other account's session is still valid",
        user_b.request("GET", "/api/auth/me")[0] == 200,
    )
    results.equal(
        "a user can revoke its own other sessions",
        user_a.request(
            "POST", "/api/auth/sessions/revoke", {"scope": "others", "current_password": pass_a}
        )[0],
        200,
    )
    results.check(
        "that revocation does not touch the other accounts",
        user_b.request("GET", "/api/auth/me")[0] == 200
        and admin.request("GET", "/api/auth/me")[0] == 200,
    )

    # --- daily new words (G8) --------------------------------------------
    print("\n== the daily new-word queue is per account ==")
    # A has already reviewed one of its own new words above, so with a target of 1 its
    # own budget is spent; B's is not. That is the G8 rule, measured per account.
    user_a.request("PUT", "/api/settings", {"daily_new_words": 1})
    user_b.request("PUT", "/api/settings", {"daily_new_words": 3})
    status_a, queue_a = user_a.request("GET", "/api/study/today")
    queue_b = user_b.request("GET", "/api/study/today")[1]
    budget_a = queue_a["daily_new_words"]
    budget_b = queue_b["daily_new_words"]
    new_a = [word for word in queue_a["words"] if word["status"] == "new"]
    new_b = [word for word in queue_b["words"] if word["status"] == "new"]
    results.check(
        f"{USER_A} and {USER_B} read their own targets",
        status_a == 200 and budget_a["target"] == 1 and budget_b["target"] == 3,
        (status_a, budget_a, budget_b),
    )
    results.equal(f"{USER_A}'s own review already spent its target", budget_a["consumed_today"], 1)
    results.equal(f"so {USER_A} has no new-word room left today", budget_a["remaining"], 0)
    results.equal(f"{USER_A}'s queue therefore serves no new words", len(new_a), 0)
    results.equal(f"{USER_B}'s budget is untouched by {USER_A}", budget_b["consumed_today"], 0)
    results.check(f"{USER_B} still has new words to study", len(new_b) >= 1, queue_b)
    results.check(
        "the two queues share no words",
        not ({w["word"] for w in new_a} & {w["word"] for w in new_b}),
    )
    if new_b:
        user_b.request(
            "POST",
            f"/api/study/word-states/{new_b[0]['word_state_id']}/review",
            {"result": "know", "source": "daily", "review_type": "recall"},
        )
        after_a = user_a.request("GET", "/api/study/today")[1]["daily_new_words"]
        after_b = user_b.request("GET", "/api/study/today")[1]["daily_new_words"]
        results.equal(f"{USER_B}'s review counts for {USER_B} only", after_b["consumed_today"], 1)
        results.equal(f"{USER_A}'s counter did not move", after_a["consumed_today"], 1)

    # --- the request contracts -------------------------------------------
    print("\n== S-1 re-auth and the CSRF rule are enforced over HTTP ==")
    results.equal(
        "creating an account without current_password is rejected (422)",
        admin.request(
            "POST", "/api/users", {"username": "t8-no-pw", "password": "x" * 12, "role": "user"}
        )[0],
        422,
    )
    results.equal(
        "creating an account with a wrong current_password is rejected (400)",
        admin.request(
            "POST",
            "/api/users",
            {
                "username": "t8-bad-pw",
                "password": "x" * 12,
                "role": "user",
                "current_password": "wrong",
            },
        )[0],
        400,
    )
    results.equal(
        "the two refused creations added no account",
        int(
            scalar(
                STAGING_DB,
                "select count(*) from user where username in ('t8-no-pw','t8-bad-pw')",
            )
            or 0
        ),
        0,
    )
    results.equal(
        "updating an account without current_password is rejected (422)",
        admin.request("PATCH", f"/api/users/{user_b_id}", {"role": "admin"})[0],
        422,
    )
    results.equal(
        "updating an account with a wrong current_password is rejected (400)",
        admin.request(
            "PATCH", f"/api/users/{user_b_id}", {"role": "admin", "current_password": "wrong"}
        )[0],
        400,
    )
    results.equal(
        "the refused updates left that account alone",
        scalar(STAGING_DB, "select role from user where id = ?", (user_b_id,)),
        "user",
    )
    results.equal(
        "a cross-origin write is refused (403)",
        user_a.request(
            "PUT", "/api/settings", {"daily_new_words": 9}, origin="http://evil.example"
        )[0],
        403,
    )
    results.equal(
        "the cross-origin write changed nothing",
        scalar(STAGING_DB, "select daily_new_words from user_settings where user_id = ?", (user_a_id,)),
        1,
    )
    results.equal(
        "a write without an Origin header is refused (403)",
        user_a.request("PUT", "/api/settings", {"daily_new_words": 9}, origin=None)[0],
        403,
    )


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    global STAGING_DB, STAGING_DIR, PORT, SEEDED

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING_DIR)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--stamp", default=None, help="UTC stamp used in the new names")
    parser.add_argument("--server-log-lines", type=int, default=25, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    source = args.source.resolve()
    baseline_path = args.baseline.resolve()
    if not source.is_file():
        raise SystemExit(f"source database not found: {source}")
    stamp = args.stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    run_dir = (args.staging_dir / f"t8-three-user-{stamp}").resolve()
    STAGING_DIR = run_dir
    STAGING_DB = run_dir / "vocab.db"
    PORT = args.port or free_port()
    backup_dir = run_dir / "backups"
    report_path = (
        args.report.resolve()
        if args.report is not None
        else (DEFAULT_REPORT_DIR / f"t8-three-user-report-{stamp}.json")
    )

    print("== 0. the live database, before anything else ==")
    source_before = database_files(source)
    print(f"   {source.name}: {source_before[source.name]['sha256'][:16]}…")

    report: dict[str, Any] = {
        "acceptance": "T8 three-account end-to-end isolation (Phase 2.8)",
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_database": str(source),
        "baseline": str(baseline_path),
        "staging_copy": str(STAGING_DB),
        "staging_dir": str(run_dir),
        "staging_port": PORT,
        "accounts": {"admin": "admin", "a": USER_A, "b": USER_B},
        "checks": results.checks,
        "counts_before": {},
        "counts_after": {},
        "counts_delta": {},
        "fingerprints_before": {},
        "fingerprints_after": {},
        "source_fingerprint_before": source_before,
        "failures": results.failures,
        "verified": False,
    }
    server = None
    try:
        print("\n== 1. a new copy of the live database (SQLite online backup API) ==")
        if run_dir.exists():
            raise SystemExit(f"refusing to reuse an existing run directory: {run_dir}")
        online_backup(source, STAGING_DB)
        print(f"   copy: {STAGING_DB} ({STAGING_DB.stat().st_size} bytes)")

        print("\n== 2. the copy must be a faithful 0007 database ==")
        baseline = verified_db.load_baseline(baseline_path)
        with contextlib.closing(read_only(STAGING_DB)) as connection:
            revision = verified_db.revision(connection)
            integrity = connection.execute("pragma integrity_check").fetchone()[0]
            foreign_keys = connection.execute("pragma foreign_key_check").fetchall()
        results.equal(
            "the copy is at the baseline revision", revision, baseline.get("alembic_revision")
        )
        results.equal("the copy passes integrity_check", integrity, "ok")
        results.equal("the copy has no foreign-key violations", list(foreign_keys), [])
        verified, verification = verified_db.compare_against_baseline(
            STAGING_DB, baseline, retention_dir=baseline_path.parent / "history-retention"
        )
        results.check("the copy matches the original baseline", verified, verification.get("failures"))
        report["preconditions"] = {
            "revision": revision,
            "integrity_check": integrity,
            "foreign_key_check_violations": len(foreign_keys),
            "baseline_verified": bool(verified),
            "copy_sha256": verified_db.sha256_file(STAGING_DB),
            "copy_bytes": STAGING_DB.stat().st_size,
            "baseline_label": baseline.get("label"),
        }

        print("\n== 3. accounts, and the fixture world, in the copy only ==")
        admin_pw = f"t8-admin-{secrets.token_hex(12)}"
        pass_a = f"t8-alpha-{secrets.token_hex(12)}"
        pass_b = f"t8-beta-{secrets.token_hex(12)}"
        staging_account("set-password", "admin", admin_pw)
        print("   admin password set in the copy (stdin, never argv)")

        server = start_server(PORT, run_dir / "server.log")
        print(f"   the application is serving the copy on http://127.0.0.1:{PORT}")
        base = f"http://127.0.0.1:{PORT}"
        admin = Client(base)
        status, payload = admin.login("admin", admin_pw)
        if status != 200:
            raise SystemExit(f"the admin login against the copy failed: {status} {payload}")
        # A and B are created by the administrator through the API, with the
        # administrator's own current_password: the S-1 contract, end to end.
        for username, password in ((USER_A, pass_a), (USER_B, pass_b)):
            status, payload = admin.request(
                "POST",
                "/api/users",
                {
                    "username": username,
                    "password": password,
                    "role": "user",
                    "current_password": admin_pw,
                },
            )
            if status != 201:
                raise SystemExit(f"creating {username} failed: {status} {payload}")
            print(f"   created {username} through the API (id {payload['id']})")

        lexicons: dict[str, int] = {}
        for key, username, password, name in (
            ("a", USER_A, pass_a, "T8 alpha private"),
            ("b", USER_B, pass_b, "T8 beta private"),
            ("a_empty", USER_A, pass_a, "T8 alpha empty"),
        ):
            client = Client(base)
            client.login(username, password)
            status, payload = client.request("POST", "/api/lexicons", {"name": name})
            if status != 201:
                raise SystemExit(f"creating the {key} lexicon failed: {status} {payload}")
            lexicons[key] = int(payload["id"])

        user_a_id = int(scalar(STAGING_DB, "select id from user where username = ?", (USER_A,)) or 0)
        user_b_id = int(scalar(STAGING_DB, "select id from user where username = ?", (USER_B,)) or 0)
        SEEDED = seed_world(user_a_id, user_b_id, lexicons["a"], lexicons["b"])
        SEEDED["lexicons"] = lexicons

        counts_before = table_counts(STAGING_DB)
        fingerprints_before = table_fingerprints(STAGING_DB)

        print("\n== 4. the acceptance matrix ==")
        run_matrix(admin_pw, pass_a, pass_b, backup_dir)

        counts_after = table_counts(STAGING_DB)
        fingerprints_after = table_fingerprints(STAGING_DB)

        print("\n== 5. the writes landed in the copy, and only there ==")
        results.check(
            "the copy recorded the logins and reviews",
            counts_after["user_session"] > counts_before["user_session"]
            and counts_after["review_event"] > counts_before["review_event"],
            {key: (counts_before[key], counts_after[key]) for key in ("user_session", "review_event")},
        )
        results.check(
            "the copy's append-only audit grew",
            counts_after["history_event"] > counts_before["history_event"],
            (counts_before["history_event"], counts_after["history_event"]),
        )
        results.check(
            "the only data change the matrix made was the lexicon it deleted",
            counts_after["user"] == counts_before["user"]
            and counts_after["user_word_state"] == counts_before["user_word_state"]
            and counts_after["lexicon_entry"] == counts_before["lexicon_entry"]
            and counts_after["article"] == counts_before["article"]
            and counts_after["article_word_exposure"] == counts_before["article_word_exposure"]
            # Deleting that one empty lexicon also cascades to its own membership row.
            and counts_after["user_lexicon"] == counts_before["user_lexicon"] - 1
            and counts_after["lexicon"] == counts_before["lexicon"] - 1,
            {key: (counts_before[key], counts_after[key]) for key in KEY_TABLES},
        )
        report["counts_before"] = counts_before
        report["counts_after"] = counts_after
        report["counts_delta"] = {
            table: counts_after[table] - counts_before[table] for table in KEY_TABLES
        }
        report["fingerprints_before"] = fingerprints_before
        report["fingerprints_after"] = fingerprints_after
    except BaseException as error:  # noqa: BLE001 - the report must survive any abort
        print(f"\nABORTED: {type(error).__name__}: {error}")
        print("--- last server log lines ---")
        print(tail_of(run_dir / "server.log"))
        results.failures.append(f"aborted: {type(error).__name__}: {error}")
        report["aborted"] = f"{type(error).__name__}: {error}"
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()

    print("\n== 6. the live database was never written ==")
    source_after = database_files(source)
    results.check(
        "the live database, its WAL and its SHM are byte-identical",
        source_after == source_before,
        {
            name: (source_before[name], source_after[name])
            for name in source_before
            if source_before[name] != source_after[name]
        },
    )

    print("\n== 7. the matrix covered every required item ==")
    executed = [str(item["name"]) for item in results.checks]
    missing = missing_matrix_items(executed)
    results.check("the matrix covers every required T8 item", not missing, missing)
    report["matrix_requirements"] = {
        label: label not in missing for label, _needle in REQUIRED_MATRIX
    }

    report["checks"] = results.checks
    report["source_fingerprint_after"] = source_after
    report["failures"] = results.failures
    report["verified"] = not results.failures
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        raise SystemExit(f"refusing to overwrite an existing report: {report_path}")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    passed = sum(1 for item in results.checks if item["passed"])
    print()
    print("=" * 68)
    print(f"checks: {passed}/{len(results.checks)} passed")
    if results.failures:
        print(f"T8 THREE-ACCOUNT ACCEPTANCE FAILED: {len(results.failures)} problems")
        for failure in results.failures:
            print("  -", failure)
        print("the copy and the report are kept for investigation")
    else:
        print("T8 THREE-ACCOUNT ACCEPTANCE VERIFIED")
    print("report :", report_path)
    print("copy   :", STAGING_DB)
    return 0 if not results.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
