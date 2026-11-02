# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

# defaults/SKIP: the defaults stay hand-written in defaults/main.yml (with their worked examples),
# so no option below declares a `default` — a second copy here would be a second source of truth.
argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Own a host's systemd-networkd config for one interface",
            description=[
                "Renders /etc/systemd/network/10-<interface>.network (DHCP, or static with",
                "optional extra addresses) and disables ifupdown so networkd is the only client.",
            ],
            version_added="1.0",
            author=AUTHOR,
            options={
                "networkd_config_interface": OptionSpec(
                    type="str",
                    required=False,
                    description=["Interface the .network file matches. Default eth0."],
                ),
                "networkd_config_address": OptionSpec(
                    type="str",
                    required=False,
                    description=["Static primary address as a CIDR. Empty means DHCP."],
                ),
                "networkd_config_gateway": OptionSpec(
                    type="str",
                    required=False,
                    description=["Gateway for a static host. Empty means none written."],
                ),
                "networkd_config_dns": OptionSpec(
                    type="list",
                    elements="str",
                    required=False,
                    description=["Resolvers for a static host. Empty means none written."],
                ),
                "networkd_config_extra_addresses": OptionSpec(
                    type="list",
                    elements="str",
                    required=False,
                    description=[
                        "Additional CIDRs the host holds. The role owns the whole file, so an",
                        "address added out of band is removed on the next run; declare it here.",
                    ],
                ),
            },
        )
    },
    spdx_license="GPL-3.0-or-later",
)
