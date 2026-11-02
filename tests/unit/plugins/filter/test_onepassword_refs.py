# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for plugins/filter/onepassword_refs.py (a credential preflight's item list).

Each shape is one real playbooks use; the near-misses (a comment, a different plugin) must NOT
produce a reference, or a preflight would refuse a run over an item nothing reads.
"""

from __future__ import annotations

from ansible_collections.richard_lynch.common.plugins.filter.onepassword_refs import (
    onepassword_refs,
)

HOST_VARS = """
example_api_key: >-
  {{ lookup('richard_lynch.common.onepassword_cached', 'service/example-app/api-key',
            field='password', vault=onepassword_vault | default('Example-Vault')) }}
example_bot_token: >-
  {{ lookup('richard_lynch.common.onepassword_cached', 'service/example-app/bot-token',
            field='credential', vault=onepassword_vault | default('Example-Vault')) }}
"""

DEPLOY_STACK = """
    example_settings_token: >-
      {{ lookup('richard_lynch.common.onepassword_cached', 'service/example-app/settings-token',
                field='password', vault=onepassword_vault)
         if example_settings_enabled | default(false) | bool else '' }}
    example_repo_token: >-
      {{ lookup('richard_lynch.common.onepassword_cached', example_repo_token_item,
                field='password', vault=onepassword_vault)
         if (example_repo_token_item | default('') | length > 0) else '' }}
"""


def test_unconditional_literal_items_with_their_fields():
    refs = onepassword_refs(HOST_VARS)
    assert [(r["item"], r["field"], r["guard"]) for r in refs] == [
        ("service/example-app/api-key", "password", None),
        ("service/example-app/bot-token", "credential", None),
    ]


def test_parentheses_inside_the_vault_argument_do_not_end_the_call():
    # `default('Example-Vault')` closes a paren before the lookup does; a regex that
    # stopped there would lose the field (it comes first here, so put it last to prove it).
    text = (
        "{{ lookup('richard_lynch.common.onepassword_cached', 'a/b', "
        "vault=onepassword_vault | default('X'), field='credential') }}"
    )
    assert onepassword_refs(text)[0]["field"] == "credential"


def test_guarded_and_variable_items():
    refs = onepassword_refs(DEPLOY_STACK)
    assert refs[0]["item"] == "service/example-app/settings-token"
    assert refs[0]["guard"] == "example_settings_enabled"
    assert refs[1]["item"] is None
    assert refs[1]["item_var"] == "example_repo_token_item"
    assert refs[1]["guard"] == "example_repo_token_item"


def test_field_defaults_to_password_and_optional_is_seen():
    text = "{{ lookup('richard_lynch.common.onepassword_cached', 'x/y', missing='empty') }}"
    assert onepassword_refs(text) == [
        {"item": "x/y", "item_var": None, "field": "password", "optional": True, "guard": None}
    ]


def test_near_misses_are_not_references():
    text = """
# e.g. {{ lookup('richard_lynch.common.onepassword_cached', 'only/in/a/comment') }}
a: "{{ lookup('community.general.onepassword', 'other/plugin') }}"
b: "{{ lookup('example_ns.example_coll.other_lookup', 'not/1password') }}"
"""
    assert onepassword_refs(text) == []


def test_a_repeated_read_is_listed_once():
    one = "{{ lookup('richard_lynch.common.onepassword_cached', 'x/y', field='password') }}\n"
    assert len(onepassword_refs(one * 3)) == 1


def test_the_lookup_is_found_under_any_collection_name():
    # A filter pinned to one FQCN would silently return [] for every file that uses another
    # collection's copy, and a preflight would then probe nothing and pass.
    text = """
a: "{{ lookup('example_ns.example_coll.onepassword_cached', 'svc/a', field='password') }}"
b: "{{ lookup(\"richard_lynch.common.onepassword_cached\", 'svc/b') }}"
c: "{{ lookup('onepassword_cached', 'svc/c') }}"
"""
    assert [r["item"] for r in onepassword_refs(text)] == ["svc/a", "svc/b", "svc/c"]


def test_a_different_plugin_with_a_similar_name_is_not_a_reference():
    text = """
a: "{{ lookup('community.general.onepassword', 'svc/a') }}"
b: "{{ lookup('example_ns.example_coll.onepassword_cached_v2', 'svc/b') }}"
c: "{{ lookup('richard_lynch.common.onepassword_cached_raw', 'svc/c') }}"
"""
    assert onepassword_refs(text) == []
