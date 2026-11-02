# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""redact(): by key and by value, deep, non-mutating; learn_secret registers with the module."""

from __future__ import annotations

import json

from ansible_collections.richard_lynch.common.plugins.module_utils.errors import (
    ApiError,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.redact import (
    REDACTED,
    is_secret_key,
    learn_secret,
    redact,
)

SECRET = "s3cr3t-value-abcdef"


def test_redacts_by_key_whatever_the_value():
    out = redact(
        {
            "token": "anything",
            "Authorization": "Bearer x",
            "X-Api-Key": "k",
            "name": "n",
        }
    )
    assert out == {
        "token": REDACTED,
        "Authorization": REDACTED,
        "X-Api-Key": REDACTED,
        "name": "n",
    }


def test_secret_suffixes_and_client_keys():
    assert (
        is_secret_key("bot_token")
        and is_secret_key("db_password")
        and is_secret_key("client-secret")
    )
    assert not is_secret_key("tokens_used") and not is_secret_key("name")
    assert redact({"pin": "1234"}, secret_keys=("pin",)) == {"pin": REDACTED}


def test_redacts_by_value_inside_strings_deeply():
    obj = {
        "msg": "server said: bad token %s!" % SECRET,
        "nested": [{"note": SECRET}, ("x", SECRET)],
    }
    out = redact(obj, no_log_values=[SECRET])
    assert SECRET not in json.dumps(out)
    assert out["msg"] == "server said: bad token %s!" % REDACTED
    assert out["nested"][1] == ("x", REDACTED)


def test_longest_value_first_and_empty_ignored():
    out = redact("abc-abcdef", no_log_values=["abc", "abc-abcdef", ""])
    assert out == REDACTED


def test_does_not_mutate_input():
    obj = {"token": "t", "l": [SECRET]}
    redact(obj, no_log_values=[SECRET])
    assert obj == {"token": "t", "l": [SECRET]}


def test_error_dict_is_redacted():
    err = ApiError(
        401,
        '{"error": "invalid credentials: Bearer %s"}' % SECRET,
        method="GET",
        url="https://x/y",
    )
    out = redact({"msg": str(err), "error": err.to_dict()}, no_log_values=[SECRET])
    assert SECRET not in json.dumps(out)


def test_none_under_secret_key_stays_none():
    # "no token was set" is information an operator needs; it is not a secret.
    assert redact({"token": None}) == {"token": None}


class _M:
    def __init__(self):
        self.no_log_values = set()


def test_learn_secret_registers_value():
    m = _M()
    learn_secret(m, "minted")
    learn_secret(m, "")
    learn_secret(m, None)
    assert m.no_log_values == {"minted"}
