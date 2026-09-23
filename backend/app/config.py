from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.testing_guards import assert_not_real_data


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    uploads_dir: Path
    backups_dir: Path
    ocr_temp_dir: Path
    config_dir: Path
    frontend_dist: Path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def local_config_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def ocr_enabled(self) -> bool:
        """PaddleOCR is a local-only capability.

        The cloud deployment must be able to start without Paddle installed, so
        every OCR entry point is guarded by this flag instead of relying on the
        import failing somewhere deep in the stack.
        """
        return os.getenv("VOCAB_ENABLE_OCR", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    @property
    def session_days(self) -> int:
        raw = os.getenv("VOCAB_SESSION_DAYS", "30").strip()
        try:
            value = int(raw)
        except ValueError:
            return 30
        return value if value > 0 else 30

    @property
    def session_idle_days(self) -> int:
        """How long a session may go unused before it stops being accepted.

        The idle timeout is what makes a stolen cookie expire: the absolute
        lifetime (``session_days``) is never extended by activity, and this adds a
        second, shorter limit measured from the last use. A freshly issued session
        counts as used, because ``last_seen_at`` falls back to ``created_at``.

        ``0`` disables the idle check and leaves the absolute lifetime alone. A
        value larger than ``session_days`` is harmless: the absolute deadline still
        applies and decides.
        """
        raw = os.getenv("VOCAB_SESSION_IDLE_DAYS", "7").strip()
        try:
            value = int(raw)
        except ValueError:
            return 7
        # A negative value is treated as 0, which means "no idle check".
        return max(0, value)

    @property
    def login_max_concurrent(self) -> int:
        """How many password verifications may run at the same time.

        Argon2id costs 64 MiB of memory per verification at the current parameters,
        so this is the knob that bounds the process during a burst of login attempts.
        A request above the limit is refused immediately rather than queued: waiting
        would fill the thread pool with sleepers instead of protecting anything.

        A value below 1 falls back to the default rather than to a limit that would
        make signing in impossible.
        """
        raw = os.getenv("VOCAB_LOGIN_MAX_CONCURRENT", "8").strip()
        try:
            value = int(raw)
        except ValueError:
            return 8
        return max(1, value)

    @property
    def login_ip_failures(self) -> int:
        """Failed logins allowed per client address inside the window.

        ``0`` disables the check, leaving only the concurrency gate.
        """
        raw = os.getenv("VOCAB_LOGIN_IP_FAILURES", "10").strip()
        try:
            value = int(raw)
        except ValueError:
            return 10
        return max(0, value)

    @property
    def login_ip_window_seconds(self) -> int:
        """The rolling window the per-address failure count is measured over.

        A value below 1 falls back to the default: a zero-length window would count
        nothing, which is indistinguishable from a broken limit.
        """
        raw = os.getenv("VOCAB_LOGIN_IP_WINDOW_SECONDS", "300").strip()
        try:
            value = int(raw)
        except ValueError:
            return 300
        return value if value > 0 else 300

    @property
    def reauth_failures(self) -> int:
        """Wrong-password attempts allowed per account before re-auth is refused.

        Sensitive operations (changing the password today, revoking other sessions
        next) ask for the account's password again. This is the budget for guessing
        it, counted per user rather than per address: the caller is already
        authenticated, so the question is which account is being probed.

        ``0`` disables the check. The window is deliberately short so a legitimate
        user who fumbles their password, or an attacker burning the budget to keep
        them out, is not locked out of their own settings for long.
        """
        raw = os.getenv("VOCAB_REAUTH_FAILURES", "5").strip()
        try:
            value = int(raw)
        except ValueError:
            return 5
        return max(0, value)

    @property
    def reauth_window_seconds(self) -> int:
        """The rolling window the per-account re-auth budget is measured over."""
        raw = os.getenv("VOCAB_REAUTH_WINDOW_SECONDS", "300").strip()
        try:
            value = int(raw)
        except ValueError:
            return 300
        return value if value > 0 else 300

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies require HTTPS, so this must follow the environment.

        Hard-coding ``True`` makes local http://127.0.0.1 login impossible;
        hard-coding ``False`` is unsafe in production.
        """
        return os.getenv("VOCAB_COOKIE_SECURE", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def csrf_allow_missing_origin(self) -> bool:
        """Whether a write request may carry neither ``Origin`` nor ``Referer``.

        Off by default. A browser always sends ``Origin`` on an unsafe method, even
        same-origin, so a request without one is either a scripted client or an attempt
        to dodge the check. Turn this on only for a client that cannot be given a
        header -- and read ``docs/V1.2-PHASE2.8-B-CSRF-DESIGN.md`` first, because it
        reopens the hole for every client at once.
        """
        return os.getenv("VOCAB_CSRF_ALLOW_MISSING_ORIGIN", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def csrf_trusted_origins(self) -> tuple[str, ...]:
        """Extra origins accepted for write requests, comma separated.

        Only needed when the ``Host`` a request arrives with is not the origin the
        browser used: a proxy that rewrites the host, or a second entry point (a LAN
        address beside a domain). The ordinary case needs nothing here, because the
        request's own host is always accepted.
        """
        raw = os.getenv("VOCAB_CSRF_TRUSTED_ORIGINS", "")
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    @property
    def bootstrap_username(self) -> str:
        return os.getenv("VOCAB_BOOTSTRAP_USERNAME", "admin").strip() or "admin"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.backups_dir,
            self.ocr_temp_dir,
            self.config_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def read_local_config(self) -> dict[str, object]:
        if not self.local_config_path.exists():
            return {}
        try:
            return json.loads(self.local_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def ai_config(self) -> dict[str, str]:
        local = self.read_local_config()
        configured_model = os.getenv(
            "DEEPSEEK_MODEL", str(local.get("deepseek_model", "deepseek-flash"))
        ).strip()
        if configured_model in {"deepseek-chat", "deepseek-v4-flash"}:
            configured_model = "deepseek-flash"
        return {
            "api_key": os.getenv("DEEPSEEK_API_KEY", str(local.get("deepseek_api_key", ""))),
            "base_url": os.getenv(
                "DEEPSEEK_BASE_URL",
                str(local.get("deepseek_base_url", "https://api.deepseek.com")),
            ),
            "model": configured_model or "deepseek-flash",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = _project_root()
    data_dir = Path(os.getenv("VOCAB_DATA_DIR", str(root / "data"))).resolve()
    # An explicit database path lets operator tooling and staging clones point at
    # a specific file without moving it, and keeps the "one data directory, one
    # vocab.db" convention intact for the real deployment.
    explicit_database = os.getenv("VOCAB_DATABASE_PATH", "").strip()
    database_path = (
        Path(explicit_database).resolve() if explicit_database else data_dir / "vocab.db"
    )
    settings = Settings(
        project_root=root,
        data_dir=data_dir,
        database_path=database_path,
        uploads_dir=data_dir / "uploads",
        backups_dir=data_dir / "backups",
        ocr_temp_dir=data_dir / "ocr-temp",
        config_dir=data_dir / "config",
        frontend_dist=root / "frontend" / "dist",
    )
    # A test process must never resolve to the real user database. This is a
    # hard stop, not a warning: the V1.1 database was destroyed by exactly this.
    assert_not_real_data(settings.database_path, action="resolve the settings for")
    settings.ensure_directories()
    return settings
