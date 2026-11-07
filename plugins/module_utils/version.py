# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The common API contract and its check, as a module_utils FILE.

``COMMON_API_VERSION`` is the API contract, independent of the galaxy release version. MAJOR moves
when a public symbol is removed or renamed; MINOR when one is added. A dependent module calls
``require_common(">=1.0,<2")`` before anything else, so a mismatched install fails naming both
versions instead of failing later with an AttributeError deep inside a run.

This lives in version.py and not in the package's __init__.py because AnsiballZ ships a module
with the module_utils FILES its imports name and replaces every package __init__.py with an empty
stub: code defined in __init__.py is importable in-process (unit tests, the controller) and
absent on the target. Found 2026-11-07 on the first live run of the first dependent module
(richard_lynch.monitoring.alertmanager_silence 1.1.0): "module ... has no attribute
'require_common'". Import it as
``from ansible_collections.richard_lynch.common.plugins.module_utils.version import require_common``.
"""

from __future__ import annotations

import re

COMMON_API_VERSION = (1, 0)

_CONSTRAINT = re.compile(r"\A\s*(>=|<=|==|!=|>|<)\s*(\d+)(?:\.(\d+))?\s*\Z")


class CommonVersionError(Exception):
    """The installed common does not satisfy the caller's declared range."""


def _parse(spec):
    constraints = []
    for part in spec.split(","):
        m = _CONSTRAINT.match(part)
        if not m:
            raise ValueError("invalid common version constraint %r in %r" % (part, spec))
        constraints.append((m.group(1), (int(m.group(2)), int(m.group(3) or 0))))
    return constraints


def _satisfies(version, op, want):
    if op == ">=":
        return version >= want
    if op == "<=":
        return version <= want
    if op == ">":
        return version > want
    if op == "<":
        return version < want
    if op == "==":
        return version == want
    return version != want


def require_common(spec: str, version: tuple = COMMON_API_VERSION) -> None:
    """Raise CommonVersionError unless ``version`` satisfies every constraint in ``spec``.

    ``spec`` is a comma-separated list such as ``">=1.0,<2"``. A malformed spec is a programming
    error and raises ValueError, never a silent pass.
    """
    failed = [
        "%s%d.%d" % (op, want[0], want[1])
        for op, want in _parse(spec)
        if not _satisfies(tuple(version), op, want)
    ]
    if failed:
        raise CommonVersionError(
            "richard_lynch.common API %d.%d is installed; this module requires %s (unsatisfied: %s)"
            % (version[0], version[1], spec, ", ".join(failed))
        )
