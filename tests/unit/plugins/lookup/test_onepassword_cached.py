# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for plugins/lookup/onepassword_cached.py — where the cache file lives.

The cache path is NOT fixed: a relative ``cache_file`` resolves against the directory of the active
ansible.cfg when there is one, and against the WORKING DIRECTORY when there is not. That second
case is the one that surprises: the same play started from two directories reads two different
caches, so a warm cache looks cold and every read becomes a real `op` call. These tests pin the
documented behaviour, including through ``run()`` itself, so the docs and the code cannot drift.
"""

from __future__ import annotations

import json
import os

import pytest

from ansible_collections.richard_lynch.common.plugins.lookup import onepassword_cached as opc

TERM, VAULT, FIELD = "service/example-app/db-password", "Example-Vault", "password"
KEY = f"{VAULT}:{TERM}:{FIELD}:"


def test_relative_path_resolves_against_the_ansible_cfg_directory(tmp_path):
    cfg = tmp_path / "project" / "ansible.cfg"
    got = opc.resolve_cache_path(".credentials_cache.json", {"ansible_config_file": str(cfg)})
    assert got == str(tmp_path / "project" / ".credentials_cache.json")


def test_without_a_config_file_it_resolves_against_the_working_directory(tmp_path):
    got = opc.resolve_cache_path(".credentials_cache.json", {}, cwd=str(tmp_path))
    assert got == str(tmp_path / ".credentials_cache.json")


def test_without_variables_at_all_it_uses_the_real_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert opc.resolve_cache_path(".credentials_cache.json") == os.path.join(
        os.getcwd(), ".credentials_cache.json"
    )


def test_an_empty_config_file_variable_falls_back_to_the_working_directory(tmp_path):
    # ansible_config_file is None/'' when Ansible runs with no config file; that must not resolve
    # to a path relative to '' (i.e. silently the cwd of whatever imported the plugin).
    for empty in (None, ""):
        got = opc.resolve_cache_path("c.json", {"ansible_config_file": empty}, cwd=str(tmp_path))
        assert got == str(tmp_path / "c.json")


def test_an_absolute_path_is_used_as_given(tmp_path):
    absolute = str(tmp_path / "elsewhere" / "cache.json")
    cfg = str(tmp_path / "project" / "ansible.cfg")
    assert opc.resolve_cache_path(absolute, {"ansible_config_file": cfg}) == absolute
    assert opc.resolve_cache_path(absolute, {}, cwd="/nonexistent") == absolute


def test_no_name_means_the_documented_default():
    got = opc.resolve_cache_path(None, {}, cwd="/x")
    assert got == os.path.join("/x", opc.DEFAULT_CACHE_FILE)


def test_documented_default_matches_the_code():
    import yaml

    doc = yaml.safe_load(opc.DOCUMENTATION)
    assert doc["options"]["cache_file"]["default"] == opc.DEFAULT_CACHE_FILE


@pytest.fixture
def lookup(monkeypatch):
    """A LookupModule whose module-level cache starts empty for each test."""
    monkeypatch.setattr(opc, "CACHE", {})
    module = opc.LookupModule()
    # Instantiated directly rather than through the plugin loader, so it has no option
    # definitions; stand in the documented defaults for the two options run() reads.
    defaults = {"missing": "fail", "refresh": False}
    monkeypatch.setattr(module, "set_options", lambda **_k: None)
    monkeypatch.setattr(module, "get_option", defaults.get)
    return module


def test_run_reads_a_cache_hit_from_the_cwd_when_no_config_file_is_set(
    tmp_path, monkeypatch, lookup
):
    # End to end through run(): a value cached in the cwd is returned WITHOUT any 1Password
    # call. The upstream loader is replaced with one that fails the test if it is ever reached.
    (tmp_path / ".credentials_cache.json").write_text(json.dumps({KEY: "from-cwd-cache"}))
    monkeypatch.chdir(tmp_path)

    def no_op_calls(*_a, **_k):
        raise AssertionError("cache hit must not reach community.general.onepassword")

    monkeypatch.setattr("ansible.plugins.loader.lookup_loader.get", no_op_calls)
    assert lookup.run([TERM], variables={}, vault=VAULT, field=FIELD) == ["from-cwd-cache"]


def test_run_prefers_the_config_directory_over_the_cwd(tmp_path, monkeypatch, lookup):
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".credentials_cache.json").write_text(json.dumps({KEY: "from-project"}))
    (elsewhere / ".credentials_cache.json").write_text(json.dumps({KEY: "from-cwd"}))
    monkeypatch.chdir(elsewhere)
    variables = {"ansible_config_file": str(project / "ansible.cfg")}
    assert lookup.run([TERM], variables=variables, vault=VAULT, field=FIELD) == ["from-project"]
