# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

# defaults/SKIP: the defaults stay hand-written in defaults/main.yml (the per-distro pin and its
# reasoning live there), so no option below declares a `default` — a second copy here would be a
# second source of truth. Derived values (application_docker_packages, *_resolved) are internal
# and deliberately not declared.
argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Install Docker (version-pinned per distro) and own its daemon config",
            description=[
                "Installs Docker through geerlingguy.docker with a per-distro version pin, writes",
                "/etc/docker/daemon.json (log rotation by default), and a systemd drop-in that keeps",
                "dockerd on the unix socket unless a TCP socket is deliberately enabled.",
            ],
            version_added="1.0",
            author=AUTHOR,
            options={
                "application_docker_pin": OptionSpec(
                    type="bool",
                    required=False,
                    description=["Pin the Docker packages to the per-distro versions below."],
                ),
                "application_docker_version_debian": OptionSpec(
                    type="str",
                    required=False,
                    description=["docker-ce package version used on Debian."],
                ),
                "application_docker_version_ubuntu": OptionSpec(
                    type="str",
                    required=False,
                    description=["docker-ce package version used on Ubuntu."],
                ),
                "application_docker_containerd_version_debian": OptionSpec(
                    type="str",
                    required=False,
                    description=["containerd.io package version used on Debian."],
                ),
                "application_docker_containerd_version_ubuntu": OptionSpec(
                    type="str",
                    required=False,
                    description=["containerd.io package version used on Ubuntu."],
                ),
                "application_docker_log_driver": OptionSpec(
                    type="str",
                    required=False,
                    description=["daemon.json log-driver."],
                ),
                "application_docker_log_opts": OptionSpec(
                    type="dict",
                    required=False,
                    description=[
                        "daemon.json log-opts (max-size / max-file bound json-file logs)."
                    ],
                ),
                "application_docker_daemon_config": OptionSpec(
                    type="dict",
                    required=False,
                    description=["Extra daemon.json keys, merged over the log settings."],
                ),
                "docker_expose_tcp": OptionSpec(
                    type="bool",
                    required=False,
                    description=[
                        "Also listen on an UNAUTHENTICATED TCP socket. Leave false; see defaults/main.yml.",
                    ],
                ),
                "docker_tcp_bind": OptionSpec(
                    type="str",
                    required=False,
                    description=["host:port for the TCP socket when docker_expose_tcp is true."],
                ),
            },
        )
    },
    spdx_license="GPL-3.0-or-later",
)
