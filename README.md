<!--
Copyright: (c) 2026, Richard Lynch
SPDX-License-Identifier: GPL-3.0-or-later
-->

# richard_lynch.common

Generic building blocks for Ansible estates: a small library for writing
HTTP-API modules, two controller-side lookups, a filter, and ten roles. Nothing
in this collection names a host, an address, a domain or a vault: everything
estate-specific comes from the caller's inventory.

## Install

```sh
ansible-galaxy collection install richard_lynch.common
```

or pin it in `requirements.yml`:

```yaml
collections:
  - name: richard_lynch.common
    version: "1.0.0"
```

Requires ansible-core 2.19 or newer. `community.general` is declared as a
dependency and installed with it.

## Contents

### Plugins

| Plugin                        | Type         | What it does                                                                                                                                         |
| ----------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `onepassword_cached`          | lookup       | `community.general.onepassword` with a local JSON cache (mode 0600), so each item is fetched from 1Password once; `missing=empty` for optional items |
| `reachable_host`              | lookup       | picks the first of a roaming host's DNS names that answers, probing the address from a direct DNS query rather than a possibly stale system resolver |
| `onepassword_refs`            | filter       | lists the `onepassword_cached` reads in a YAML text, for a preflight that checks every item exists before a run changes anything                     |
| `http_connection`             | doc fragment | the shared documentation for the connection options below                                                                                            |
| `module_utils` (package)      | module_utils | `COMMON_API_VERSION` and `require_common()`, the version contract dependent collections check                                                        |
| `module_utils.transport`      | module_utils | `Transport`, `FetchUrlTransport(module)`, `UrllibTransport`, `retry`, `call_api`, `wait_for`, `join_url`, `bearer`                                   |
| `module_utils.args`           | module_utils | `http_argument_spec()`, the argument spec matching `http_connection`                                                                                 |
| `module_utils.errors`         | module_utils | `ApiError` and its subclasses, `TransportError`, `interpret_status`                                                                                  |
| `module_utils.redact`         | module_utils | `redact()` and `learn_secret()` for everything a module returns or fails with                                                                        |
| `module_utils.desired_state`  | module_utils | the `Resource` protocol and `DesiredState(...).run()`: read, normalize, plan, check/diff, write, read back                                           |
| `plugin_utils.reachable_host` | plugin_utils | the pure decision logic behind the `reachable_host` lookup                                                                                           |

A module in another collection builds on the base like this:

```python
from ansible_collections.richard_lynch.common.plugins.module_utils import require_common
from ansible_collections.richard_lynch.common.plugins.module_utils.args import http_argument_spec

require_common(">=1.0,<2")
```

and declares `richard_lynch.common: ">=1.0.0,<2.0.0"` in its own `galaxy.yml`.
`COMMON_API_VERSION` (currently `1.0`) is the module_utils API contract: its
MAJOR moves when a public symbol is removed or renamed, its MINOR when one is
added. A mismatched install fails naming both versions.

### Roles

Each role's interface is its `meta/argument_specs.yml`, and its README covers
behaviour and testing.

| Role                      | What it does                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `deployment_config`       | deploys a tree of config files (copy, or render `.j2`) and reports `deployment_config_changed` inline         |
| `secrets_manager`         | 1Password credential lifecycle (`get`/`ensure`/`generate`/`roll`/`remove`/`list`), backup-before-destroy      |
| `utils_file_download`     | downloads a file unless a fresh copy exists, with optional checksum verification                              |
| `utils_verify_connection` | waits until a newly created host has an address in the inventory and accepts a connection                     |
| `application_docker`      | installs Docker (version-pinned per distro) and owns `daemon.json` and a systemd drop-in                      |
| `config_networkd`         | owns a host's systemd-networkd config for one interface (DHCP or static, with extra addresses)                |
| `packages_apt`            | installs or removes a declared list of apt packages                                                           |
| `packages_pip`            | installs or removes a declared list of pip packages, optionally into a virtualenv                             |
| `setup_pip`               | prepares a Debian-family host for system pip (removes the PEP 668 marker of every system Python)              |
| `host_baseline`           | owns sshd's authentication posture and unattended-upgrades, generate-don't-apply, with a verified sshd reload |

```yaml
- name: Deploy an application's config
  ansible.builtin.include_role:
    name: richard_lynch.common.deployment_config
  vars:
    deployment_config_source: "{{ role_path }}/files"
    deployment_config_destination: /opt/example-app
```

### Requirements outside this collection

- `onepassword_cached` and `secrets_manager` need the 1Password CLI (`op`) on
  the controller. `secrets_manager` needs a service account with write access to
  the vaults in `secrets_manager_allowed_vaults`, which the caller declares in
  inventory together with `secrets_manager_vault` (neither has a default).
- `application_docker` installs Docker through the `geerlingguy.docker` role,
  which must be on the roles path
  (`ansible-galaxy role install geerlingguy.docker`).
- `reachable_host` needs `dig` on the controller; its automatic resolver-cache
  flush is implemented for a macOS controller only.

## Development

```sh
# from a checkout at ansible_collections/richard_lynch/common
ansible-test sanity --docker
ansible-test units --docker
ansible-galaxy collection build
```

Each role's `meta/argument_specs.py` is the source of its `argument_specs.yml`
and generated `defaults/`; edit the Python, then regenerate with
`argspec x generate <role>` from
[ansible-devkit](https://github.com/Richard-Lynch/ansible-devkit). Molecule
scenarios live under `roles/<role>/molecule/default` and run with
`molecule test` from the role directory.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
