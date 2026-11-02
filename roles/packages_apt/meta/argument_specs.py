# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            description=["Installs requires `apt`"],
            version_added="1.0",
            author=AUTHOR,
            options={
                "packages_apt_skip_update": OptionSpec(type="bool", required=False, default=False),
                "packages_apt_packages": OptionSpec(
                    type="list",
                    elements="dict",
                    required=False,
                    default=[],
                    options={
                        "name": OptionSpec(type="str", required=True),
                        "state": OptionSpec(
                            type="str",
                            required=True,
                            choices=[
                                "absent",
                                "build-dep",
                                "latest",
                                "present",
                                "fixed",
                            ],
                        ),
                        "install_recommends": OptionSpec(
                            type="bool", required=False, default=False
                        ),
                    },
                ),
            },
        ),
        "update": ArgumentSpec(
            description=["Update apt"],
            version_added="1.0",
            author=AUTHOR,
            options={},
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
