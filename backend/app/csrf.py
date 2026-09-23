"""Cross-origin request forgery defence for write requests.

Three layers protect this API, and this module is the third:

1. ``SameSite=Lax`` on the session cookie, so a cross-site request does not carry a
   session at all;
2. the request body is only parsed when its ``Content-Type`` is ``application/json``
   (FastAPI's ``strict_content_type`` default), so a cross-site HTML form cannot even
   build a valid write request;
3. this check: an unsafe method must come from the same host the request was
   addressed to.

Layer 3 is the layer that still holds when

* the page is same-site but a different origin (``other.example.com`` posting to
  ``vocab.example.com``): ``SameSite`` is defined on the registrable domain, so the
  cookie *is* sent;
* Chromium applies its "Lax + POST" interop exception, which sends a Lax cookie on a
  cross-site top-level POST for two minutes after the cookie was set;
* a future deployment relaxes ``SameSite`` (a cross-site PWA embed, a native shell);
* a future endpoint accepts a form body -- the one thing layer 2 cannot stop.

It needs no client-side state, which is why this project issues no CSRF token: a
token would add a server-side secret to keep in sync across login, logout and
password change, and this application is served same-origin with no CORS.

Stated boundary: none of this defends against XSS. Script running on this origin can
forge the request and its headers together.

Everything here is a pure function: no database, no network, no module state.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Safe methods never need the check. The only side effects reachable over GET are
#: self-scoped and idempotent: ``touch_session`` refreshes the caller's own
#: ``last_seen_at``, and ``ensure_user_settings`` creates the caller's own settings
#: row on first use.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: Values that look like an origin but never are one: a sandboxed iframe and a
#: ``file://`` page both send the literal string ``null``. They are refused even when
#: ``VOCAB_CSRF_ALLOW_MISSING_ORIGIN`` is on, because "I have no origin" is not the
#: same statement as "I am not a browser".
NULL_ORIGINS: frozenset[str] = frozenset({"null"})

#: Default ports, used only to fill in a URL that omits one. The scheme itself is
#: never compared: behind a TLS-terminating proxy the browser's origin is https while
#: FastAPI sees http, and the two must still match.
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def authority_of(url: str) -> tuple[str, int] | None:
    """``(host, port)`` of an absolute URL, or ``None`` when it has no usable host."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        # urlsplit rejects a malformed IPv6 literal, and ``parts.port`` rejects a
        # non-numeric port. Both mean "this is not an origin we can compare".
        return None
    if not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme.lower(), 80)
    return host, port


def request_authority(host_header: str, scheme: str) -> tuple[str, int] | None:
    """``(host, port)`` this request was addressed to, from its ``Host`` header.

    The port has to come from somewhere when the header omits it: a public https
    origin is ``:443`` while the header may be a bare domain. ``scheme`` is the
    request's own scheme, which uvicorn rewrites from ``X-Forwarded-Proto`` when it is
    started with ``--proxy-headers`` -- required by the proxy deployment, and the
    reason the scheme is not compared directly.
    """
    header = host_header.strip()
    if not header or any(character in header for character in " \t/@\\"):
        # A Host header with userinfo, a path or whitespace is not something a browser
        # produces. Parsing it charitably is how header confusion starts.
        return None
    return authority_of(f"{scheme or 'http'}://{header}")


def request_origin(origin: str, referer: str) -> str:
    """The value to check: ``Origin`` when present, otherwise ``Referer``.

    An empty header counts as absent. The referrer is only consulted when the origin
    is missing, so a page that sets ``Referrer-Policy: no-referrer`` still passes as
    long as the browser sent ``Origin`` -- which it always does for an unsafe method.
    """
    return origin.strip() or referer.strip()


def write_is_allowed(
    *,
    method: str,
    origin: str,
    referer: str,
    host_header: str,
    scheme: str,
    allow_missing_origin: bool = False,
    trusted_origins: tuple[str, ...] = (),
) -> bool:
    """May this request proceed?

    Safe methods always may. For every other method the request must name an origin
    that is the authority it was addressed to -- or one of ``trusted_origins``, which
    exists for a proxy that rewrites ``Host`` or for a second entry point. A request
    that names no origin at all is refused unless ``allow_missing_origin`` says that
    scripted clients are welcome.
    """
    if method.upper() in SAFE_METHODS:
        return True

    candidate = request_origin(origin, referer)
    if not candidate:
        return allow_missing_origin
    if candidate.lower() in NULL_ORIGINS:
        # Explicitly origin-less, so never treated as "missing": the escape hatch for
        # scripted clients must not be talked into accepting it.
        return False

    if authority_of(candidate) == request_authority(host_header, scheme):
        return True
    expected = authority_of(candidate)
    return any(
        expected is not None and expected == authority_of(entry)
        for entry in trusted_origins
        if entry.strip()
    )
