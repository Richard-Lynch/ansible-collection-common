# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""richard_lynch.common module_utils: the generic base for HTTP-API modules.

``COMMON_API_VERSION`` is the API contract, independent of the galaxy release version. MAJOR moves
when a public symbol is removed or renamed; MINOR when one is added. A dependent module calls
``require_common(">=1.0,<2")`` before anything else, so a mismatched install fails naming both
versions instead of failing later with an AttributeError deep inside a run.
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
