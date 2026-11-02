# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the roaming-host selector (plugins/plugin_utils/reachable_host.py).

Every case drives the PURE decision with injected evidence — no DNS, no sockets, no sudo. The
cases cover both directions: each stale state must be seen through, and each near-miss must NOT
trigger a flush or a failure.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from ansible_collections.richard_lynch.common.plugins.plugin_utils import reachable_host as rh

WIRED = "device-wired.example.com"
WIFI = "device-wifi.example.com"
CANDIDATES = [WIRED, WIFI]
WIRED_IP = "192.0.2.4"
WIFI_IP = "198.51.100.4"


class FakeWorld:
    """DNS server view, system-resolver view (which a flush can repair), and what answers."""

    def __init__(self, dns, system, up, flush_ok=True, flush_repairs=True):
        self.dns_view = dict(dns)
        self.system_view = dict(system)
        self.up = set(up)
        self.flush_ok = flush_ok
        self.flush_repairs = flush_repairs
        self.flushes = 0
        self.probes: list[str] = []

    def io(self) -> rh.Io:
        return rh.Io(
            dns=lambda name: self.dns_view.get(name, []),
            system=lambda name: list(self.system_view.get(name, [])),
            probe=self._probe,
            flush=self._flush,
            sleep=lambda s: None,
        )

    def _probe(self, addr, port):
        self.probes.append(addr)
        return addr in self.up

    def _flush(self):
        self.flushes += 1
        if not self.flush_ok:
            return (
                False,
                "`sudo -n dscacheutil -flushcache` exited 1: sudo: a password is required",
            )
        if self.flush_repairs:
            for name, addrs in self.dns_view.items():
                if addrs:
                    self.system_view[name] = list(addrs)
        return True, "flushed"


# --------------------------------------------------------------------------------------------
# select(): the decision
# --------------------------------------------------------------------------------------------


class TestSelectHealthy:
    def test_wired_up_picks_wired_without_flushing(self):
        w = FakeWorld(
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            up=[WIRED_IP],
        )
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.name, c.preferred_up, c.error, w.flushes) == (WIRED, True, None, 0)
        assert w.probes == [WIRED_IP]

    def test_wired_down_falls_back_to_wifi_without_probing_it(self):
        # Wired->WiFi: the wired record still resolves, to an address that does not answer.
        w = FakeWorld(
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            up=[WIFI_IP],
        )
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.name, c.preferred_up, c.error, w.flushes) == (WIFI, False, None, 0)
        assert w.probes == [
            WIRED_IP
        ], "the fallback is never probed: one fewer unauthenticated connection"

    def test_returns_a_name_never_an_address(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        assert not rh.is_address(rh.select(CANDIDATES, 22, w.io()).name)


class TestSelectStaleResolver:
    """dig -> the right address, system resolver -> nothing (a stale negative cache)."""

    def test_stale_negative_cache_on_wired_still_picks_wired_and_flushes(self):
        # `nc -z <name>` would resolve through the system cache, fail, and say "down"; the
        # decision must come from the DNS server's address instead.
        w = FakeWorld(
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            {WIRED: [], WIFI: [WIFI_IP]},
            up=[WIRED_IP],
        )
        c = rh.select(CANDIDATES, 22, w.io())
        assert c.name == WIRED and c.preferred_up and c.error is None
        assert w.flushes == 1
        assert any("flushed it automatically" in n for n in c.notes)

    def test_stale_positive_cache_old_address_flushes(self):
        # The system resolver holds the PREVIOUS address — ssh would go to the wrong place.
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: ["192.0.2.99"]}, up=[WIRED_IP])
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.name, c.error, w.flushes) == (WIRED, None, 1)

    def test_stale_and_flush_unavailable_fails_with_the_cause_and_fix(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: []}, up=[WIRED_IP], flush_ok=False)
        c = rh.select(CANDIDATES, 22, w.io())
        assert c.name == WIRED and c.error
        for must in (
            "SYSTEM resolver",
            WIRED_IP,
            "NOTHING",
            "Could not resolve hostname",
            rh.FLUSH_HINT,
            "password is required",
        ):
            assert must in c.error, must

    def test_stale_and_flush_does_not_repair_fails(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: []}, up=[WIRED_IP], flush_repairs=False)
        c = rh.select(CANDIDATES, 22, w.io())
        assert c.error and "still returns NOTHING" in c.error

    def test_stale_on_the_wifi_fallback_flushes_too(self):
        # Wired is down, and the wifi name is the stale one: ssh would say "Could not resolve
        # hostname <wifi name>".
        w = FakeWorld(
            {WIRED: [WIRED_IP], WIFI: [WIFI_IP]},
            {WIRED: [WIRED_IP], WIFI: []},
            up=[WIFI_IP],
        )
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.name, c.error, w.flushes) == (WIFI, None, 1)


class TestSelectNearMisses:
    def test_dig_unavailable_falls_back_to_system_and_never_flushes(self):
        # Off-LAN, or no dig: "cannot tell" must not read as "no such name", and is no basis to
        # flush anything.
        w = FakeWorld({}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        io = w.io()
        io.dns = lambda name: None
        c = rh.select(CANDIDATES, 22, io)
        assert (c.name, c.error, w.flushes) == (WIRED, None, 0)
        assert w.probes == [WIRED_IP]

    def test_asleep_both_records_lapsed_returns_fallback_without_failing(self):
        # A wake-on-LAN play reads the device's hostvars while it sleeps. Failing here would
        # break the wake itself, so: a note, not an error.
        w = FakeWorld({WIRED: [], WIFI: []}, {}, up=[])
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.name, c.preferred_up, c.error, w.flushes) == (WIFI, False, None, 0)
        assert w.probes == [], "nothing to probe when DNS has no address"
        assert any("ASLEEP" in n for n in c.notes)

    def test_agreement_on_any_address_is_enough(self):
        w = FakeWorld({WIRED: [WIRED_IP, "192.0.2.5"]}, {WIRED: ["192.0.2.5"]}, up=[WIRED_IP])
        c = rh.select(CANDIDATES, 22, w.io())
        assert (c.error, w.flushes) == (None, 0)

    def test_single_candidate_is_its_own_fallback(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[])
        c = rh.select([WIRED], 22, w.io())
        assert (c.name, w.probes) == (WIRED, [])

    def test_no_candidates_is_an_error(self):
        with pytest.raises(ValueError):
            rh.select([], 22, FakeWorld({}, {}, []).io())


# --------------------------------------------------------------------------------------------
# Cache semantics
# --------------------------------------------------------------------------------------------


class TestCacheValidity:
    def test_up_stands_for_the_run(self):
        assert rh.cache_valid({"ts": 0, "preferred_up": True}, now=rh.UP_TTL_RUN - 1, scoped=True)
        assert not rh.cache_valid(
            {"ts": 0, "preferred_up": True}, now=rh.UP_TTL_RUN + 1, scoped=True
        )

    def test_up_outside_a_run_is_short(self):
        assert not rh.cache_valid(
            {"ts": 0, "preferred_up": True}, now=rh.UP_TTL_ADHOC + 1, scoped=False
        )

    def test_down_is_short_even_within_a_run(self):
        assert rh.cache_valid({"ts": 0, "preferred_up": False}, now=rh.DOWN_TTL - 1, scoped=True)
        assert not rh.cache_valid(
            {"ts": 0, "preferred_up": False}, now=rh.DOWN_TTL + 1, scoped=True
        )

    def test_an_error_is_short_even_when_up(self):
        assert not rh.cache_valid(
            {"ts": 0, "preferred_up": True, "error": "x"},
            now=rh.DOWN_TTL + 1,
            scoped=True,
        )

    def test_a_future_timestamp_is_never_trusted(self):
        assert not rh.cache_valid({"ts": 100, "preferred_up": True}, now=50, scoped=True)


class TestResolveCache:
    @pytest.fixture(autouse=True)
    def _tmpdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    def test_one_probe_per_run(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        hits = [rh.resolve(CANDIDATES, 22, w.io(), key="run-A")[1] for _run in range(25)]
        assert hits == [False] + [True] * 24
        assert w.probes == [WIRED_IP]

    def test_stale_down_from_a_previous_run_is_not_reused(self):
        # A pre-wake evaluation cached "down"; the next play, seconds after a successful wake,
        # must probe afresh.
        asleep = FakeWorld({WIRED: []}, {}, up=[])
        assert rh.resolve(CANDIDATES, 22, asleep.io(), key="wake-run")[0].name == WIFI
        awake = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        choice, hit = rh.resolve(CANDIDATES, 22, awake.io(), key="next-run")
        assert (choice.name, hit) == (WIRED, False)

    def test_down_expires_within_the_same_run(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(rh.time, "time", lambda: clock[0])
        asleep = FakeWorld({WIRED: []}, {}, up=[])
        assert rh.resolve(CANDIDATES, 22, asleep.io(), key="R")[0].name == WIFI
        awake = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        clock[0] += rh.DOWN_TTL - 1
        assert rh.resolve(CANDIDATES, 22, awake.io(), key="R")[0].name == WIFI  # still cached
        clock[0] += 2
        assert rh.resolve(CANDIDATES, 22, awake.io(), key="R")[0].name == WIRED  # re-probed

    def test_cached_error_is_still_an_error(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: []}, up=[WIRED_IP], flush_ok=False)
        first, _hit = rh.resolve(CANDIDATES, 22, w.io(), key="R")
        again, hit = rh.resolve(CANDIDATES, 22, w.io(), key="R")
        assert hit and first.error and again.error == first.error
        assert w.flushes == 1, "a cached failure does not re-flush on every evaluation"

    def test_corrupt_cache_file_is_ignored(self):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        path = rh.cache_path("R", CANDIDATES, 22)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("{not json")
        assert rh.resolve(CANDIDATES, 22, w.io(), key="R") == (
            rh.select(CANDIDATES, 22, w.io()),
            False,
        )


class TestCacheDirectory:
    """The cache lives where only this user can write, and is never trusted otherwise."""

    def test_prefers_xdg_runtime_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert rh.cache_dir() == str(tmp_path / "reachable-host")

    def test_falls_back_to_the_home_cache(self, tmp_path, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert rh.cache_dir() == str(tmp_path / ".cache" / "reachable-host")

    def test_a_relative_runtime_dir_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "relative/dir")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert rh.cache_dir() == str(tmp_path / ".cache" / "reachable-host")


class TestUntrustedCacheIsNotUsed:
    """Each case must still return the right answer, and must neither read nor write the cache."""

    @staticmethod
    def _twice(directory):
        w = FakeWorld({WIRED: [WIRED_IP]}, {WIRED: [WIRED_IP]}, up=[WIRED_IP])
        first = rh.resolve(CANDIDATES, 22, w.io(), key="R", directory=str(directory))
        second = rh.resolve(CANDIDATES, 22, w.io(), key="R", directory=str(directory))
        return first, second, w

    def test_a_trusted_directory_does_cache(self, tmp_path):
        first, second, w = self._twice(tmp_path / "cache")
        assert (first[1], second[1]) == (False, True)
        assert w.probes == [WIRED_IP]

    def test_a_directory_owned_by_someone_else(self, tmp_path, monkeypatch):
        d = tmp_path / "cache"
        d.mkdir(mode=0o700)
        real_uid = os.getuid()
        monkeypatch.setattr(rh.os, "getuid", lambda: real_uid + 1)
        first, second, w = self._twice(d)
        assert (first[0].name, second[0].name) == (WIRED, WIRED)
        assert (first[1], second[1]) == (False, False)
        assert w.probes == [WIRED_IP, WIRED_IP], "each call probed: nothing was cached"
        assert list(d.iterdir()) == [], "nothing was written into a directory we do not own"

    def test_a_group_writable_directory(self, tmp_path):
        d = tmp_path / "cache"
        d.mkdir()
        d.chmod(0o770)
        first, second, w = self._twice(d)
        assert (first[0].name, first[1], second[1]) == (WIRED, False, False)
        assert list(d.iterdir()) == []

    def test_a_symlinked_directory(self, tmp_path):
        real = tmp_path / "elsewhere"
        real.mkdir(mode=0o700)
        link = tmp_path / "cache"
        link.symlink_to(real)
        first, second, _w = self._twice(link)
        assert (first[0].name, first[1], second[1]) == (WIRED, False, False)
        assert list(real.iterdir()) == []

    def test_a_symlinked_cache_file_is_neither_read_nor_written_through(self, tmp_path):
        d = tmp_path / "cache"
        d.mkdir(mode=0o700)
        target = tmp_path / "victim.txt"
        target.write_text("precious\n")
        path = rh.cache_path("R", CANDIDATES, 22, str(d))
        os.symlink(str(target), path)
        first, second, w = self._twice(d)
        assert (first[0].name, first[1], second[1]) == (WIRED, False, False)
        assert target.read_text() == "precious\n", "the link target was not clobbered"
        assert os.path.islink(path), "the planted link was left alone, not replaced"
        assert w.probes == [WIRED_IP, WIRED_IP]

    def test_a_symlinked_lock_file(self, tmp_path):
        d = tmp_path / "cache"
        d.mkdir(mode=0o700)
        target = tmp_path / "victim.txt"
        target.write_text("precious\n")
        path = rh.cache_path("R", CANDIDATES, 22, str(d))
        os.symlink(str(target), path + ".lock")
        first, second, _w = self._twice(d)
        assert (first[0].name, first[1], second[1]) == (WIRED, False, False)
        assert target.read_text() == "precious\n"

    def test_the_cache_file_is_private(self, tmp_path):
        d = tmp_path / "cache"
        self._twice(d)
        path = rh.cache_path("R", CANDIDATES, 22, str(d))
        assert os.stat(path).st_mode & 0o077 == 0
        assert os.stat(path + ".lock").st_mode & 0o077 == 0


# --------------------------------------------------------------------------------------------
# Run identity
# --------------------------------------------------------------------------------------------


def _table(rows):
    return rows.get


class TestRunKey:
    def test_workers_share_the_topmost_ansible_ancestor(self):
        rows = {
            300: (
                200,
                "T3",
                "/venv/bin/python venv/bin/ansible-playbook play.yml",
            ),  # forked worker
            200: (
                100,
                "T2",
                "/venv/bin/python venv/bin/ansible-playbook play.yml",
            ),  # main
            100: (
                50,
                "T1",
                "bash -o pipefail -c ... venv/bin/ansible-playbook play.yml",
            ),  # make recipe
            50: (1, "T0", "make deploy PLAYBOOK=site.yml"),
        }
        assert rh.run_key(300, _table(rows)) == rh.run_key(200, _table(rows)) == "100@T1"

    def test_a_reused_pid_is_a_different_run(self):
        a = {10: (1, "Sat Oct 31 09:00:00 2026", "ansible-playbook a.yml")}
        b = {10: (1, "Sat Oct 31 10:00:00 2026", "ansible-playbook a.yml")}
        assert rh.run_key(10, _table(a)) != rh.run_key(10, _table(b))

    def test_a_path_through_ansible_collections_is_not_ansible(self):
        rows = {
            7: (
                6,
                "T",
                "python -c import sys; sys.path.insert(0, 'collections/ansible_collections/example')",
            ),
            6: (1, "T", "zsh -c ANSIBLE_PRIVATE_KEY_FILE=x make deploy"),
        }
        assert rh.run_key(7, _table(rows)) is None

    def test_no_ancestor_information_is_none(self):
        assert rh.run_key(5, lambda pid: None) is None


# --------------------------------------------------------------------------------------------
# Evidence gathering
# --------------------------------------------------------------------------------------------


def _ran(rc=0, out="", err=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, rc, out, err)


class TestQueryDns:
    def test_keeps_addresses_and_drops_a_cname_chain(self):
        assert rh.query_dns(WIRED, _ran(out="alias.example.\n192.0.2.4\n")) == [WIRED_IP]

    def test_no_record_is_an_empty_answer(self):
        assert rh.query_dns(WIRED, _ran(out="")) == []

    def test_timeout_diagnostics_on_stdout_are_cannot_tell(self):
        # dig writes ";; connection timed out" to STDOUT.
        assert (
            rh.query_dns(
                WIRED,
                _ran(rc=0, out=";; connection timed out; no servers could be reached\n"),
            )
            is None
        )

    def test_nonzero_rc_is_cannot_tell(self):
        assert rh.query_dns(WIRED, _ran(rc=9, out="")) is None

    def test_missing_dig_is_cannot_tell(self):
        def boom(*a, **k):
            raise FileNotFoundError("dig")

        assert rh.query_dns(WIRED, boom) is None


class TestQuerySystem:
    def test_unresolvable_is_empty(self):
        def gai(*a):
            raise OSError("nodename nor servname provided")

        assert rh.query_system(WIRED, gai) == []

    def test_dedupes(self):
        info = (2, 1, 6, "", (WIRED_IP, 0))
        assert rh.query_system(WIRED, lambda *a: [info, info]) == [WIRED_IP]


class TestFlush:
    def test_only_on_macos(self):
        ok, why = rh.flush_system_resolver(_ran(), platform="linux")
        assert not ok and "macOS only" in why

    def test_opt_out(self, monkeypatch):
        monkeypatch.setenv("REACHABLE_HOST_RESOLVER_FLUSH", "0")
        ok, why = rh.flush_system_resolver(_ran(), platform="darwin")
        assert not ok and "REACHABLE_HOST_RESOLVER_FLUSH=0" in why

    def test_sudo_needing_a_password_is_reported_not_prompted(self, monkeypatch):
        monkeypatch.delenv("REACHABLE_HOST_RESOLVER_FLUSH", raising=False)
        seen = []

        def run(cmd, **k):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "sudo: a password is required\n")

        ok, why = rh.flush_system_resolver(run, platform="darwin")
        assert not ok and "a password is required" in why
        assert seen[0][:2] == ["sudo", "-n"], "never an interactive sudo"

    def test_success_runs_both_commands_non_interactively(self, monkeypatch):
        monkeypatch.delenv("REACHABLE_HOST_RESOLVER_FLUSH", raising=False)
        seen = []

        def run(cmd, **k):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        assert rh.flush_system_resolver(run, platform="darwin") == (True, "flushed")
        assert [c[:2] for c in seen] == [["sudo", "-n"], ["sudo", "-n"]]
        assert seen[1][-1] == "mDNSResponder"


def test_is_address():
    assert rh.is_address(WIRED_IP) and not rh.is_address(WIRED)
