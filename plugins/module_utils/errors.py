# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed errors raised by pure code; ``fail_json`` happens only at the edge.

FAIL-CLOSED by construction, because an error is exactly where server-controlled text meets a
run log:

* A message (``str(err)``) is built ONLY from the status, a fixed hint, the method and a
  sanitised URL. Server text never enters it.
* A response body is carried only as the structured ``body`` field, and only when it is a JSON
  object or array no larger than ``BODY_MAX_BYTES`` and no deeper than ``BODY_MAX_DEPTH``.
  ``redact()`` then walks it by key name and scrubs known values. Anything else -- free text,
  a JSON scalar, malformed, oversized or pathological input -- is replaced wholesale by
  ``BODY_OMITTED``. There is no attempt to find secrets inside free text.
* A URL is stored sanitised: http(s) with a host, no userinfo, the whole query masked.
"""

from __future__ import annotations

import json
import urllib.parse

# redact(max_len=...) truncates result strings to this AFTER scrubbing -- never before.
BODY_LIMIT = 2000
# Hard cap on a body considered at all, checked BEFORE it is parsed.
BODY_MAX_BYTES = 16 * 1024
BODY_MAX_DEPTH = 32
BODY_OMITTED = "[body omitted]"

MASK = "********"

RETRYABLE_STATUSES = (429, 502, 503, 504)


def sanitize_url(url) -> str:
    """``scheme://host[:port]/path`` plus ``?********`` when there was a query.

    Anything that is not an http(s) URL with a host -- or that does not parse -- is masked whole:
    a sanitiser that guesses at odd input is a parser differential waiting to leak."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except (ValueError, TypeError):
        return MASK
    if scheme not in ("http", "https") or not host:
        return MASK
    if ":" in host:
        host = "[%s]" % host
    netloc = host + (":%d" % port if port else "")
    out = "%s://%s%s" % (scheme, netloc, urllib.parse.quote(parts.path, safe="/%:@"))
    if parts.query:
        out += "?" + MASK
    return out


def _depth_ok(obj, limit: int) -> bool:
    """Iterative depth check (no recursion: the input is server-controlled)."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if d > limit:
            return False
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)
    return True


def safe_body(body):
    """The structured form of a server body, or BODY_OMITTED. Never raises."""
    try:
        if body is None or body == "" or body == b"":
            return ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        body = str(body)
        if len(body.encode("utf-8", errors="replace")) > BODY_MAX_BYTES:
            return BODY_OMITTED
        parsed = json.loads(body)
        if not isinstance(parsed, (dict, list)) or not _depth_ok(parsed, BODY_MAX_DEPTH):
            return BODY_OMITTED
        return parsed
    except (ValueError, RecursionError, TypeError, MemoryError):
        return BODY_OMITTED
    except Exception:  # fail closed on anything else server input can provoke
        return BODY_OMITTED


class CommonError(Exception):
    """Base of every error this library raises."""

    retryable = False

    def to_dict(self) -> dict:
        return {
            "type": type(self).__name__,
            "message": str(self),
            "retryable": self.retryable,
        }


class InvalidUrlError(CommonError, ValueError):
    """A URL this library will not send a request to: not http(s), no host, embedded
    credentials, or unparseable. The message carries only the sanitised form."""


class TransportError(CommonError):
    """No usable HTTP response: refused, reset, DNS, TLS verification, timeout, oversized."""

    def __init__(self, message: str, *, url: str = "", retryable: bool = True):
        message = str(message)
        if url:
            message = message.replace(str(url), sanitize_url(url))
        super().__init__(message)
        self.url = sanitize_url(url) if url else ""
        self.retryable = retryable

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["url"] = self.url
        return d


class DecodeError(CommonError):
    """A response that should have been JSON was not. Never presented as 'nothing there'.

    By definition the body did not parse, so it is never carried -- only its size."""

    def __init__(self, message: str, *, status: int = 0, content_type: str = "", body: str = ""):
        super().__init__(message)
        self.status = status
        self.content_type = content_type
        self.body = BODY_OMITTED if body else ""
        self.body_length = len(body or "")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(
            status=self.status,
            content_type=self.content_type,
            body=self.body,
            body_length=self.body_length,
        )
        return d


class WaitTimeout(CommonError):
    """``wait_for`` reached its deadline without the predicate becoming true."""

    def __init__(self, message: str, *, last=None):
        super().__init__(message)
        self.last = last


class ApiError(CommonError):
    """An HTTP response with a status that means the call did not do what was asked."""

    def __init__(
        self,
        status: int,
        body=None,
        *,
        hint: str = "",
        method: str = "",
        url: str = "",
        retryable: bool | None = None,
    ):
        self.status = int(status)
        self.body = safe_body(body)
        self.hint = hint
        self.method = method
        self.url = sanitize_url(url) if url else ""
        self.retryable = (self.status in RETRYABLE_STATUSES) if retryable is None else retryable
        super().__init__(self._message())

    def _message(self) -> str:
        where = (" %s %s" % (self.method, self.url)).rstrip() if (self.method or self.url) else ""
        msg = "HTTP %d%s" % (self.status, (" from" + where) if where else "")
        if self.hint:
            msg += ": " + self.hint
        return msg

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(
            status=self.status,
            body=self.body,
            hint=self.hint,
            method=self.method,
            url=self.url,
        )
        return d


class AuthError(ApiError):
    """401/403: the credential was refused or lacks the permission."""


class NotFoundError(ApiError):
    """404: the path or the resource does not exist."""


class RejectedError(ApiError):
    """Any other 4xx: the server understood the request and refused its content."""


class UnsupportedError(ApiError):
    """405/501: this server (or firmware) does not implement the call."""


class RedirectError(ApiError):
    """A 3xx the transport refused to follow: a write, any change of origin (scheme, host or
    port -- including https -> http), a bad or missing Location, or too many hops. The response
    is not the resource, so it is not success."""


_DEFAULT_HINTS = {
    400: "bad request",
    401: "authentication failed (missing, wrong or expired credential)",
    403: "forbidden (credential valid but lacks permission)",
    404: "not found",
    405: "method not allowed by this server",
    409: "conflict with the current state of the resource",
    422: "unprocessable entity (validation failed)",
    429: "rate limited",
    500: "server error",
    501: "not implemented by this server",
    502: "bad gateway",
    503: "service unavailable",
    504: "gateway timeout",
}


def _hint(status: int, hints: dict | None) -> str:
    h = (hints or {}).get(status) or _DEFAULT_HINTS.get(status)
    if h:
        return h
    if 300 <= status < 400:
        return "redirect not followed (a write, a change of origin, or too many hops)"
    return "unexpected status"


def interpret_status(status: int, hints: dict | None = None) -> str | None:
    """None for a 2xx; otherwise a one-line explanation (status + hint, never server text).

    ``hints`` maps a status to a system-specific explanation and takes precedence over the
    generic one (e.g. ``{404: "the integration is not installed"}``).
    """
    status = int(status)
    if 200 <= status < 300:
        return None
    return "HTTP %d: %s" % (status, _hint(status, hints))


def error_for_status(
    status: int,
    body=None,
    *,
    hints: dict | None = None,
    method: str = "",
    url: str = "",
) -> ApiError | None:
    """The typed error a status means, or None for success (2xx only).

    A 3xx reaching here is a redirect the transport REFUSED to follow, so it is an error."""
    status = int(status)
    if 200 <= status < 300:
        return None
    if 300 <= status < 400:
        cls = RedirectError
    elif status in (401, 403):
        cls = AuthError
    elif status == 404:
        cls = NotFoundError
    elif status in (405, 501):
        cls = UnsupportedError
    elif 400 <= status < 500 and status != 429:
        cls = RejectedError
    else:
        cls = ApiError
    return cls(status, body, hint=_hint(status, hints), method=method, url=url)
