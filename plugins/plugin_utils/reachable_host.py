# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pick which of a roaming host's DNS names to connect to — from evidence a stale controller
resolver cache cannot fake — and say exactly why when the name it picks will not resolve.

Used by the ``reachable_host`` lookup (a roaming device's ``ansible_host``). Stdlib only
and free of Ansible imports, so the decision logic is unit-tested without a network:
``tests/unit/plugins/plugin_utils/test_reachable_host.py``.

THE PROBLEM
    A device that moves between a wired name and a wireless name is usually selected with
    something like ``nc -z <wired name> 22`` and a short-lived cached verdict. Two controller-side
    caches make a reachable device look unreachable that way:

    1. macOS's SYSTEM resolver (mDNSResponder — what ssh and nc use) can keep answering "no such
       name" for minutes after the device wakes or changes network, far past the zone's negative
       TTL, while ``dig`` straight to the DNS server returns the right address. nc cannot resolve
       the wired name, reports "down", and the wireless name is chosen for a device that is wired
       and answering.
    2. A time-based cache outlives the change it describes: a "down" verdict taken before a wake
       is reused by a play run seconds after a SUCCESSFUL wake.

THE DECISION, and why each part is shaped the way it is
    * The address to PROBE comes from a DIRECT DNS query (dig), not from the system resolver.
      The DNS server is the authority on where a name points; the system resolver is a cache in
      front of it, and the cache is the thing that is wrong. Ansible is still handed the NAME —
      never the address: an ``ansible_host`` value outlives the run and is copied into whatever
      reads it, so an address there goes stale where a name would not.
    * Having picked a name, its SYSTEM resolution is compared with the DNS server's. If they
      disagree, ssh would fail (or go to the old address), so the cache is flushed — macOS only,
      ``sudo -n`` only, once per evaluation window — and re-checked. If it still disagrees the
      lookup FAILS with a message naming the disagreement and the fix, instead of letting ssh
      report an opaque "Could not resolve hostname".
    * The verdict is cached PER RUN: one probe per ansible-playbook run rather than one per
      evaluation (a run evaluates ``ansible_host`` about twice per task, and each probe is a TCP
      connection that closes without authenticating, which OpenSSH 10's PerSourcePenalties
      punishes). A NEW run always
      probes afresh, so no verdict can outlive the run that measured it. Within a run a
      "preferred name is down" verdict is kept only ``DOWN_TTL`` seconds, so a play that wakes the
      device and then uses it is not stuck with the pre-wake answer.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

# How long a verdict stands. "up" stands for the whole run (capped, so a run key that somehow
# collides with an ancient file cannot resurrect it); "fell back" stands only briefly.
UP_TTL_RUN = 3600
UP_TTL_ADHOC = 60  # no ansible ancestor found (a test, a manual python call): short either way
DOWN_TTL = 15
PRUNE_AFTER = 86400

FLUSH_COMMANDS = (
    ["sudo", "-n", "dscacheutil", "-flushcache"],
    ["sudo", "-n", "killall", "-HUP", "mDNSResponder"],
)
FLUSH_HINT = "sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder"

_IPV4 = re.compile(r"\A\d{1,3}(\.\d{1,3}){3}\Z")
# An ansible EXECUTABLE token (`venv/bin/ansible-playbook`, `ansible-inventory`, `ansible`), not
# any mention of the word: a path through `ansible_collections/` or an `ANSIBLE_*` variable in a
# shell's command line must not make that shell part of the run.
_ANSIBLE_CMD = re.compile(r"(?:\A|[\s/])ansible(?:-[a-z]+)?(?=\s|\Z)")


# --------------------------------------------------------------------------------------------
# Evidence gathering (each injectable, so the tests drive the decision with no network)
# --------------------------------------------------------------------------------------------


def query_dns(name: str, run: Callable = subprocess.run) -> Optional[list[str]]:
    """Ask the DNS server directly, bypassing the system resolver's cache.

    Returns the IPv4 addresses, ``[]`` when the server answered with none (NXDOMAIN, or a name
    whose DDNS record lapsed while the device slept), or ``None`` when the server could not be
    asked at all (no dig, a timeout, off-LAN) — "cannot tell", which must never read as "no such
    name".
    """
    try:
        proc = run(
            ["dig", "+short", "+time=1", "+tries=1", name.rstrip(".") + ".", "A"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    # dig writes its diagnostics to STDOUT (";; connection timed out"), so a non-empty stdout is
    # not an answer.
    if any(ln.startswith(";;") for ln in lines):
        return None
    # +short prints a CNAME chain's targets before the addresses; keep only the addresses.
    return sorted({ln for ln in lines if _IPV4.match(ln)})


def query_system(name: str, resolve: Callable = socket.getaddrinfo) -> list[str]:
    """Resolve the way ssh will: through the system resolver (mDNSResponder on macOS)."""
    try:
        infos = resolve(name, None, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return sorted({info[4][0] for info in infos})


def probe_tcp(addr: str, port: int, timeout: float = 2.0) -> bool:
    """Does anything accept a TCP connection on addr:port. The PORT rather than ICMP: an
    address can answer ping from a stale neighbour entry while sshd is unreachable."""
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return True
    except OSError:
        return False


def flush_system_resolver(
    run: Callable = subprocess.run, platform: str = sys.platform
) -> tuple[bool, str]:
    """Discard the system resolver's cached answers. macOS only, never prompts (``sudo -n``).

    Returns (ok, detail). A controller without passwordless sudo gets ``ok=False`` and the reason,
    which ends up in the failure message — nothing is retried interactively.
    """
    if os.environ.get("REACHABLE_HOST_RESOLVER_FLUSH", "1") == "0":
        return False, "automatic flush disabled by REACHABLE_HOST_RESOLVER_FLUSH=0"
    if platform != "darwin":
        return (
            False,
            f"automatic flush is implemented for macOS only (this is {platform})",
        )
    for cmd in FLUSH_COMMANDS:
        try:
            proc = run(cmd, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"`{' '.join(cmd)}` could not run: {exc}"
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            return (
                False,
                f"`{' '.join(cmd)}` exited {proc.returncode}: {err[0] if err else 'no output'}",
            )
    return True, "flushed"


# --------------------------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------------------------


@dataclass
class Choice:
    name: str
    # Did the PREFERRED (first) candidate answer? Decides how long the verdict may be cached.
    preferred_up: bool
    # Set when the chosen name WILL NOT work from this controller and we know why. The lookup
    # raises it; it is never silently returned as a working name.
    error: Optional[str] = None
    # Things worth a warning (a flush happened; the fallback is not in DNS at all).
    notes: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


@dataclass
class Io:
    dns: Callable[[str], Optional[list[str]]] = query_dns
    system: Callable[[str], list[str]] = query_system
    probe: Callable[[str, int], bool] = probe_tcp
    flush: Callable[[], tuple[bool, str]] = flush_system_resolver
    sleep: Callable[[float], None] = time.sleep


def _describe(addrs: list[str]) -> str:
    return ", ".join(addrs) if addrs else "NOTHING"


def check_agreement(name: str, dns: Optional[list[str]], io: Io) -> tuple[Optional[str], list[str]]:
    """Will ssh, resolving ``name`` through the system resolver, reach what DNS says it is?

    Returns (error, notes). The flush happens only on a MEASURED disagreement — the DNS server
    holds an address the system resolver does not return — never speculatively.
    """
    notes: list[str] = []
    if dns is None:
        # Could not ask the DNS server: there is nothing to compare against, so no basis to flush.
        return None, notes
    if not dns:
        notes.append(
            f"{name} has no A record at the DNS server, so ssh cannot resolve it either. "
            "For a portable device this usually means it is ASLEEP (dynamic DNS records can lapse "
            "while it sleeps); wake it, then re-run."
        )
        return None, notes
    system = io.system(name)
    if set(system) & set(dns):
        return None, notes

    ok, detail = io.flush()
    if ok:
        for _attempt in range(4):
            system_after = io.system(name)
            if set(system_after) & set(dns):
                notes.append(
                    f"the system resolver returned {_describe(system)} for {name} while the DNS "
                    f"server says {_describe(dns)} — a stale macOS resolver cache; flushed it "
                    "automatically and it now agrees."
                )
                return None, notes
            io.sleep(0.5)
        detail = f"flushed, but it still returns {_describe(system_after)}"

    return (
        f"{name} resolves to {_describe(dns)} at the DNS server (queried directly with dig), "
        f"but this controller's SYSTEM resolver — the one ssh uses — returns {_describe(system)}. "
        "That is the macOS resolver cache holding an answer from before the device woke or "
        f"changed network, so ssh would fail with 'Could not resolve hostname'. Automatic "
        f"flush: {detail}. Fix: run `{FLUSH_HINT}` and "
        "re-run the play.",
        notes,
    )


def select(candidates: list[str], port: int, io: Io) -> Choice:
    """Choose the first candidate whose DNS-resolved address answers on ``port``; the LAST
    candidate is the unprobed fallback (the old behaviour, kept — probing it would only add an
    unauthenticated connection per run to tell us what ssh is about to tell us anyway)."""
    if not candidates:
        raise ValueError("reachable_host needs at least one candidate name")
    evidence: list[dict] = []
    for index, name in enumerate(candidates):
        last = index == len(candidates) - 1
        dns = io.dns(name)
        if not last:
            # DNS is the authority when it could be asked; the system resolver only when it could
            # not (off-LAN, no dig) — which is the old behaviour, no worse than before.
            addrs = dns if dns is not None else io.system(name)
            up = any(io.probe(addr, port) for addr in addrs[:4])
            evidence.append({"name": name, "dns": dns, "probed": addrs[:4], "up": up})
            if not up:
                continue
        else:
            evidence.append({"name": name, "dns": dns, "probed": [], "up": None})
        error, notes = check_agreement(name, dns, io)
        return Choice(
            name=name,
            preferred_up=(index == 0),
            error=error,
            notes=notes,
            evidence=evidence,
        )
    raise AssertionError("unreachable: the last candidate always returns")  # pragma: no cover


# --------------------------------------------------------------------------------------------
# Run-scoped cache
# --------------------------------------------------------------------------------------------


def _ps_row(pid: int, run: Callable = subprocess.run) -> Optional[tuple[int, str, str]]:
    try:
        proc = run(
            ["ps", "-o", "ppid=", "-o", "lstart=", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (proc.stdout or "").strip()
    if proc.returncode != 0 or not line:
        return None
    # "  PPID Day Mon DD HH:MM:SS YYYY command..." — lstart is always five fields.
    parts = line.split(None, 6)
    if len(parts) < 7 or not parts[0].isdigit():
        return None
    return int(parts[0]), " ".join(parts[1:6]), parts[6]


def run_key(pid: Optional[int] = None, row: Callable = _ps_row) -> Optional[str]:
    """Identify the ansible run this process belongs to: the TOPMOST unbroken ancestor whose
    command mentions ansible. Ansible forks its task workers (multiprocessing 'fork'), so every
    worker of one ansible-playbook shares that ancestor; its start time is part of the key so a
    reused PID is a different run. None when there is no ansible ancestor."""
    pid = os.getpid() if pid is None else pid
    found: Optional[str] = None
    for _depth in range(16):
        info = row(pid)
        if info is None:
            break
        ppid, started, command = info
        if not _ANSIBLE_CMD.search(command):
            break
        found = f"{pid}@{started}"
        if ppid <= 1:
            break
        pid = ppid
    return found


def cache_valid(entry: dict, now: float, scoped: bool) -> bool:
    """Is a cached Choice still usable. "Up" stands for the run; anything else — the preferred
    name was down, or the choice carries an error — only for DOWN_TTL."""
    age = now - float(entry.get("ts", 0))
    if age < 0:
        return False
    if entry.get("preferred_up") and not entry.get("error"):
        return age < (UP_TTL_RUN if scoped else UP_TTL_ADHOC)
    return age < DOWN_TTL


def cache_dir() -> str:
    """A directory only this user should be able to write.

    ``$XDG_RUNTIME_DIR`` (per-user, mode 0700 by specification), else ``~/.cache``, else a
    uid-suffixed directory under the system temp dir. A shared, predictable path such as
    ``/tmp/reachable-host`` would let another local user pre-create it or plant symlinks in it, so
    every path is still checked by ``_secure_dir`` before anything is read from or written to it.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and os.path.isabs(runtime):
        return os.path.join(runtime, "reachable-host")
    home = os.path.expanduser("~")
    if home and home != "~" and os.path.isabs(home):
        return os.path.join(home, ".cache", "reachable-host")
    return os.path.join(os.environ.get("TMPDIR") or "/tmp", "reachable-host-%d" % os.getuid())


def cache_path(
    key: Optional[str], candidates: list[str], port: int, directory: Optional[str] = None
) -> str:
    raw = json.dumps([key or "adhoc", candidates, port])
    name = hashlib.sha256(raw.encode()).hexdigest()[:24] + ".json"
    return os.path.join(directory or cache_dir(), name)


def _secure_dir(directory: str) -> bool:
    """Create ``directory`` (0700) and confirm it is a real directory, owned by this user, with no
    group or other permission bits. Anything else — a symlink, someone else's directory, a
    group-writable one — means the cache cannot be trusted, and the caller does not use it."""
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        st = os.lstat(directory)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0


def _own_regular_file(fd: int) -> bool:
    st = os.fstat(fd)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()


def _read_entry(path: str) -> Optional[dict]:
    """The cached record, or None. Never follows a symlink (O_NOFOLLOW)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd) as fh:
        if not _own_regular_file(fh.fileno()):
            return None
        try:
            entry = json.load(fh)
        except ValueError:
            return None
    return entry if isinstance(entry, dict) else None


def _write_entry(directory: str, path: str, record: dict) -> None:
    """Write via a private temp file in the same directory, then an atomic rename over ``path``.
    rename() replaces a name rather than following it, so a planted link is never written through.
    """
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".entry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(directory: str, now: float) -> None:
    try:
        for entry in os.scandir(directory):
            if (
                entry.is_file(follow_symlinks=False)
                and now - entry.stat(follow_symlinks=False).st_mtime > PRUNE_AFTER
            ):
                os.unlink(entry.path)
    except OSError:
        pass


def resolve(
    candidates: list[str],
    port: int = 22,
    io: Optional[Io] = None,
    key: Optional[str] = "auto",
    directory: Optional[str] = None,
) -> tuple[Choice, bool]:
    """The whole thing: run-scoped cache around ``select``. Returns (choice, from_cache).

    The cache is an optimisation, never a dependency: when its directory or a file in it is not
    exactly what this user created (see ``_secure_dir``), the verdict is computed afresh and not
    stored, so the answer is still correct.
    """
    import fcntl  # POSIX; the controller is macOS/Linux

    io = io or Io()
    key = run_key() if key == "auto" else key
    directory = directory or cache_dir()
    if not _secure_dir(directory):
        return select(candidates, port, io), False
    path = cache_path(key, candidates, port, directory)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            # Something other than a file this code wrote (a symlink, a fifo): leave it alone.
            return select(candidates, port, io), False
    except FileNotFoundError:
        pass
    except OSError:
        return select(candidates, port, io), False
    try:
        lock_fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return select(candidates, port, io), False
    with os.fdopen(lock_fd, "r+") as lock:
        if not _own_regular_file(lock.fileno()):
            return select(candidates, port, io), False
        # Serialise concurrent workers so two of them cannot both probe (and both flush).
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time()
        entry = _read_entry(path)
        if entry is not None and cache_valid(entry, now, scoped=key is not None):
            entry.pop("ts", None)
            try:
                return Choice(**entry), True
            except TypeError:
                pass
        choice = select(candidates, port, io)
        record = asdict(choice)
        record["ts"] = now
        try:
            _write_entry(directory, path, record)
        except OSError:
            return choice, False
        prune(directory, now)
        return choice, False


def is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False
