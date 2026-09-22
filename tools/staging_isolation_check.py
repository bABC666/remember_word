"""Level 2 staging acceptance for P1.3: two users against real migrated data.

Starts from a brand new clone of the verified V1.1 source (never a staging file
that tests have already written to), migrates it to head, then runs the real
application against it and proves per-user isolation end to end:

* admin sees the original 19 words and the retained history;
* a second user sees none of the admin's private data;
* the second user can read the public system lexicon;
* once the second user builds their own learning state, the admin's statistics
  and words are unchanged.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
STAGING_DIR = Path("data/staging").resolve()
STAGING_DB = Path("data/staging/v1.3-acceptance.db").resolve()
REPORT = Path("data/recovery/staging-p13-isolation-report.json")
SOURCE = Path("data/recovery/vocab-restored-v1.1.db")
BASELINE = Path("data/recovery/baseline.json")
PORT = 8078
BASE = f"http://127.0.0.1:{PORT}"

ADMIN_PASSWORD = "staging-admin-secret"
USERB_PASSWORD = "staging-userb-secret"

failures: list[str] = []
checks: dict[str, object] = {}


def check(name: str, condition: bool, detail: object = "") -> None:
    checks[name] = {"passed": bool(condition), "detail": "" if condition else detail}
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  -> {detail}" if not condition else ""))
    if not condition:
        failures.append(f"{name}: {detail}")


def staging_env() -> dict[str, str]:
    return {
        **__import__("os").environ,
        "VOCAB_DATA_DIR": str(STAGING_DIR),
        "VOCAB_REAL_DATA_DIR": str(STAGING_DIR),
        "VOCAB_DATABASE_PATH": str(STAGING_DB),
        "VOCAB_ENABLE_OCR": "false",
        "VOCAB_COOKIE_SECURE": "false",
    }


def fresh_clone() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("verified_db", TOOLS / "verified_db.py")
    assert spec and spec.loader
    verified_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verified_db)

    baseline = verified_db.load_baseline(BASELINE)
    ok, report = verified_db.compare_against_baseline(SOURCE, baseline)
    if not ok:
        raise AssertionError(f"verified source drifted: {report['failures']}")

    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(STAGING_DB) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    STAGING_DB.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(SOURCE, STAGING_DB)
    print(f"  fresh clone: {STAGING_DB} ({STAGING_DB.stat().st_size} bytes)")


def migrate() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(Path("backend/alembic.ini").resolve()),
            "-x",
            f"db_url=sqlite:///{STAGING_DB.as_posix()}",
            "upgrade",
            "head",
        ],
        cwd="backend",
        capture_output=True,
        text=True,
        check=False,
        env=staging_env(),
    )
    if result.returncode != 0:
        raise AssertionError(f"migration failed:\n{result.stdout}\n{result.stderr}")
    print("  0003 -> head migration complete")


def cli(*arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *arguments],
        cwd="backend",
        capture_output=True,
        text=True,
        check=False,
        env=staging_env(),
    )
    if result.returncode != 0:
        raise AssertionError(f"cli {' '.join(arguments)} failed: {result.stderr}")
    return result.stdout.strip()


class Session:
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
                cookie = response.headers.get("set-cookie")
                if cookie:
                    self.cookie = cookie.split(";")[0]
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                return error.code, json.loads(body)
            except json.JSONDecodeError:
                return error.code, {"raw": body}


def start_server():
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
        env=staging_env(),
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
    print("== building a fresh staging clone ==")
    fresh_clone()
    migrate()

    print()
    print("== creating the two accounts on staging ==")
    cli("set-password", "admin", "--password", ADMIN_PASSWORD)
    cli("create-user", "userb", "--password", USERB_PASSWORD)
    print("  admin + userb ready")

    server = start_server()
    print(f"  application listening on {BASE}")
    try:
        print()
        print("== admin: the original data is intact and visible ==")
        admin = Session()
        status, payload = admin.request(
            "POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PASSWORD}
        )
        check("admin logs in", status == 200 and payload.get("is_admin") is True, payload)

        status, words = admin.request("GET", "/api/words")
        admin_words = words["words"] if status == 200 else []
        check("admin sees the original 19 words", len(admin_words) == 19, len(admin_words))
        check(
            "admin's lexicon content is intact",
            all(item["source_meanings"] is not None for item in admin_words)
            and all(item["anchor"] for item in admin_words),
        )
        check(
            "admin still sees the retained review history",
            sum(len(admin.request("GET", f"/api/words/{item['id']}")[1].get("review_history", []))
                for item in admin_words) == 10,
        )
        status, articles = admin.request("GET", "/api/articles")
        check("admin still sees the retained article", len(articles) == 1, articles)
        status, dashboard_before = admin.request("GET", "/api/dashboard")
        check("admin dashboard responds", status == 200, dashboard_before)

        print()
        print("== userb: none of the admin's private data ==")
        userb = Session()
        status, payload = userb.request(
            "POST", "/api/auth/login", {"username": "userb", "password": USERB_PASSWORD}
        )
        check("userb logs in", status == 200 and payload.get("is_admin") is False, payload)

        status, userb_words = userb.request("GET", "/api/words")
        check("userb starts with no words", userb_words["total"] == 0, userb_words)
        status, userb_articles = userb.request("GET", "/api/articles")
        check("userb starts with no articles", userb_articles == [], userb_articles)
        status, userb_dashboard = userb.request("GET", "/api/dashboard")
        check(
            "userb's dashboard shows nothing of the admin's",
            status == 200
            and userb_dashboard["due_reviews"] == 0
            and userb_dashboard["weak_words"] == 0,
            userb_dashboard,
        )
        status, userb_imports = userb.request("GET", "/api/imports")
        check("userb sees no import history", userb_imports == [], userb_imports)

        admin_word_id = admin_words[0]["id"]
        admin_article_id = articles[0]["id"]
        print()
        print("== userb probing the admin's ids ==")
        status, body = userb.request("GET", f"/api/words/{admin_word_id}")
        check("GET admin word -> 404", status == 404, status)
        check("the 404 leaks no word text", not body or "word" not in json.dumps(body, ensure_ascii=False))

        status, _ = userb.request("GET", f"/api/articles/{admin_article_id}")
        check("GET admin article -> 404", status == 404, status)

        status, _ = userb.request("GET", "/api/imports/1")
        check("GET admin import batch -> 404", status == 404, status)

        status, _ = userb.request(
            "POST", f"/api/study/words/{admin_word_id}/review", {"result": "fail"}
        )
        check("POST review on admin word -> 404", status == 404, status)

        status, _ = userb.request("GET", "/api/users")
        check("userb cannot use admin endpoints -> 403", status == 403, status)

        print()
        print("== userb can read the public system lexicon ==")
        status, lexicons = userb.request("GET", "/api/lexicons")
        system = [item for item in lexicons if item["is_system"]] if status == 200 else []
        check("userb sees the system public lexicon", bool(system), lexicons)
        check(
            "the system lexicon carries the migrated 19 entries",
            bool(system) and system[0]["entry_count"] == 19,
            system,
        )
        check(
            "userb cannot modify the system lexicon -> 403",
            userb.request(
                "PATCH", f"/api/lexicons/{system[0]['id']}", {"description": "defaced"}
            )[0]
            == 403,
            None,
        )
        check(
            "userb cannot delete the system lexicon -> 403",
            userb.request("DELETE", f"/api/lexicons/{system[0]['id']}")[0] == 403,
        )

        print()
        print("== userb builds their own learning state ==")
        status, created = userb.request("POST", "/api/lexicons", {"name": "userb 的私人词库"})
        check("userb can create a private lexicon", status == 201, created)

        status, detail = userb.request("GET", "/api/lexicons")
        private = [item for item in detail if not item["is_system"]]
        check("userb's private lexicon is listed for them only", len(private) == 1, private)
        admin_sees_it = any(
            not item["is_system"] for item in admin.request("GET", "/api/lexicons")[1]
        )
        check("admin does not see userb's private lexicon", admin_sees_it is False)

        status, dashboard_after = admin.request("GET", "/api/dashboard")
        check(
            "admin's statistics are unaffected by userb",
            dashboard_after == dashboard_before,
            (dashboard_before, dashboard_after),
        )
        status, admin_words_after = admin.request("GET", "/api/words")
        check(
            "admin still has exactly 19 words",
            admin_words_after["total"] == 19,
            admin_words_after["total"],
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()

    payload = {"checks": checks, "failures": failures, "verified": not failures}
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for value in checks.values() if value["passed"])
    print()
    print("=" * 60)
    print(f"{passed}/{len(checks)} checks passed")
    if failures:
        print("STAGING P1.3 ACCEPTANCE FAILED")
        for failure in failures:
            print("  -", failure)
    else:
        print("STAGING P1.3 ACCEPTANCE VERIFIED")
    print("report:", REPORT)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
