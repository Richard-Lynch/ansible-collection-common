# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secret redaction for everything a module returns or fails with.

Two nets, both applied by ``redact()``, and deliberately NO attempt to find secrets in free text
(that is a parser differential; free-text server bodies are dropped wholesale by
``errors.safe_body`` instead):

* by KEY -- a mapping key that names a secret has its value replaced, whatever the value is.
  FAIL-CLOSED: a key is secret if ANY of its segments, or its segments run together, name a
  secret word (``token``, ``apiKey``, ``X-Auth-Token``, ``user_name_password``,
  ``token_url``...). The only exceptions are the exact names in ``NON_SECRET_KEYS``;
* by VALUE -- every known secret string (``module.no_log_values``: the ``no_log`` parameters plus
  anything registered with ``learn_secret``), and its JSON-escaped, URL-encoded and base64 forms,
  is cut out of every string, wherever it appears.

Truncation (``max_len``) happens after scrubbing, so a secret cut at the boundary cannot leak its
prefix. A structure too deep to walk is replaced whole by ``REDACTED`` rather than raising.

AnsibleModule also strips ``no_log_values`` from ``exit_json``/``fail_json``; this layer does not
rely on that, because a ``diff`` or an error dict is built BEFORE it reaches the module, and the
key net covers secrets the module was never told about (a server returning ``api_key`` in a body).
"""

from __future__ import annotations

import base64
import json
import re
import typing as t
import urllib.parse

REDACTED = "********"

# A key is secret if any snake_case SEGMENT is one of these ...
_SECRET_SEGMENTS = frozenset(
    (
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "pass",
        "pwd",
        "credential",
        "credentials",
        "auth",
        "authorization",
        "cookie",
        "session",
        "apikey",
        "privatekey",
        "accesskey",
        "secretkey",
        "signature",
    )
)
# ... or the segments run together CONTAIN one of these (APIKey, accesskey, x-api-key, bearertoken).
_SECRET_COMPACT = (
    "token",
    "password",
    "passwd",
    "passphrase",
    "secret",
    "apikey",
    "privatekey",
    "accesskey",
    "credential",
    "authorization",
)
# The ONLY exceptions: exact normalised names known to hold no secret. Kept small on purpose;
# adding a name here is a reviewed decision, never a pattern.
NON_SECRET_KEYS = frozenset(
    (
        "token_count",
        "tokens_used",
        "token_type",
        "max_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "password_policy",
    )
)

_CAMEL_1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _norm_key(key: object) -> str:
    if isinstance(key, bytes):
        key = key.decode("utf-8", errors="replace")
    k = _CAMEL_2.sub(r"\1_\2", _CAMEL_1.sub(r"\1_\2", str(key).strip()))
    return _NON_WORD.sub("_", k.lower()).strip("_")


def is_secret_key(key: object, secret_keys: t.Iterable[t.Any] = ()) -> bool:
    k = _norm_key(key)
    if k in {_norm_key(s) for s in secret_keys}:
        return True
    if k in NON_SECRET_KEYS:
        return False
    segments = [s for s in k.split("_") if s]
    if any(s in _SECRET_SEGMENTS for s in segments):
        return True
    compact = "".join(segments)
    return any(c in compact for c in _SECRET_COMPACT)


def _variants(value: str) -> list:
    """The value plus the encodings it commonly travels in."""
    out = {value}
    out.add(json.dumps(value)[1:-1])
    out.add(json.dumps(value, ensure_ascii=False)[1:-1])
    out.add(urllib.parse.quote(value, safe=""))
    out.add(urllib.parse.quote(value))
    out.add(urllib.parse.quote_plus(value))
    raw = value.encode("utf-8")
    for enc in (base64.b64encode(raw), base64.urlsafe_b64encode(raw)):
        b64 = enc.decode("ascii")
        out.add(b64)
        out.add(b64.rstrip("="))
    return [v for v in out if v]


def _scrub_values(text: str, values: t.Sequence[str]) -> str:
    for v in values:
        if v in text:
            text = text.replace(v, REDACTED)
    return text


def redact(
    obj: t.Any,
    secret_keys: t.Iterable[t.Any] = (),
    no_log_values: t.Iterable[t.Any] = (),
    max_len: int | None = None,
) -> t.Any:
    """A deep copy of ``obj`` with secrets removed by key and by value. ``obj`` is not modified.

    Values are matched longest-first (with their encoded forms) so a secret containing another is
    removed whole; an empty value is ignored. Strings longer than ``max_len`` are truncated after
    scrubbing. Never raises on a pathological structure: it is replaced by ``REDACTED``.
    """
    keys = tuple(secret_keys)
    variants: set = set()
    for v in no_log_values:
        if v not in (None, ""):
            variants.update(_variants(str(v)))
    values = sorted(variants, key=len, reverse=True)

    def text(s: str) -> str:
        s = _scrub_values(s, values)
        if max_len is not None and len(s) > max_len:
            s = "%s...[truncated %d chars]" % (s[:max_len], len(s) - max_len)
        return s

    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                nk = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k
                if isinstance(nk, str):
                    nk = _scrub_values(nk, values)
                out[nk] = REDACTED if is_secret_key(k, keys) and v is not None else walk(v)
            return out
        if isinstance(o, (list, tuple, set, frozenset)):
            # A tuple stays a tuple; a set becomes a list (two scrubbed members may now collide,
            # and a module result cannot carry a set anyway).
            items = [walk(i) for i in o]
            return tuple(items) if isinstance(o, tuple) else items
        if isinstance(o, bytes):
            return text(o.decode("utf-8", errors="replace"))
        if isinstance(o, str):
            return text(o)
        if isinstance(o, BaseException):
            return text(str(o))
        return o

    try:
        return walk(obj)
    except RecursionError:
        return REDACTED


def learn_secret(module, value: str | None) -> None:
    """Register a secret learned at run time (a minted token, a session id) BEFORE first use, so
    neither AnsibleModule's output filter nor ``redact`` can let it through."""
    if value:
        module.no_log_values.add(value)
