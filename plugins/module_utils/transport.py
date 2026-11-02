# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP transport, injected into every system client.

A ``Transport`` returns a ``Response`` for EVERY HTTP status and raises ``TransportError`` only
when there is no usable response (refused, DNS, TLS verification, timeout, a body over
``max_bytes``). Deciding what a status means is the caller's job (``call_api`` /
``errors.error_for_status``).

Fail-closed rules shared by both transports: only http(s) URLs with a host and no userinfo are
sent (``validate_url``); only same-origin GET/HEAD redirects are followed (``next_hop``), every
other 3xx comes back to the caller as a RedirectError; a TransportError message is fixed text by
category, never the library's (server-influenced) reason string.

* ``FetchUrlTransport(module)`` -- inside a module. Goes through ``fetch_url`` so
  ``validate_certs`` (default TRUE), ``ca_path``, proxies and ``url_username`` come from the
  standard argspec (``args.http_argument_spec``).
* ``UrllibTransport()`` -- stdlib only, for scripts, agents and tests. Same defaults: TLS is
  verified unless the caller says otherwise.

Retry (``retry``/``call_api``) is bounded and applies to IDEMPOTENT methods only: a creating POST
is never repeated behind the caller's back.

Imports nothing from Ansible at module level, so a pure-Python client can use
``UrllibTransport`` without Ansible installed.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request

from .errors import (
    DecodeError,
    InvalidUrlError,
    TransportError,
    WaitTimeout,
    error_for_status,
    sanitize_url,
)
from .redact import learn_secret

DEFAULT_TIMEOUT = 30
IDEMPOTENT_METHODS = frozenset(("GET", "HEAD", "OPTIONS", "PUT", "DELETE"))


class Response(t.NamedTuple):
    status: int
    headers: dict
    text: str

    def header(self, name: str, default: str = "") -> str:
        name = name.lower()
        for k, v in self.headers.items():
            if k.lower() == name:
                return v
        return default

    def json(self) -> t.Any:
        """Parse the body as JSON whatever the content-type claims; raise DecodeError if it is not.

        Some admin endpoints serve JSON as ``text/plain``; trusting the header would turn a
        parseable body into "nothing there". An empty body is an error too, not ``None``.
        """
        try:
            return json.loads(self.text)
        except ValueError as e:
            raise DecodeError(
                "response body is not JSON (%s)" % type(e).__name__,
                status=self.status,
                content_type=self.header("content-type"),
                body=self.text,
            )


class Transport(t.Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        body: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Response: ...


def _decode(raw, content_type: str) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    charset = "utf-8"
    for part in (content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip('"') or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _is_tls_failure(reason: object) -> bool:
    if isinstance(reason, ssl.SSLError):
        return True
    text = str(reason)
    return "CERTIFICATE_VERIFY_FAILED" in text or "SSL" in text


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason)


def _transport_error(reason: object, url: str, timeout: float) -> TransportError:
    """A TransportError whose message is FIXED text chosen by category -- never the library's
    reason string, which can quote server-controlled bytes (a bad status line, a Location)."""
    if _is_tls_failure(reason):
        return TransportError("TLS verification failed", url=url, retryable=False)
    if _is_timeout(reason):
        return TransportError("request timed out after %ss" % timeout, url=url)
    kind = type(reason).__name__ if isinstance(reason, BaseException) else "error"
    return TransportError("connection failed (%s)" % kind, url=url)


REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
MAX_REDIRECTS = 5
# Bound on any response body read into memory (fail closed above it).
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _origin(url: str) -> tuple:
    p = urllib.parse.urlsplit(url)
    scheme = p.scheme.lower()
    return (
        scheme,
        (p.hostname or "").lower(),
        p.port or {"http": 80, "https": 443}.get(scheme),
    )


def validate_url(url: str) -> None:
    """Refuse a URL this library will not send a request to: not http(s), no host, embedded
    credentials (they travel in logs and errors; pass them as ``token``), or unparseable.
    Raises InvalidUrlError (a CommonError) whose message carries only the sanitised URL."""
    try:
        p = urllib.parse.urlsplit(str(url))
        scheme, host, _port = p.scheme.lower(), p.hostname, p.port
        has_userinfo = bool(p.username or p.password or "@" in p.netloc)
    except (ValueError, TypeError):
        raise InvalidUrlError("unparseable URL refused: %s" % sanitize_url(url))
    if scheme not in ("http", "https") or not host:
        raise InvalidUrlError("only http(s) URLs with a host are allowed: %s" % sanitize_url(url))
    if has_userinfo:
        raise InvalidUrlError(
            "credentials in the URL are refused (%s): pass them as the token option"
            % sanitize_url(url)
        )


# Kept for callers of the first API draft.
refuse_userinfo = validate_url


def next_hop(method: str, url: str, resp: Response, headers: dict) -> tuple | None:
    """``(url, headers)`` to follow ``resp`` with, or None to refuse it (the 3xx is then returned
    to the caller, and ``call_api`` raises RedirectError for it).

    FAIL-CLOSED: only a GET/HEAD redirect to the SAME origin (scheme, host and port all
    unchanged) is followed. A write, any change of origin (including https -> http), a missing
    Location, userinfo in it, or anything that fails to parse is refused. Never raises."""
    try:
        if resp.status not in REDIRECT_STATUSES or method not in ("GET", "HEAD"):
            return None
        location = resp.header("location")
        if not location or any(ord(c) < 0x21 or ord(c) == 0x7F for c in location):
            return None
        loc = urllib.parse.urlsplit(location)
        if (loc.scheme or location.startswith("//")) and not loc.netloc:
            return None  # "http://", "//": an origin-looking Location with no host
        new = urllib.parse.urljoin(url, location)
        validate_url(new)
        if _origin(new) != _origin(url) or new == url:
            return None
        return new, dict(headers or {})
    except Exception:  # any failure on server-controlled input refuses the redirect
        return None


class _RedirectPolicy:
    """Both transports disable their library's own redirect following and use ``next_hop``, so
    the policy is one piece of code with one set of tests."""

    max_bytes: int = MAX_RESPONSE_BYTES

    def _send_once(self, method, url, headers, body, timeout) -> Response:  # pragma: no cover
        raise NotImplementedError

    def _bounded(self, raw, url):
        if raw is not None and len(raw) > self.max_bytes:
            raise TransportError(
                "response exceeds %d bytes" % self.max_bytes, url=url, retryable=False
            )
        return raw

    def request(self, method, url, *, headers=None, body=None, timeout=DEFAULT_TIMEOUT):
        validate_url(url)
        method = method.upper()
        hdrs = dict(headers or {})
        resp = None
        for _hop in range(MAX_REDIRECTS + 1):
            resp = self._send_once(method, url, hdrs, body, timeout)
            hop = next_hop(method, url, resp, hdrs)
            if hop is None:
                return resp
            url, hdrs = hop
        return resp  # still a 3xx after MAX_REDIRECTS hops: the caller sees it as an error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Declines every 3xx WITHOUT parsing the Location (urllib's own handler parses it first and
    raises on a malformed one), so it surfaces as HTTPError(3xx) and ``next_hop`` decides."""

    def _decline(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_302 = http_error_303 = _decline
    http_error_307 = http_error_308 = _decline


class UrllibTransport(_RedirectPolicy):
    """Stdlib transport. ``validate_certs`` defaults to True; ``ca_path`` pins a CA bundle."""

    def __init__(
        self,
        *,
        validate_certs: bool = True,
        ca_path: str | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ):
        self.validate_certs = validate_certs
        self.ca_path = ca_path
        self.max_bytes = max_bytes
        ctx = (
            ssl.create_default_context(cafile=ca_path) if ca_path else ssl.create_default_context()
        )
        if not validate_certs:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx), _NoRedirect()
        )

    def _send_once(self, method, url, headers, body, timeout):
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                hdrs = dict(r.headers.items())
                raw = self._bounded(r.read(self.max_bytes + 1), url)
                return Response(r.status, hdrs, _decode(raw, r.headers.get("Content-Type", "")))
        except urllib.error.HTTPError as e:
            hdrs = dict(e.headers.items()) if e.headers else {}
            try:
                raw = e.read(self.max_bytes + 1)
            except Exception:
                raw = b""
            raw = self._bounded(raw, url)
            return Response(e.code, hdrs, _decode(raw, hdrs.get("Content-Type", "")))
        except urllib.error.URLError as e:
            raise _transport_error(e.reason, url, timeout)
        except TransportError:
            raise
        except (
            OSError,
            ValueError,
        ) as e:  # socket.timeout and ssl.SSLError are OSErrors
            raise _transport_error(e, url, timeout)
        except Exception as e:  # http.client.HTTPException etc.: a malformed server reply
            raise _transport_error(e, url, timeout)


# fetch_url's info dict mixes metadata into the header namespace; these keys are never headers.
_INFO_KEYS = frozenset(("url", "status", "msg", "body", "cookies", "cookies_string", "exception"))


class FetchUrlTransport(_RedirectPolicy):
    """Transport through ``ansible.module_utils.urls.fetch_url`` for use inside a module.

    TLS and proxy behaviour come from ``module.params`` (``validate_certs``, ``ca_path``,
    ``use_proxy``, ``url_username``...), i.e. from ``http_argument_spec``. fetch_url never follows
    a redirect itself (``follow_redirects`` is forced to ``none`` for each call); ``next_hop``
    does, same origin only. A password in the module's ``url`` is registered as a secret at
    construction, and a request to any URL with userinfo is refused. fetch_url reads an ERROR body
    whole before returning; success bodies are read with the ``max_bytes`` bound.
    """

    def __init__(self, module, *, max_bytes: int = MAX_RESPONSE_BYTES):
        self.module = module
        self.max_bytes = max_bytes
        try:
            password = urllib.parse.urlsplit(str(module.params.get("url") or "")).password
        except ValueError:
            password = None
        learn_secret(module, password)

    def _send_once(self, method, url, headers, body, timeout):
        from ansible.module_utils.urls import fetch_url

        params = self.module.params
        had, previous = "follow_redirects" in params, params.get("follow_redirects")
        params["follow_redirects"] = "none"
        try:
            r, info = fetch_url(
                self.module,
                url,
                data=body,
                headers=dict(headers),
                method=method,
                timeout=timeout,
                ca_path=params.get("ca_path"),
            )
        finally:
            if had:
                params["follow_redirects"] = previous
            else:
                params.pop("follow_redirects", None)
        status = int(info.get("status", -1))
        if status <= 0:
            raise _transport_error(str(info.get("msg", "")), url, timeout)
        hdrs = {k: v for k, v in info.items() if k not in _INFO_KEYS and isinstance(v, str)}
        if status >= 300:
            raw = info.get("body", b"")
        else:
            try:
                raw = r.read(self.max_bytes + 1) if r is not None else b""
            except Exception as e:
                raise _transport_error(e, url, timeout)
        raw = self._bounded(raw, url)
        return Response(status, hdrs, _decode(raw, hdrs.get("content-type", "")))


def join_url(base: str, path: str) -> str:
    """``base`` + ``path`` with exactly one slash between them.

    A ``path`` that is itself an absolute URL is refused: joining it would send the credential to
    whatever host the path names.
    """
    if not base:
        raise ValueError("join_url: base URL is empty")
    if "://" in (path or ""):
        raise ValueError("join_url: path %r is an absolute URL, not a path" % path)
    if not path:
        return base
    return base.rstrip("/") + "/" + path.lstrip("/")


def bearer(token: str, extra: dict | None = None) -> dict:
    """``Authorization: Bearer <token>`` plus ``extra``. An empty token is refused, not sent."""
    if not token:
        raise ValueError(
            "bearer: token is empty (an empty Bearer header 401s like a permissions fault)"
        )
    headers = {"Authorization": "Bearer " + token}
    headers.update(extra or {})
    return headers


def _retry_after(resp: Response, cap: float) -> float | None:
    value = resp.header("retry-after")
    try:
        return min(max(float(value), 0.0), cap)
    except (TypeError, ValueError):
        return None


def retry(
    call: t.Callable[[], Response],
    *,
    attempts: int = 4,
    retry_on: t.Iterable[int] = (429, 502, 503, 504),
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: t.Callable[[float], t.Any] = time.sleep,
) -> Response:
    """Call ``call()`` up to ``attempts`` times while it returns a ``retry_on`` status or raises a
    retryable TransportError. Honours a numeric ``Retry-After`` (capped at ``max_delay``).

    Returns the LAST response, which may still be a failure status: the caller decides what it
    means. Only wrap idempotent calls; ``call_api`` enforces that for you.
    """
    if attempts < 1:
        raise ValueError("retry: attempts must be >= 1")
    retry_on = frozenset(retry_on)
    for i in range(attempts):
        last_try = i == attempts - 1
        try:
            resp = call()
        except TransportError as e:
            if last_try or not e.retryable:
                raise
            sleep(min(base_delay * (2**i), max_delay))
            continue
        if resp.status not in retry_on or last_try:
            return resp
        delay = _retry_after(resp, max_delay)
        sleep(min(base_delay * (2**i), max_delay) if delay is None else delay)
    raise AssertionError("unreachable")  # pragma: no cover


def call_api(
    transport: Transport,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: t.Any = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    hints: dict | None = None,
    attempts: int = 4,
    idempotent: bool | None = None,
    sleep: t.Callable[[float], t.Any] = time.sleep,
) -> Response:
    """One API call: encode, send, retry if (and only if) idempotent, raise on a failure status.

    ``idempotent`` defaults from the method (GET/HEAD/OPTIONS/PUT/DELETE). A POST or PATCH is sent
    exactly once unless the caller asserts ``idempotent=True`` -- e.g. an API whose POST replaces
    by an explicit id.
    """
    method = method.upper()
    hdrs = dict(headers or {})
    if json_body is not None:
        if body is not None:
            raise ValueError("call_api: pass json_body or body, not both")
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "application/json")
    if idempotent is None:
        idempotent = method in IDEMPOTENT_METHODS

    def once():
        return transport.request(method, url, headers=hdrs, body=body, timeout=timeout)

    resp = retry(once, attempts=attempts if idempotent else 1, sleep=sleep)
    err = error_for_status(resp.status, resp.text, hints=hints, method=method, url=url)
    if err is not None:
        raise err
    return resp


def wait_for(
    predicate: t.Callable[[], t.Any],
    *,
    deadline_s: float,
    interval_s: float = 2.0,
    clock: t.Callable[[], float] = time.monotonic,
    sleep: t.Callable[[float], t.Any] = time.sleep,
) -> t.Any:
    """Poll ``predicate()`` until it returns something truthy (returned) or ``deadline_s`` passes
    (WaitTimeout, carrying the last value). Always evaluates at least once."""
    end = clock() + deadline_s
    while True:
        value = predicate()
        if value:
            return value
        remaining = end - clock()
        if remaining <= 0:
            raise WaitTimeout("condition not met within %ss" % deadline_s, last=value)
        sleep(min(interval_s, remaining))
