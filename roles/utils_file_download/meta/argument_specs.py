# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            description=["Download a file from a URL"],
            version_added="1.0",
            author=AUTHOR,
            options={
                "file_download_path": OptionSpec(
                    type="str",
                    required=True,
                ),
                "file_download_url": OptionSpec(
                    type="str",
                    required=True,
                ),
                "file_download_ttl_days": OptionSpec(type="int", required=False, default=1000),
                "file_download_timeout": OptionSpec(type="int", required=False, default=300),
                "file_download_checksum": OptionSpec(
                    type="str",
                    required=False,
                    default="",
                    description=[
                        "Optional integrity check, as '<algorithm>:<hex>' or",
                        "'<algorithm>:<url-of-checksum-file>' — e.g. 'sha256:fc7bbc...'.",
                        "get_url verifies after transfer and DELETES the file on mismatch.",
                        "Empty means no verification, which is the historical behaviour of this",
                        "role; supply one for anything where silent corruption matters (OS",
                        "images, firmware).",
                    ],
                ),
                "file_download_mode": OptionSpec(type="str", required=False, default="0644"),
            },
        ),
    },
    spdx_license="GPL-3.0-or-later",
)
