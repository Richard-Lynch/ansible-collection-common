# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            description=["Installs requires `pip`"],
            version_added="1.0",
            author=AUTHOR,
            options={
                "packages_pip_skip_setup": OptionSpec(type="bool", required=False, default=True),
                "packages_pip_virtualenv": OptionSpec(type="str", required=False),
                "packages_pip_virtualenv_command": OptionSpec(type="str", required=False),
                "packages_pip_packages": OptionSpec(
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
                                "latest",
                                "present",
                                "forcereinstall",
                            ],
                        ),
                        "version": OptionSpec(type="str", required=False),
                        "install_recommends": OptionSpec(
                            type="bool", required=False, default=False
                        ),
                        # Interesting for PEP 668.
                        # https://docs.ansible.com/ansible/latest/collections/ansible/builtin/pip_module.html#parameter-break_system_packages
                    },
                ),
            },
        ),
        "setup": ArgumentSpec(
            description=[
                "Prepare a Debian-family host for pip: upgrade, clear the PEP 668 marker, install pip"
            ],
            version_added="1.0",
            author=AUTHOR,
            options={},
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
