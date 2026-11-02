# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

_STDLIB_ROOT = OptionSpec(
    type="str",
    required=False,
    default="/usr/lib",
    description=[
        "Directory holding the system Pythons' stdlib dirs (python3.X/). Every",
        "<root>/python3.X/EXTERNALLY-MANAGED marker found one level below it is removed.",
    ],
)

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Prepare a Debian-family host for system pip",
            description=[
                "Upgrades apt packages, removes the PEP 668 EXTERNALLY-MANAGED marker of every",
                "system Python, and installs pip.",
            ],
            version_added="1.0",
            author=AUTHOR,
            options={"setup_pip_stdlib_root": _STDLIB_ROOT},
        ),
        "pep668": ArgumentSpec(
            short_description="Remove the PEP 668 marker of every system Python",
            version_added="1.0",
            author=AUTHOR,
            options={"setup_pip_stdlib_root": _STDLIB_ROOT},
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
