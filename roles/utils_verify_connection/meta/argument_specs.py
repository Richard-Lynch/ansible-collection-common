# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

# defaults/SKIP: the defaults stay hand-written in defaults/main.yml under their bare names, so no
# option below declares a `default` — a second copy here would be a second source of truth.
argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Ensures connection to a host in dynamic inventory after creation/restart",
            description=[
                "Refreshes the inventory until the host has an ansible_host, asserts that it does,",
                "then waits until Ansible can connect to it.",
            ],
            version_added="1.0",
            author=AUTHOR,
            options={
                "utils_verify_connection_hostname": OptionSpec(
                    type="str",
                    required=True,
                    description=["Inventory hostname to wait for."],
                ),
                "utils_verify_connection_refresh_retries": OptionSpec(
                    type="int",
                    required=False,
                    description=[
                        "Inventory refreshes after the first, while ansible_host is empty."
                    ],
                ),
                "utils_verify_connection_refresh_delay": OptionSpec(
                    type="int",
                    required=False,
                    description=["Seconds between inventory refreshes."],
                ),
                "utils_verify_connection_wait_delay": OptionSpec(
                    type="int",
                    required=False,
                    description=["wait_for_connection: seconds before the first attempt."],
                ),
                "utils_verify_connection_wait_sleep": OptionSpec(
                    type="int",
                    required=False,
                    description=["wait_for_connection: seconds between attempts."],
                ),
                "utils_verify_connection_wait_connect_timeout": OptionSpec(
                    type="int",
                    required=False,
                    description=["wait_for_connection: per-attempt connect timeout."],
                ),
                "utils_verify_connection_wait_timeout": OptionSpec(
                    type="int",
                    required=False,
                    description=["wait_for_connection: overall timeout."],
                ),
            },
        )
    },
    spdx_license="GPL-3.0-or-later",
)
