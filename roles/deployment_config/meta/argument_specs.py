# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"
argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Adds configuration to the `/opt/<service_name>`",
            version_added="1.0",
            author=AUTHOR,
            options={
                "deployment_config_source": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "The directory on the control node (localhost) to use as the source for the deployed config.",
                    ],
                ),
                "deployment_config_destination": OptionSpec(
                    type="str",
                    required=True,
                    description=["The destination directory for the files to be placed"],
                ),
                "deployment_config_file_owner": OptionSpec(
                    type="str",
                    required=False,
                    description=["Owner of every deployed file. Unset means root."],
                ),
                "deployment_config_file_group": OptionSpec(
                    type="str",
                    required=False,
                    description=["Group of every deployed file. Unset means root."],
                ),
                "deployment_config_file_mode": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Mode of every deployed file. Unset means 'preserve' (the source file's mode).",
                        "Set it explicitly (e.g. '0600') when the config carries credentials.",
                    ],
                ),
            },
        ),
        "cleanup": ArgumentSpec(
            short_description="Removes all configuration to from",
            version_added="1.0",
            author=AUTHOR,
            options={
                "deployment_config_location": OptionSpec(
                    type="str",
                    required=True,
                    description=["The location of the files to be cleaned up"],
                ),
            },
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
