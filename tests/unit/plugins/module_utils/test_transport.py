# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport against a real (fake) server: TLS verification on by default, timeouts, bounded
idempotent-only retry, status -> typed error, JSON decoding that cannot read as 'empty'."""

from __future__ import annotations

import pytest
from ansible_collections.richard_lynch.common.plugins.module_utils import errors
from ansible_collections.richard_lynch.common.plugins.module_utils.args import (
    http_argument_spec,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.transport import (
    FetchUrlTransport,
    Response,
    UrllibTransport,
    bearer,
    call_api,
    join_url,
    retry,
    wait_for,
)

GOOD_TOKEN = "tok-fixture-credential-for-unit-tests"
AUTH = {"Authorization": "Bearer " + GOOD_TOKEN}


class Sleeps(list):
    def __call__(self, s):
        self.append(s)


def _fetch_transport(make_module, url, **extra):
    args = {"url": url, "token": GOOD_TOKEN}
    args.update(extra)
    return FetchUrlTransport(make_module(http_argument_spec(), args))


@pytest.fixture(params=["urllib", "fetch_url"])
def transport_factory(request, make_module):
    """Every behavioural test runs against BOTH transports: they must agree."""

    def build(url, validate_certs=None, ca_path=None):
        if request.param == "urllib":
            kw = {}
            if validate_certs is not None:
                kw["validate_certs"] = validate_certs
            if ca_path is not None:
                kw["ca_path"] = ca_path
            return UrllibTransport(**kw)
        extra = {}
        if validate_certs is not None:
            extra["validate_certs"] = validate_certs
        if ca_path is not None:
            extra["ca_path"] = ca_path
        return _fetch_transport(make_module, url, **extra)

    build.kind = request.param
    return build


# -- plain HTTP --------------------------------------------------------------------------------


def test_success_and_error_statuses_are_responses_not_exceptions(fake_http, transport_factory):
    tr = transport_factory(fake_http.url)
    ok = tr.request("GET", fake_http.url + "/echo", headers=AUTH)
    assert ok.status == 200 and ok.json()["echo"] is None
    missing = tr.request("GET", fake_http.url + "/items/nope", headers=AUTH)
    assert missing.status == 404
    assert "no item nope" in missing.text
    assert missing.header("content-type") == "application/json"


def test_connection_refused_is_retryable_transport_error(transport_factory):
    # Port 9 (discard) on loopback: nothing listens there in a test environment.
    tr = transport_factory("http://127.0.0.1:9")
    with pytest.raises(errors.TransportError) as ei:
        tr.request("GET", "http://127.0.0.1:9/x", timeout=2)
    assert ei.value.retryable is True


def test_timeout_raises_transport_error(fake_http, transport_factory):
    fake_http.state.slow_s = 2.0
    tr = transport_factory(fake_http.url)
    with pytest.raises(errors.TransportError) as ei:
        tr.request("GET", fake_http.url + "/echo", headers=AUTH, timeout=0.5)
    assert "timed out" in str(ei.value).lower()
    assert ei.value.retryable is True


# -- TLS: verification is ON unless the caller turns it off --------------------------------------


def test_tls_verify_is_on_by_default(fake_https, transport_factory):
    tr = transport_factory(fake_https.url)
    with pytest.raises(errors.TransportError) as ei:
        tr.request("GET", fake_https.url + "/echo", headers=AUTH, timeout=5)
    assert ei.value.retryable is False, "a certificate failure must not be retried"
    assert fake_https.state.requests == [], "the request must not reach an unverified server"


def test_tls_ca_path_trusts_the_pinned_ca(fake_https, transport_factory):
    tr = transport_factory(fake_https.url, ca_path=fake_https.ca_path)
    assert tr.request("GET", fake_https.url + "/echo", headers=AUTH, timeout=5).status == 200


def test_tls_validate_certs_false_is_an_explicit_opt_out(fake_https, transport_factory):
    tr = transport_factory(fake_https.url, validate_certs=False)
    assert tr.request("GET", fake_https.url + "/echo", headers=AUTH, timeout=5).status == 200


def test_argspec_defaults_validate_certs_true(make_module):
    m = make_module(http_argument_spec(), {"url": "https://x.invalid", "token": "t"})
    assert m.params["validate_certs"] is True
    assert m.params["timeout"] == 30


# -- retry: bounded, honours Retry-After, idempotent only ----------------------------------------


def test_retry_recovers_from_transient_503(fake_http):
    fake_http.state.flaky["/echo"] = [503, 502]
    sleeps = Sleeps()
    resp = call_api(UrllibTransport(), "GET", fake_http.url + "/echo", headers=AUTH, sleep=sleeps)
    assert resp.status == 200
    assert len(fake_http.state.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_honours_retry_after(fake_http):
    fake_http.state.flaky["/echo"] = [429]
    sleeps = Sleeps()
    call_api(UrllibTransport(), "GET", fake_http.url + "/echo", headers=AUTH, sleep=sleeps)
    assert sleeps == [7.0]


def test_retry_is_bounded_and_then_raises(fake_http):
    fake_http.state.flaky["/echo"] = [503] * 10
    sleeps = Sleeps()
    with pytest.raises(errors.ApiError) as ei:
        call_api(
            UrllibTransport(),
            "GET",
            fake_http.url + "/echo",
            headers=AUTH,
            attempts=3,
            sleep=sleeps,
        )
    assert ei.value.status == 503 and ei.value.retryable
    assert len(fake_http.state.requests) == 3
    assert len(sleeps) == 2


def test_post_is_never_retried(fake_http):
    fake_http.state.flaky["/echo"] = [503, 503]
    sleeps = Sleeps()
    with pytest.raises(errors.ApiError):
        call_api(
            UrllibTransport(),
            "POST",
            fake_http.url + "/echo",
            headers=AUTH,
            json_body={},
            sleep=sleeps,
        )
    assert len(fake_http.state.requests) == 1
    assert sleeps == []


def test_post_retried_only_when_caller_asserts_idempotent(fake_http):
    fake_http.state.flaky["/echo"] = [503]
    resp = call_api(
        UrllibTransport(),
        "POST",
        fake_http.url + "/echo",
        headers=AUTH,
        json_body={},
        idempotent=True,
        sleep=Sleeps(),
    )
    assert resp.status == 200
    assert len(fake_http.state.requests) == 2, "the second attempt is the one that succeeded"


def test_retry_retries_transport_errors_but_not_tls():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise errors.TransportError("reset", retryable=True)
        return Response(200, {}, "{}")

    assert retry(flaky, sleep=Sleeps()).status == 200
    assert len(calls) == 3

    def tls():
        raise errors.TransportError("CERTIFICATE_VERIFY_FAILED", retryable=False)

    with pytest.raises(errors.TransportError):
        retry(tls, sleep=Sleeps())


# -- status -> typed error -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,cls",
    [
        (401, errors.AuthError),
        (403, errors.AuthError),
        (404, errors.NotFoundError),
        (405, errors.UnsupportedError),
        (409, errors.RejectedError),
        (422, errors.RejectedError),
        (429, errors.ApiError),
        (500, errors.ApiError),
    ],
)
def test_error_for_status(status, cls):
    err = errors.error_for_status(status, "body", hints={404: "integration not installed"})
    assert err.__class__ is cls
    if status == 404:
        assert "integration not installed" in str(err)


def test_success_statuses_are_not_errors():
    assert errors.error_for_status(204, "") is None
    assert errors.interpret_status(200) is None
    assert errors.interpret_status(302).startswith("HTTP 302: redirect not followed")
    assert errors.interpret_status(401).startswith("HTTP 401: authentication failed")
    assert errors.error_for_status(302, "").__class__ is errors.RedirectError


def test_wrong_token_is_auth_error(fake_http):
    with pytest.raises(errors.AuthError):
        call_api(UrllibTransport(), "GET", fake_http.url + "/echo", headers=bearer("wrong"))


def test_non_json_body_is_a_decode_error_not_empty(fake_http):
    resp = call_api(UrllibTransport(), "GET", fake_http.url + "/text", headers=AUTH)
    with pytest.raises(errors.DecodeError) as ei:
        resp.json()
    # The body did not parse, so it is never carried (fail closed) -- only that it existed.
    assert ei.value.status == 200 and ei.value.body == errors.BODY_OMITTED
    assert ei.value.body_length == len("plain text, not json")


# -- small helpers -------------------------------------------------------------------------------


def test_join_url():
    assert join_url("https://a/", "/b") == "https://a/b"
    assert join_url("https://a", "b/c") == "https://a/b/c"
    assert join_url("https://a/api", "") == "https://a/api"
    with pytest.raises(ValueError):
        join_url("https://a", "https://elsewhere/steal")
    with pytest.raises(ValueError):
        join_url("", "x")


def test_bearer_refuses_empty_token():
    assert bearer("t", {"X": "1"}) == {"Authorization": "Bearer t", "X": "1"}
    with pytest.raises(ValueError):
        bearer("")


def test_wait_for_returns_value_and_times_out():
    clock = [0.0]

    def tick(s):
        clock[0] += s

    seq = iter([None, None, {"ok": 1}])
    assert wait_for(
        lambda: next(seq),
        deadline_s=10,
        interval_s=1,
        clock=lambda: clock[0],
        sleep=tick,
    ) == {"ok": 1}
    assert clock[0] == 2.0

    clock[0] = 0.0
    with pytest.raises(errors.WaitTimeout) as ei:
        wait_for(lambda: 0, deadline_s=3, interval_s=1, clock=lambda: clock[0], sleep=tick)
    assert ei.value.last == 0
