# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"
argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Own a host's baseline OS state: sshd, updates, boot behaviour",
            version_added="1.0",
            author=AUTHOR,
            options={
                "baseline_ssh_password_auth": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "Whether sshd accepts password authentication.",
                        "FALSE on any host with a public IP: a password-accepting sshd is a",
                        "brute-force target, and it only takes one later `useradd` with a weak",
                        "password to turn a theoretical exposure into a real one.",
                    ],
                ),
                "baseline_ssh_permit_root_login": OptionSpec(
                    type="str",
                    required=False,
                    default="prohibit-password",
                    choices=["yes", "no", "prohibit-password", "forced-commands-only"],
                    description=[
                        "PermitRootLogin. 'prohibit-password' keeps key-based root access — which",
                        "automation that connects as root depends on — while refusing passwords.",
                        "'no' locks out anything that connects as root.",
                    ],
                ),
                "baseline_unattended_upgrades": OptionSpec(
                    type="bool",
                    required=False,
                    default=True,
                    description=["Whether unattended-upgrades is installed and enabled."],
                ),
                "baseline_unattended_auto_reboot": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "Whether unattended-upgrades may REBOOT on its own.",
                        "FALSE: an unplanned reboot on a host carrying a public service is an",
                        "outage nobody scheduled, and an untested boot path is discovered at the",
                        "worst moment. Reboots stay deliberate.",
                    ],
                ),
                "baseline_apply": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "Generate-don't-apply. False reports the diff and changes nothing."
                    ],
                ),
            },
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
