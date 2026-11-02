# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec

AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="1Password credential lifecycle: get/generate/roll/remove/list",
            version_added="1.0",
            author=AUTHOR,
            options={
                "secrets_manager_action": OptionSpec(
                    type="str",
                    required=False,
                    default="get",
                    choices=["get", "ensure", "generate", "roll", "remove", "list"],
                    description=[
                        "get: read the value. ensure: idempotent create-if-absent-else-read (for provisioning).",
                        "generate: create a new item (REFUSES if present).",
                        "roll: rotate an existing item (backup-then-replace). remove: archive (backup-then-archive).",
                        "list: enumerate vault items (optionally by tag); no title needed.",
                    ],
                ),
                "secrets_manager_title": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "1Password item title (path-style, e.g. service/example-app/admin-password)."
                    ],
                ),
                "secrets_manager_vault": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "The 1Password vault to act on. REQUIRED, with no default: which vault an",
                        "estate keeps its credentials in is estate configuration, so it is declared",
                        "in inventory (group_vars), never assumed by the role.",
                        "Must be a member of secrets_manager_allowed_vaults, or the role refuses.",
                    ],
                ),
                "secrets_manager_allowed_vaults": OptionSpec(
                    type="list",
                    elements="str",
                    required=True,
                    description=[
                        "The vaults this role may manage — the scope of the service account it runs",
                        "under. REQUIRED, declared in inventory beside secrets_manager_vault.",
                        "A caller that overrides secrets_manager_vault to any other vault (a personal",
                        "vault, say) is refused before any op call is made. Keep it to the vaults the",
                        "write-capable service account can reach; an empty list refuses everything.",
                    ],
                ),
                "secrets_manager_category": OptionSpec(
                    type="str",
                    required=False,
                    default="password",
                    choices=["password", "API Credential"],
                    description=["Item category for generate."],
                ),
                "secrets_manager_field": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Secret field name; derived from category ('password' or 'credential')."
                    ],
                ),
                "secrets_manager_value": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Explicit value for generate/roll. Empty => generate a strong random value."
                    ],
                ),
                "secrets_manager_generate_length": OptionSpec(
                    type="int",
                    required=False,
                    default=32,
                    description=["Length of a generated value."],
                ),
                "secrets_manager_notes": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Note stored on the item (usage / rotation). Encouraged on generate."
                    ],
                ),
                "secrets_manager_purpose": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "What the secret is for (STRING field; mirrored into notes if notes empty)."
                    ],
                ),
                "secrets_manager_rotation_days": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Expected rotation cadence in days (metadata for a health view). '' = don't stamp."
                    ],
                ),
                "secrets_manager_tags": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Tags to set on the item (comma-separated). Used by list filtering."
                    ],
                ),
                "secrets_manager_list_tag": OptionSpec(
                    type="str",
                    required=False,
                    description=["list: only show items carrying this tag ('' = all)."],
                ),
                "secrets_manager_permanent_delete": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "remove: permanently delete instead of soft-archive. Backup is enforced regardless."
                    ],
                ),
                "secrets_manager_backup_dir": OptionSpec(
                    type="str",
                    required=False,
                    description=[
                        "Where value snapshots are written before destructive ops (gitignored)."
                    ],
                ),
                "secrets_manager_result_var": OptionSpec(
                    type="str",
                    required=False,
                    default="secrets_manager_value_out",
                    description=["Fact name to return the value into (no_log'd)."],
                ),
            },
        )
    },
    spdx_license="GPL-3.0-or-later",
)
