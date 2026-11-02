# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The one Ansible adapter: read -> normalize -> plan -> [check] -> write -> read-back.

A system client implements ``Resource`` in pure Python; a module wires it up with::

    DesiredState(module, resource, state=module.params["state"], desired={...}).run()

Guarantees:

1. Read before any write.
2. Check mode writes nothing and does NO read-back; ``changed``/``diff``/``resource`` are a
   prediction and the result carries ``predicted: true``.
3. After a write, the read-back decides ``changed`` and ``resource`` -- never the write's own
   response, so a 200 that changed nothing is ``changed: false``.
4. A write that fails, or a read-back that does not show the desired state, is a FAILURE with
   ``applied: true``, ``before`` and what is there now (``partial``/``resource``, ``remaining``).
5. Retries are the transport's business and idempotent-only (``transport.call_api``).
6. Identity is explicit: ``Resource.read()`` finds ITS resource by a client-defined key.

Not guaranteed: atomicity, rollback, protection from a concurrent writer.

Comparison is a SUBSET match: only the keys present in ``normalize(desired)`` are compared, so a
server-managed field the caller did not specify is never drift. ``normalize`` must drop
server-managed fields and canonicalise representation (ordering, formatting) -- it is what makes
``changed`` honest. Every returned structure passes through ``redact()``.
"""

from __future__ import annotations

import time
import typing as t

from .errors import BODY_LIMIT, CommonError, WaitTimeout
from .redact import redact
from .transport import wait_for

_MISSING = object()

STATES = ("present", "absent")


class Resource(t.Protocol):
    secret_keys: tuple

    def read(self) -> dict | None: ...  # None = absent

    def normalize(self, raw: dict) -> dict: ...

    def create(self, desired: dict) -> None: ...

    def update(self, current: dict, desired: dict) -> None: ...

    def delete(self, current: dict) -> None: ...


def drift(current: dict | None, desired: dict) -> dict:
    """Keys of ``desired`` whose value differs from ``current``: ``{key: {have, want}}``."""
    current = current or {}
    out = {}
    for k, want in desired.items():
        have = current.get(k, _MISSING)
        if have != want:
            out[k] = {"have": None if have is _MISSING else have, "want": want}
    return out


def plan(before: dict | None, desired: dict | None, state: str) -> str:
    """'create' | 'update' | 'delete' | 'none'."""
    if state == "absent":
        return "delete" if before is not None else "none"
    if before is None:
        return "create"
    return "update" if drift(before, desired or {}) else "none"


def predict(before: dict | None, desired: dict | None, action: str) -> dict | None:
    if action == "delete":
        return None
    if action == "create":
        return dict(desired or {})
    if action == "update":
        after = dict(before or {})
        after.update(desired or {})
        return after
    return before


class DesiredState:
    def __init__(
        self,
        module,
        resource: Resource,
        state: str,
        desired: dict | None = None,
        *,
        settle_s: float = 0.0,
        interval_s: float = 1.0,
        sleep: t.Callable[[float], t.Any] = time.sleep,
        clock: t.Callable[[], float] = time.monotonic,
    ):
        if state not in STATES:
            raise ValueError("DesiredState: state must be one of %s, got %r" % (STATES, state))
        if state == "present" and desired is None:
            raise ValueError("DesiredState: state=present needs a desired dict")
        self.module = module
        self.resource = resource
        self.state = state
        self.desired = desired
        self.settle_s = settle_s
        self.interval_s = interval_s
        self._sleep = sleep
        self._clock = clock

    # -- output, always redacted -------------------------------------------------------------

    def _redact(self, obj):
        return redact(
            obj,
            getattr(self.resource, "secret_keys", ()),
            getattr(self.module, "no_log_values", ()),
            max_len=BODY_LIMIT,  # truncation happens AFTER scrubbing, never before
        )

    def _exit(self, **result):
        self.module.exit_json(**self._redact(result))

    def _fail(self, msg, **result):
        self.module.fail_json(msg=self._redact(msg), **self._redact(result))

    # -- helpers -----------------------------------------------------------------------------

    def _read(self):
        raw = self.resource.read()
        return None if raw is None else self.resource.normalize(raw)

    def _try_read(self):
        """Best-effort read after a failed write: what is there now, or None if unreadable."""
        try:
            return {"resource": self._read(), "read_ok": True}
        except CommonError as e:
            return {"resource": None, "read_ok": False, "error": e.to_dict()}

    def _remaining(self, after, desired):
        if self.state == "absent":
            return {} if after is None else {"exists": {"have": True, "want": False}}
        if after is None:
            return {"exists": {"have": False, "want": True}}
        return drift(after, desired)

    def _read_back(self, desired):
        if self.settle_s <= 0:
            after = self._read()
            return after, self._remaining(after, desired)
        holder = {}

        def converged():
            holder["after"] = self._read()
            return not self._remaining(holder["after"], desired)

        try:
            wait_for(
                converged,
                deadline_s=self.settle_s,
                interval_s=self.interval_s,
                clock=self._clock,
                sleep=self._sleep,
            )
        except WaitTimeout:
            pass  # reported below as `remaining`, never swallowed as success
        after = holder.get("after")
        return after, self._remaining(after, desired)

    # -- the run -----------------------------------------------------------------------------

    def run(self):
        try:
            before = self._read()
        except CommonError as e:
            self._fail("read failed before any write: %s" % e, applied=False, error=e.to_dict())
            return

        desired = self.resource.normalize(self.desired) if self.state == "present" else None
        action = plan(before, desired, self.state)
        predicted = predict(before, desired, action)
        diff = {"before": before or {}, "after": predicted or {}}

        if action == "none":
            self._exit(
                changed=False,
                applied=False,
                action=action,
                diff=diff,
                resource=before,
                msg="already in the desired state",
            )
            return

        if self.module.check_mode:
            self._exit(
                changed=True,
                applied=False,
                predicted=True,
                action=action,
                diff=diff,
                resource=predicted,
                msg="check mode: would %s" % action,
            )
            return

        try:
            if action == "create":
                self.resource.create(desired)
            elif action == "update":
                self.resource.update(before, desired)
            else:
                self.resource.delete(before)
        except CommonError as e:
            now = self._try_read()
            self._fail(
                "%s failed: %s" % (action, e),
                applied=True,
                action=action,
                before=before,
                partial=now,
                error=e.to_dict(),
            )
            return

        try:
            after, remaining = self._read_back(desired)
        except CommonError as e:
            self._fail(
                "%s was sent but the read-back failed: %s" % (action, e),
                applied=True,
                action=action,
                before=before,
                error=e.to_dict(),
            )
            return

        if remaining:
            self._fail(
                "%s was accepted but the read-back does not show the desired state" % action,
                applied=True,
                action=action,
                before=before,
                resource=after,
                remaining=remaining,
            )
            return

        self._exit(
            changed=before != after,
            applied=True,
            action=action,
            diff={"before": before or {}, "after": after or {}},
            resource=after,
            msg="%s applied" % action,
        )
