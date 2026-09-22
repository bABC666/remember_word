"""In-memory abuse protection for the login endpoint.

Two guards, both deliberately in memory and both deliberately refusing instead of
queueing:

* :class:`LoginGate` caps how many password verifications run at once. Argon2id is
  expensive in *memory* (64 MiB per verification at the current parameters), so the
  number of verifications in flight is what bounds the process. A request that has to
  wait for a slot would move the problem rather than solve it -- the thread pool would
  fill with waiters -- so a saturated gate answers immediately and the caller returns
  429.
* :class:`LoginFailureWindow` counts failed logins per client address inside a rolling
  window. It is keyed on the address only: never on the username, never on whether an
  account exists, never on a database row. That is what keeps the refusal from
  becoming a new way to learn which usernames are real.

Neither guard touches the database. The audit trail (``history_event``) stays the
record of what happened; these counters are operational state that may be lost on
restart without losing any fact.

Why memory: the deployment is a single process (``start-vocab.ps1`` starts one
uvicorn worker), so process-local counters are the whole picture, need no schema and
no migration, and cannot grow a table an unauthenticated caller writes to. The trade
is that they reset on restart and would under-count if the app were ever run with
several workers -- see the Phase 2.5 design document for the trigger conditions.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from app.config import get_settings

#: How many distinct client addresses are tracked before the least recently seen one
#: is forgotten. Bounds the counter's memory: an attacker sending traffic from many
#: addresses can churn the map but not grow it.
DEFAULT_MAX_KEYS = 10_000


class LoginGate:
    """A non-blocking cap on password verifications running at the same time."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def acquire(self) -> bool:
        """Take a slot, or return False at once. Never waits."""
        with self._lock:
            if self._in_flight >= self._limit:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1


class LoginFailureWindow:
    """Failed logins per client address inside a rolling window.

    Memory is bounded twice over:

    * at most ``threshold + 1`` timestamps are kept per address. Older ones cannot
      change the answer ("are there at least ``threshold`` failures inside the
      window?"), so dropping them is free;
    * at most ``max_keys`` addresses are tracked, least recently recorded first.

    ``clock`` is injectable so tests can move time instead of sleeping.
    """

    def __init__(
        self,
        *,
        threshold: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = max(0, threshold)
        self._window = float(window_seconds)
        self._max_keys = max(1, max_keys)
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        """False when the operator turned the check off (threshold 0)."""
        return self._threshold > 0

    @property
    def threshold(self) -> int:
        return self._threshold

    def _moment(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def _prune(self, stamps: deque[float], moment: float) -> None:
        cutoff = moment - self._window
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    def failure_count(self, client: str, *, now: float | None = None) -> int:
        """Failures from this address inside the window, as currently retained."""
        if not self.enabled:
            return 0
        moment = self._moment(now)
        with self._lock:
            stamps = self._failures.get(client)
            if not stamps:
                return 0
            self._prune(stamps, moment)
            if not stamps:
                del self._failures[client]
                return 0
            return len(stamps)

    def is_limited(self, client: str, *, now: float | None = None) -> bool:
        """True once this address has used up its failures inside the window."""
        if not self.enabled:
            return False
        return self.failure_count(client, now=now) >= self._threshold

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        """Whole seconds until enough of the window has slid past (at least 1)."""
        if not self.enabled:
            return 0
        moment = self._moment(now)
        with self._lock:
            stamps = self._failures.get(client)
            if not stamps:
                return 0
            self._prune(stamps, moment)
            if len(stamps) < self._threshold:
                return 0
            # Waiting for this stamp to expire leaves fewer than ``threshold``
            # failures behind it, which is the first moment the address is allowed
            # through again.
            expiring = stamps[len(stamps) - self._threshold]
        return max(1, int(expiring + self._window - moment + 0.999))

    def record_failure(self, client: str, *, now: float | None = None) -> None:
        """Count one refused login for this address."""
        if not self.enabled:
            return
        moment = self._moment(now)
        with self._lock:
            stamps = self._failures.get(client)
            if stamps is None:
                stamps = deque(maxlen=self._threshold + 1)
                self._failures[client] = stamps
            else:
                self._failures.move_to_end(client)
            stamps.append(moment)
            while len(self._failures) > self._max_keys:
                self._failures.popitem(last=False)

    def clear(self, client: str) -> None:
        """Forget this address entirely, which a successful login does."""
        with self._lock:
            self._failures.pop(client, None)

    def tracked(self) -> int:
        """How many addresses are currently tracked (bounded by ``max_keys``)."""
        with self._lock:
            return len(self._failures)


class LoginLimiter:
    """The two guards together, built from the configured limits."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        failure_threshold: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gate = LoginGate(max_concurrent)
        self.failures = LoginFailureWindow(
            threshold=failure_threshold,
            window_seconds=window_seconds,
            max_keys=max_keys,
            clock=clock,
        )


_limiter: LoginLimiter | None = None
_limiter_lock = threading.Lock()


def login_limiter() -> LoginLimiter:
    """The process-wide limiter, built from settings the first time it is needed.

    Built once because these are limits, not preferences: changing them is a
    restart, the same way the session lifetime is. Tests call
    :func:`reset_login_limiter` to get a fresh one.
    """
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                settings = get_settings()
                _limiter = LoginLimiter(
                    max_concurrent=settings.login_max_concurrent,
                    failure_threshold=settings.login_ip_failures,
                    window_seconds=settings.login_ip_window_seconds,
                )
    return _limiter


def reset_login_limiter() -> None:
    """Drop the process-wide limiter so the next call rebuilds it.

    For tests and for a configuration reload; production never calls this, which is
    also why a restart is what clears the counters.
    """
    global _limiter
    with _limiter_lock:
        _limiter = None


_reauth: LoginFailureWindow | None = None
_reauth_lock = threading.Lock()


def reauth_limiter() -> LoginFailureWindow:
    """The failure budget for re-authentication, keyed by user id.

    Deliberately a *separate* instance from the login window:

    * the keys differ -- login failures are anonymous and counted per client address,
      these are authenticated and counted per account, because the question here is
      which account's password is being guessed;
    * sharing one budget would let a login flood from a shared address lock a
      legitimate user out of their own settings page, and would let a fumbled
      password there eat into their login allowance.

    The concurrency gate is *not* duplicated: ``login_limiter().gate`` bounds every
    password verification in the process, whichever endpoint asks for one, because
    the memory it protects does not care who is asking.
    """
    global _reauth
    if _reauth is None:
        with _reauth_lock:
            if _reauth is None:
                settings = get_settings()
                _reauth = LoginFailureWindow(
                    threshold=settings.reauth_failures,
                    window_seconds=settings.reauth_window_seconds,
                )
    return _reauth


def reset_reauth_limiter() -> None:
    """Drop the re-auth budget so the next call rebuilds it (tests, config reload)."""
    global _reauth
    with _reauth_lock:
        _reauth = None
