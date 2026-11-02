# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""DesiredState end to end: a real AnsibleModule, FetchUrlTransport, and the fake server.

The toy ``ItemResource`` below is what a per-system client looks like: pure Python,
transport injected, ``normalize`` dropping server-managed fields and canonicalising order.
"""

from __future__ import annotations

import json

import pytest
from ansible_collections.richard_lynch.common.plugins.module_utils.args import (
    http_argument_spec,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.desired_state import (
    DesiredState,
    drift,
    plan,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.errors import (
    NotFoundError,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.redact import (
    learn_secret,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.transport import (
    FetchUrlTransport,
    bearer,
    call_api,
    join_url,
)

GOOD_TOKEN = "tok-fixture-credential-for-unit-tests"

SPEC = http_argument_spec(
    extra={
        "name": {"type": "str", "required": True},
        "value": {"type": "str"},
        "tags": {"type": "list", "elements": "str", "default": []},
        "state": {
            "type": "str",
            "default": "present",
            "choices": ["present", "absent"],
        },
    }
)


class ItemResource:
    secret_keys = ("pin",)

    def __init__(self, transport, url, token, name, timeout=30):
        self.t = transport
        self.url = join_url(url, "items/" + name)
        self.headers = bearer(token)
        self.timeout = timeout

    def read(self):
        try:
            return call_api(
                self.t, "GET", self.url, headers=self.headers, timeout=self.timeout
            ).json()
        except NotFoundError:
            return None

    def normalize(self, raw):
        return {"value": raw.get("value"), "tags": sorted(raw.get("tags") or [])}

    def _put(self, desired):
        call_api(
            self.t,
            "PUT",
            self.url,
            headers=self.headers,
            json_body=desired,
            timeout=self.timeout,
        )

    def create(self, desired):
        self._put(desired)

    def update(self, current, desired):
        self._put(desired)

    def delete(self, current):
        call_api(self.t, "DELETE", self.url, headers=self.headers, timeout=self.timeout)


def run(make_module, srv, check_mode=False, token=GOOD_TOKEN, **args):
    params = {"url": srv.url, "token": token, "name": "alpha"}
    params.update(args)
    module = make_module(SPEC, params, check_mode=check_mode)
    p = module.params
    res = ItemResource(FetchUrlTransport(module), p["url"], p["token"], p["name"])
    desired = {"value": p["value"], "tags": p["tags"]} if p["state"] == "present" else None
    with pytest.raises((make_module.ExitJson, make_module.FailJson)) as ei:
        DesiredState(module, res, p["state"], desired).run()
    return isinstance(ei.value, make_module.ExitJson), ei.value.result, module


def writes(srv):
    return [r for r in srv.state.requests if r[0] in ("PUT", "DELETE", "POST")]


# -- check mode ----------------------------------------------------------------------------------


def test_check_mode_reports_the_change_without_acting(make_module, fake_http):
    ok, r, _extra = run(make_module, fake_http, check_mode=True, value="v1", tags=["b", "a"])
    assert ok and r["changed"] is True and r["predicted"] is True and r["applied"] is False
    assert r["action"] == "create"
    assert r["diff"] == {"before": {}, "after": {"value": "v1", "tags": ["a", "b"]}}
    assert writes(fake_http) == [], "check mode must write nothing"
    assert fake_http.state.items == {}
    # read-before-write, and NO read-back in check mode: exactly one GET
    assert fake_http.state.requests == [("GET", "/items/alpha")]


def test_check_mode_update_and_delete_predictions(make_module, fake_http):
    run(make_module, fake_http, value="v1", tags=["a"])
    fake_http.state.requests.clear()
    _ok, r, _extra = run(make_module, fake_http, check_mode=True, value="v2", tags=["a"])
    assert r["action"] == "update" and r["changed"] is True
    assert r["diff"]["before"]["value"] == "v1" and r["diff"]["after"]["value"] == "v2"
    _ok, r, _extra = run(make_module, fake_http, check_mode=True, state="absent")
    assert r["action"] == "delete" and r["changed"] is True and r["resource"] is None
    assert writes(fake_http) == []
    assert fake_http.state.items["alpha"]["value"] == "v1"


# -- apply + idempotence -------------------------------------------------------------------------


def test_apply_then_second_run_is_unchanged(make_module, fake_http):
    ok, r, _extra = run(make_module, fake_http, value="v1", tags=["b", "a"])
    assert ok and r["changed"] is True and r["applied"] is True and r["action"] == "create"
    # the read-back decides `resource`, normalised (server stored tags reverse-sorted + id/updated_at)
    assert r["resource"] == {"value": "v1", "tags": ["a", "b"]}
    assert fake_http.state.writes == 1

    ok, r, _extra = run(make_module, fake_http, value="v1", tags=["a", "b"])
    assert ok and r["changed"] is False and r["applied"] is False and r["action"] == "none"
    assert fake_http.state.writes == 1, "an idempotent second run must not write"


def test_update_and_absent(make_module, fake_http):
    run(make_module, fake_http, value="v1", tags=[])
    ok, r, _extra = run(make_module, fake_http, value="v2", tags=[])
    assert ok and r["changed"] and r["action"] == "update"
    assert r["diff"] == {
        "before": {"value": "v1", "tags": []},
        "after": {"value": "v2", "tags": []},
    }

    ok, r, _extra = run(make_module, fake_http, state="absent")
    assert ok and r["changed"] and r["action"] == "delete" and r["resource"] is None
    assert "alpha" not in fake_http.state.items
    ok, r, _extra = run(make_module, fake_http, state="absent")
    assert ok and r["changed"] is False


def test_accepted_write_that_did_not_land_is_a_failure(make_module, fake_http):
    """A 200 is not evidence: the read-back is. A server that accepts and stores nothing fails."""
    fake_http.state.ignore_writes = True
    ok, r, _extra = run(make_module, fake_http, value="v1", tags=[])
    assert not ok and r["applied"] is True
    assert r["remaining"] == {"exists": {"have": False, "want": True}}


# -- failures: partial writes and redaction ------------------------------------------------------


def test_partial_failure_reports_applied_before_and_partial(make_module, fake_http):
    run(make_module, fake_http, value="v1", tags=[])
    fake_http.state.fail_after_write = 400
    ok, r, _extra = run(make_module, fake_http, value="v2", tags=[])
    assert not ok and r["applied"] is True and r["action"] == "update"
    assert r["before"] == {"value": "v1", "tags": []}
    assert r["partial"] == {"resource": {"value": "v2", "tags": []}, "read_ok": True}
    assert r["error"]["status"] == 400


def test_secrets_never_reach_error_messages_or_results(make_module, fake_http):
    """The server echoes the Authorization header in its error body; the payload handed to
    fail_json (BEFORE Ansible's own no_log filter) must not contain the token anywhere."""
    run(make_module, fake_http, value="v1", tags=[])
    fake_http.state.fail_after_write = 400
    ok, r, _extra = run(make_module, fake_http, value="v2", tags=[])
    assert not ok
    blob = json.dumps(r)
    assert "auth was" in blob, "the echo must be present for this test to mean anything"
    assert GOOD_TOKEN not in blob


def test_wrong_token_echo_is_redacted_too(make_module, fake_http):
    bad = "wrong-token-77f0c2d1e9a8b3c4"
    ok, r, _extra = run(make_module, fake_http, token=bad, value="v1", tags=[])
    assert not ok and r["applied"] is False
    assert "invalid credentials" in json.dumps(r)
    assert bad not in json.dumps(r)


def test_learned_secret_is_redacted_from_results(make_module, fake_http):
    learned = "minted-session-9d8c7b6a5f4e"

    class Leaky(ItemResource):
        def normalize(self, raw):
            out = super().normalize(raw)
            out["note"] = "session %s" % learned
            out["pin"] = "1234"
            return out

    module = make_module(
        SPEC, {"url": fake_http.url, "token": GOOD_TOKEN, "name": "alpha", "value": "v"}
    )
    learn_secret(module, learned)
    res = Leaky(FetchUrlTransport(module), fake_http.url, GOOD_TOKEN, "alpha")
    with pytest.raises(make_module.ExitJson) as ei:
        DesiredState(module, res, "present", {"value": "v"}).run()
    blob = json.dumps(ei.value.result)
    assert learned not in blob and '"pin": "1234"' not in blob
    assert "session ********" in blob


# -- pure planning -------------------------------------------------------------------------------


def test_plan_and_drift_are_subset_matches():
    assert plan(None, {"a": 1}, "present") == "create"
    assert plan({"a": 1, "server_id": 9}, {"a": 1}, "present") == "none"
    assert plan({"a": 1}, {"a": 2}, "present") == "update"
    assert plan(None, None, "absent") == "none"
    assert plan({"a": 1}, None, "absent") == "delete"
    assert drift({"a": 1}, {"a": 1, "b": None}) == {"b": {"have": None, "want": None}}


def test_constructor_refuses_bad_input(make_module, fake_http):
    module = make_module(SPEC, {"url": fake_http.url, "token": GOOD_TOKEN, "name": "alpha"})
    with pytest.raises(ValueError):
        DesiredState(module, None, "presnt", {})
    with pytest.raises(ValueError):
        DesiredState(module, None, "present", None)
