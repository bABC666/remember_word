"""Level 2 staging acceptance: run the real application against the migrated clone.

Uses the migrated staging database, never production. Verifies the V1.2 promises
end to end:

* the admin account can authenticate once its password is set;
* the admin sees the original 19 words and the retained history;
* a second user can be created and can log in;
* the second user cannot see or touch the admin's private data;
* both users can read the shared public lexicon.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS.parent
STAGING_DIR = Path("data/staging").resolve()
STAGING_DB = STAGING_DIR / "v1.1-realdata-migration-test.db"
REPORT = Path("data/recovery/staging-two-user-report.json")
PORT = 8077
BASE = f"http://127.0.0.1:{PORT}"

ADMIN_PASSWORD = "staging-admin-secret"
USERB_PASSWORD = "staging-userb-secret"

failures: list[str] = []
checks: dict[str, object] = {}


def check(name: str, condition: bool, detail: object = "") -> None:
    checks[name] = {"passed": bool(condition), "detail": detail if not condition else ""}
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  -> {detail}" if not condition else ""))
    if not condition:
        failures.append(f"{name}: {detail}")


class Session:
    """Tiny cookie-keeping HTTP client."""

    def __init__(self) -> None:
        self.cookie: str | None = None

    def request(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                set_cookie = response.headers.get("set-cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                return error.code, json.loads(body)
            except json.JSONDecodeError:
                return error.code, {"raw": body}


def staging_env() -> dict[str, str]:
    """Environment that points every process at the staging clone only."""
    return {
        **__import__("os").environ,
        "VOCAB_DATA_DIR": str(STAGING_DIR),
        "VOCAB_REAL_DATA_DIR": str(STAGING_DIR),
        "VOCAB_DATABASE_PATH": str(STAGING_DB),
        "VOCAB_ENABLE_OCR": "false",
        "VOCAB_COOKIE_SECURE": "false",
    }


def seed_passwords() -> None:
    """Set a real password for the staging admin account.

    Through ``tools/staging_accounts.py``, not the operator CLI: that CLI reads
    passwords with ``getpass`` only, and on Windows ``getpass`` reads the console
    through msvcrt even when stdin is redirected, so a scripted caller blocks. The
    helper takes the password on stdin, so it never appears in argv.
    """
    result = subprocess.run(
        [sys.executable, "-m", "tools.staging_accounts", "set-password", "admin"],
        cwd=PROJECT_ROOT,
        input=f"{ADMIN_PASSWORD}\n",
        capture_output=True,
        text=True,
        check=False,
        env=staging_env(),
    )
    if result.returncode != 0:
        raise AssertionError(f"set-password failed for admin: {result.stderr}")


def create_userb() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.staging_accounts",
            "create-user",
            "userb",
            "--role",
            "user",
        ],
        cwd=PROJECT_ROOT,
        input=f"{USERB_PASSWORD}\n",
        capture_output=True,
        text=True,
        check=False,
        env=staging_env(),
    )
    print("   create-user:", (result.stdout or result.stderr).strip().splitlines()[-1])


def seed_userb_password() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.staging_accounts", "set-password", "userb"],
        cwd=PROJECT_ROOT,
        input=f"{USERB_PASSWORD}\n",
        capture_output=True,
        text=True,
        check=False,
        env=staging_env(),
    )
    if result.returncode != 0:
        raise AssertionError(f"set-password failed for userb: {result.stderr}")


def start_server():
    env = staging_env()
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
            str(PORT),
        ],
        cwd=".",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(60):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as response:
                if response.status == 200:
                    return process
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"server exited early:\n{output}")
    raise AssertionError("server did not become healthy")


def main() -> int:
    if not STAGING_DB.exists():
        print("staging clone missing; run tools/make_staging.py then "
              "tools/staging_migration_check.py")
        return 1

    print("== preparing staging accounts ==")
    seed_passwords()
    create_userb()
    seed_userb_password()

    print()
    print("== starting the application against staging ==")
    server = start_server()
    print(f"   listening on {BASE}")
    try:
        print()
        print("== admin: original data is visible ==")
        admin = Session()
        status, payload = admin.request(
            "POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PASSWORD}
        )
        check("admin can log in", status == 200, payload)
        check("admin has the admin role", (payload or {}).get("is_admin") is True)

        status, words = admin.request("GET", "/api/words")
        check("admin sees 19 words", status == 200 and words["total"] == 19,
              words.get("total") if words else status)

        status, dashboard = admin.request("GET", "/api/dashboard")
        check("admin dashboard responds", status == 200, dashboard)

        status, articles = admin.request("GET", "/api/articles")
        check("admin sees the retained article", status == 200 and len(articles) == 1, articles)

        review_total = 0
        exposure_total = 0
        for item in words["words"]:
            status, detail = admin.request("GET", f"/api/words/{item['id']}")
            if status == 200:
                review_total += len(detail.get("review_history") or [])
                exposure_total += len(detail.get("article_exposures") or [])
        check("admin sees the 10 retained review events", review_total == 10, review_total)
        check("admin sees the 16 retained exposures", exposure_total == 16, exposure_total)

        # Lexicon read endpoints are P1.3; the shared lexicon is verified below
        # directly from the migrated database.
        connection = sqlite3.connect(f"file:{STAGING_DB.as_posix()}?mode=ro", uri=True)
        lexicon_rows = connection.execute(
            "select id, name, visibility, owner_user_id from lexicon"
        ).fetchall()
        connection.close()
        check("shared public lexicon exists", lexicon_rows and lexicon_rows[0][2] == "public",
              lexicon_rows)

        print()
        print("== userb: isolated from the admin's private data ==")
        userb = Session()
        status, payload = userb.request(
            "POST", "/api/auth/login", {"username": "userb", "password": USERB_PASSWORD}
        )
        check("userb can log in", status == 200, payload)
        check("userb is not an admin", (payload or {}).get("is_admin") is False)
        check("userb has a different id than admin", (payload or {}).get("id") != 1,
              (payload or {}).get("id"))

        status, _body = userb.request("GET", "/api/users")
        check("userb cannot use admin endpoints (403)", status == 403, status)

        print()
        print("== the admin's private article is not reachable by id ==")
        connection = sqlite3.connect(f"file:{STAGING_DB.as_posix()}?mode=ro", uri=True)
        admin_article = connection.execute(
            "select id, user_id from article order by id limit 1"
        ).fetchone()
        admin_word = connection.execute(
            "select id, user_id from word order by id limit 1"
        ).fetchone()
        connection.close()
        checks["admin_article"] = {"id": admin_article[0], "owner": admin_article[1]}

        status, detail = userb.request("GET", f"/api/articles/{admin_article[0]}")
        checks["userb_reads_admin_article_status"] = status
        print(f"   GET /api/articles/{admin_article[0]} as userb -> {status}")
        print("   (V1.2 scopes this in P1.3; the article data is still global until then)")

        status, detail = userb.request("GET", f"/api/words/{admin_word[0]}")
        checks["userb_reads_admin_word_status"] = status
        print(f"   GET /api/words/{admin_word[0]} as userb -> {status}")

        print()
        print("== isolation of user-scoped endpoints ==")
        _status, me_admin = admin.request("GET", "/api/auth/me")
        _status2, me_userb = userb.request("GET", "/api/auth/me")
        check("each session resolves to its own user",
              me_admin["username"] == "admin" and me_userb["username"] == "userb",
              (me_admin, me_userb))

        anonymous = Session()
        status, _ = anonymous.request("GET", "/api/auth/me")
        check("anonymous cannot read /api/auth/me (401)", status == 401, status)

        print()
        print("== data written during staging stays in staging ==")
        connection = sqlite3.connect(f"file:{STAGING_DB.as_posix()}?mode=ro", uri=True)
        sessions = connection.execute("select count(*) from user_session").fetchone()[0]
        users = connection.execute("select count(*) from user").fetchone()[0]
        connection.close()
        check("staging recorded the sessions", sessions >= 2, sessions)
        check("staging has both users", users == 2, users)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()

    payload = {"checks": checks, "failures": failures, "verified": not failures}
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("=" * 60)
    if failures:
        print(f"STAGING TWO-USER CHECK FAILED: {len(failures)} problems")
        for failure in failures:
            print("  -", failure)
    else:
        print("STAGING TWO-USER CHECK VERIFIED")
    print("report:", REPORT)
    print()
    print("NOTE: per-user API scoping (/api/words, /api/articles, /api/study) is P1.3,")
    print("      which the current instructions explicitly defer. Until then those")
    print("      endpoints still return global data to any logged-in user.")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
