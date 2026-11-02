# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Security regression tests for the common base. The design is FAIL-CLOSED:

* Redirects: only same-origin GET/HEAD redirects are followed. Any change of scheme, host or port
  is refused (RedirectError) -- nothing is forwarded anywhere, so nothing can leak.
* Error bodies never enter a message. A body is kept only as a structured field, and only when it
  is a JSON object/array within a hard size cap; anything else is replaced wholesale.
* Keys: secret if ANY segment/compound names a secret, except a small exact allowlist.
* Server input never raises an unexpected exception: a bad Location refuses the redirect, a bad
  body is omitted, a bad URL is a CommonError.
* URLs in errors: http(s) with a host or fully masked; userinfo dropped; whole query masked.
"""

from __future__ import annotations

import base64
import json
import urllib.parse

import pytest
from ansible_collections.richard_lynch.common.plugins.module_utils import errors
from ansible_collections.richard_lynch.common.plugins.module_utils.args import (
    http_argument_spec,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.redact import (
    REDACTED,
    is_secret_key,
    redact,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.transport import (
    FetchUrlTransport,
    Response,
    UrllibTransport,
    call_api,
    next_hop,
)

GOOD_TOKEN = "tok-fixture-credential-for-unit-tests"
AUTH = {"Authorization": "Bearer " + GOOD_TOKEN, "Cookie": "session=c00kie-value"}


@pytest.fixture(params=["urllib", "fetch_url"])
def transport(request, make_module, fake_http):
    if request.param == "urllib":
        return UrllibTransport()
    return FetchUrlTransport(
        make_module(http_argument_spec(), {"url": fake_http.url, "token": GOOD_TOKEN})
    )


# -- redirects -----------------------------------------------------------------------------------


def test_cross_origin_redirect_is_refused(transport, fake_http, fake_http2):
    fake_http.state.redirect_to = fake_http2.url + "/echo"
    with pytest.raises(errors.RedirectError):
        call_api(transport, "GET", fake_http.url + "/redirect", headers=AUTH)
    assert fake_http2.state.requests == [], "another origin must never be contacted"


def test_same_origin_redirect_keeps_credentials(transport, fake_http):
    fake_http.state.redirect_to = "/echo"
    resp = transport.request("GET", fake_http.url + "/redirect", headers=AUTH)
    assert resp.status == 200
    assert fake_http.state.headers_seen[-1]["authorization"] == "Bearer " + GOOD_TOKEN


def test_https_to_http_downgrade_is_refused(make_module, fake_https, fake_http):
    fake_https.state.redirect_to = fake_http.url + "/echo"
    for tr in (
        UrllibTransport(ca_path=fake_https.ca_path),
        FetchUrlTransport(
            make_module(
                http_argument_spec(),
                {
                    "url": fake_https.url,
                    "token": GOOD_TOKEN,
                    "ca_path": fake_https.ca_path,
                },
            )
        ),
    ):
        with pytest.raises(errors.RedirectError):
            call_api(tr, "GET", fake_https.url + "/redirect", headers=AUTH)
    assert fake_http.state.requests == [], "the plain-http origin must never be contacted"


def test_a_write_is_never_redirected(transport, fake_http):
    fake_http.state.redirect_to = "/items/elsewhere"
    fake_http.state.redirect_status = 307
    with pytest.raises(errors.RedirectError):
        call_api(transport, "PUT", fake_http.url + "/redirect", headers=AUTH, json_body={})
    assert [r for r in fake_http.state.requests if r[0] == "PUT"] == [("PUT", "/redirect")]


@pytest.mark.parametrize(
    "location",
    [
        "http://[::1",
        "http://h:99999/x",
        "ftp://elsewhere/x",
        "//",
        "http://",
        "\x00bad",
    ],
)
def test_malformed_location_refuses_without_raising(location):
    resp = Response(302, {"Location": location}, "")
    assert next_hop("GET", "http://127.0.0.1:8080/a", resp, {}) is None


def test_malformed_location_from_a_server_is_a_redirect_error(transport, fake_http, make_module):
    """UrllibTransport: RedirectError. FetchUrlTransport: ansible's own redirect handler parses the
    Location before it can be declined and fetch_url turns the ValueError into fail_json -- also
    fail-closed (nothing followed, no traceback), just not through this library's error type."""
    fake_http.state.redirect_to = "http://[::1"
    expected = (errors.RedirectError,)
    if isinstance(transport, FetchUrlTransport):
        expected = (errors.RedirectError, make_module.FailJson)
    with pytest.raises(expected):
        call_api(transport, "GET", fake_http.url + "/redirect", headers=AUTH)
    assert fake_http.state.requests == [("GET", "/redirect")]


# -- error bodies ----------------------------------------------------------------------------


def _all_text(err):
    return str(err) + json.dumps(err.to_dict())


def test_body_never_enters_the_message():
    err = errors.ApiError(400, '{"error": "bad request detail"}', method="GET", url="https://h/x")
    assert "bad request detail" not in str(err)
    assert err.to_dict()["body"] == {"error": "bad request detail"}


def test_unregistered_secret_in_json_error_body_is_redacted(fake_http):
    fake_http.state.error_body = json.dumps({"error": "bad", "api_key": "ak-unregistered-77"})
    with pytest.raises(errors.ApiError) as ei:
        call_api(UrllibTransport(), "GET", fake_http.url + "/error", headers=AUTH)
    out = json.dumps(redact({"msg": str(ei.value), "error": ei.value.to_dict()}))
    assert "ak-unregistered-77" not in out
    assert "bad" in out, "the non-secret part of a JSON body is evidence and must survive"


@pytest.mark.parametrize(
    "body",
    [
        "token=abc123secret&user=bob",
        "password: abc123secret",
        "{'clientSecret': 'abc123secret'}",  # not JSON
        'prefix "accessToken":"abc123secret", suffix',
        "Authorization: Bearer abc123secret",
        '"abc123secret"',  # a JSON scalar, not an object/array
    ],
)
def test_non_json_body_is_omitted_wholesale(body):
    err = errors.ApiError(401, body)
    assert "abc123secret" not in _all_text(err)
    assert err.to_dict()["body"] == errors.BODY_OMITTED


def test_oversized_body_is_omitted_before_parsing():
    big = json.dumps({"note": "x" * errors.BODY_MAX_BYTES, "api_key": "zzsecret"})
    err = errors.ApiError(500, big)
    assert err.to_dict()["body"] == errors.BODY_OMITTED
    assert "zzsecret" not in _all_text(err)


@pytest.mark.parametrize("depth", [100, 5000])
def test_deeply_nested_body_is_omitted_not_raised(depth):
    nested = "[" * depth + "]" * depth
    err = errors.ApiError(500, nested)
    assert err.to_dict()["body"] == errors.BODY_OMITTED
    out = redact({"e": err.to_dict()})
    assert out["e"]["body"] == errors.BODY_OMITTED


def test_decode_error_carries_no_body():
    err = errors.DecodeError("not json", status=200, body="secret-ish plain text")
    assert "secret-ish" not in _all_text(err)


def test_redact_survives_a_too_deep_structure():
    o: list = []
    cur = o
    for _i in range(5000):
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    assert redact(o) == REDACTED


# -- key forms -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "apiKey",
        "APIKey",
        "X-Auth-Token",
        "clientSecret",
        "PrivateKey",
        "private_key",
        "db_passphrase",
        "credentials",
        "sessionToken",
        b"token",
        "Proxy-Authorization",
        "auth",
        "oauth_token",
        "accesskey",
        "x-api-key",
        "user_name_password",
        "service_url_token",
        "token_url",
        "token_expires_at",
        "password_file",
    ],
)
def test_secret_key_forms(key):
    assert is_secret_key(key), key
    assert redact({key: "v4lue"})[key if isinstance(key, str) else key.decode()] == REDACTED


@pytest.mark.parametrize(
    "key",
    [
        "tokens_used",
        "token_count",
        "token_type",
        "password_policy",
        "max_tokens",
        "name",
        "author",
        "ca_path",
    ],
)
def test_non_secret_key_forms(key):
    assert not is_secret_key(key), key


# -- truncation and encodings -------------------------------------------------------------


def test_secret_straddling_the_limit_is_not_leaked_by_prefix():
    secret = "SECRETVALUE-0123456789abcdef"
    text = "x" * (errors.BODY_LIMIT - 10) + secret + "y" * 50
    out = redact({"m": text}, no_log_values=[secret], max_len=errors.BODY_LIMIT)
    assert secret[:10] not in json.dumps(out)
    assert len(out["m"]) <= errors.BODY_LIMIT + 32


@pytest.mark.parametrize(
    "encode",
    [
        lambda s: json.dumps(s)[1:-1],
        lambda s: urllib.parse.quote(s, safe=""),
        urllib.parse.quote_plus,
        lambda s: base64.b64encode(s.encode()).decode(),
        lambda s: base64.urlsafe_b64encode(s.encode()).decode(),
    ],
    ids=["json", "url", "url_plus", "b64", "b64url"],
)
def test_encoded_forms_of_a_value_are_scrubbed(encode):
    secret = 'p@ss w/"q"+=?&é'
    enc = encode(secret)
    assert enc != secret
    out = redact("server echoed <%s>" % enc, no_log_values=[secret])
    assert enc not in out and REDACTED in out


# The userinfo password in the URL fixtures below, joined in at run time so a secret scanner
# reading this file does not take a documentation-style URL for a leaked credential.
_PW = "hunter2pw"

# -- URLs ------------------------------------------------------------------------------------


def test_errors_sanitize_url_userinfo_and_query():
    url = "https://bob:" + _PW + "@api.example.com/v1/x?api_key=qsecret&page=2#frag"
    for err in (
        errors.ApiError(404, "", method="GET", url=url),
        errors.TransportError("refused", url=url),
    ):
        blob = _all_text(err)
        assert "hunter2pw" not in blob and "bob" not in blob and "qsecret" not in blob
        assert "api_key" not in blob, "the whole query is masked, names included"
        assert "api.example.com/v1/x" in blob
    assert errors.sanitize_url(url) == "https://api.example.com/v1/x?" + errors.MASK


@pytest.mark.parametrize(
    "url",
    [
        "ftp://bob:pw@h/x",
        "javascript:alert(1)",
        "http:///nohost?token=x",
        "//h/x",
        "h/x?token=x",
        "http://[::1",
        "http://h:99999/",
    ],
)
def test_sanitize_url_masks_anything_not_plain_http(url):
    assert errors.sanitize_url(url) == errors.MASK


@pytest.mark.parametrize(
    "url",
    [
        "http://bob:" + _PW + "@127.0.0.1:9/x",
        "ftp://127.0.0.1/x",
        "http:///x",
        "127.0.0.1/x",
    ],
)
def test_transports_refuse_bad_urls_with_a_common_error(make_module, url):
    with pytest.raises(errors.InvalidUrlError) as ei:
        UrllibTransport().request("GET", url)
    assert isinstance(ei.value, errors.CommonError)
    assert "hunter2pw" not in str(ei.value)


def test_fetch_transport_learns_a_url_password(make_module):
    url = "http://bob:" + _PW + "@127.0.0.1:9/x"
    module = make_module(http_argument_spec(), {"url": url, "token": GOOD_TOKEN})
    tr = FetchUrlTransport(module)
    assert "hunter2pw" in module.no_log_values
    with pytest.raises(errors.InvalidUrlError) as ei:
        tr.request("GET", url)
    assert "hunter2pw" not in str(ei.value)


# -- bounded reads ---------------------------------------------------------------------------


def test_response_size_is_bounded(transport, fake_http):
    transport.max_bytes = 10
    with pytest.raises(errors.TransportError) as ei:
        transport.request("GET", fake_http.url + "/echo", headers=AUTH)
    assert "exceeds" in str(ei.value) and ei.value.retryable is False
