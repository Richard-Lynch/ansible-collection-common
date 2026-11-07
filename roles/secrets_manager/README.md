<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# secrets_manager

Manages credentials in 1Password from Ansible:
`get` · `ensure` · `generate` · `roll` · `remove` · `list`. Designed to run
under a write-capable 1Password **service account**
(`OP_SERVICE_ACCOUNT_TOKEN`), so it runs **headless** — no 1Password desktop
app.

Use it instead of ad-hoc `op` commands in plays: every action is validated,
vault-scoped and kept out of the log.

## Required inventory

Which vault an estate keeps its credentials in is estate configuration, so the
role has **no default** for it. Declare both in inventory (e.g.
`group_vars/all.yml`); the argspec marks them required and the role fails before
any `op` call if either is missing.

```yaml
secrets_manager_vault: "Example-Vault" # the vault this estate's credentials live in
secrets_manager_allowed_vaults: ["Example-Vault"] # what the service account may manage
```

## Safety model (never lose data)

- **Backup-then-act, always.** Before `roll` or `remove`, the role snapshots the
  current value to `secrets_manager_backup_dir` (default `.secrets-backup/`
  beside the active `ansible.cfg`) as `<title>.<action>.bak` (0600) **and reads
  it back + asserts it matches** before making any change. If the backup can't
  be verified, it aborts _before_ touching 1Password. Gitignore it.
- **`remove` is soft by default** — it archives the item (recoverable in 1P).
  Set `secrets_manager_permanent_delete: true` for a deliberate hard delete (the
  backup is still enforced).
- **Vault-scoped.** The role refuses any vault not in
  `secrets_manager_allowed_vaults`. A caller that overrides
  `secrets_manager_vault` to a personal vault is stopped before any `op` call.
- Secrets are passed to `op` via **environment**, never argv, and every
  secret-bearing task is `no_log`.
- **`--check` runs the reads.** The item lookup and the vault listing are
  `check_mode: false`, so `get`, `list` and `ensure` of an existing item return
  real values under `--check`. An action that would write (create an absent
  item, roll or remove an existing one) reports what it would do and stops,
  changing nothing.

## Actions

| Action     | Item exists?       | Does                                                                                                                                                          |
| ---------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get`      | required           | reads the value into `secrets_manager_result_var`                                                                                                             |
| `ensure`   | either             | **idempotent**: create if absent (generated/supplied), else return the existing value. Never overwrites. Use in provisioning plays ("make sure this exists"). |
| `generate` | must be **absent** | creates a new item with a generated (or supplied) value; refuses if present                                                                                   |
| `roll`     | required           | backup → rotate the value (generated or supplied)                                                                                                             |
| `remove`   | required           | backup → archive (soft) or permanent-delete                                                                                                                   |
| `list`     | n/a                | lists item titles in the vault, optionally filtered by `secrets_manager_list_tag`                                                                             |

The full interface is in `meta/argument_specs.py` (generated into
`meta/argument_specs.yml`).

## Usage

```yaml
# Generate a brand-new service password
- ansible.builtin.include_role:
    name: richard_lynch.common.secrets_manager
  vars:
    secrets_manager_action: generate
    secrets_manager_title: "service/foo/db-password"
    secrets_manager_notes:
      "Foo app DB. Roll: re-run with action=roll then restart foo."

# Rotate it later (old value backed up + verified first)
- ansible.builtin.include_role:
    name: richard_lynch.common.secrets_manager
  vars:
    secrets_manager_action: roll
    secrets_manager_title: "service/foo/db-password"

# Read it for immediate use
- ansible.builtin.include_role:
    name: richard_lynch.common.secrets_manager
  vars:
    secrets_manager_action: get
    secrets_manager_title: "service/foo/db-password"
- ansible.builtin.debug: { msg: "len={{ secrets_manager_value_out | length }}" }

# Store an API token (concealed 'credential' field)
- ansible.builtin.include_role:
    name: richard_lynch.common.secrets_manager
  vars:
    secrets_manager_action: generate
    secrets_manager_category: "API Credential"
    secrets_manager_title: "service/foo/api-token"
    secrets_manager_value: "{{ vendor_issued_token }}"
```

## Requirements

- `op` (the 1Password CLI) on the controller, and `OP_SERVICE_ACCOUNT_TOKEN` set
  to a service account with `read_items` + `write_items` on the allowed vaults.
  Without the token `op` falls back to desktop auth and every call raises an
  approval prompt.

## What it does NOT do

- Touch any vault outside `secrets_manager_allowed_vaults`.
- Revoke a credential at the system that issued it. Rotating the 1Password item
  changes what a consumer presents, not what the issuer accepts; revoke the old
  value at its issuer separately.

## Testing

`molecule test` runs offline — it asserts the vault-scope + action-validation
guards (no live 1P), runs `get`/`ensure`/`list` in check mode against a stub
`op`, and runs `roll` under a real `--check` to prove it stops before writing. Live get/generate/roll/remove are exercised against a real
vault during use.
