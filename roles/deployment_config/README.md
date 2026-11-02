<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# deployment_config

Deploys a **tree of config files** from the controller to a host: every file
under `deployment_config_source` lands at the same relative path under
`deployment_config_destination`. Files ending `.j2` are rendered as templates
(suffix stripped); everything else is copied.

## Inputs

See `meta/argument_specs.py`. In short:

| Option                                    | Required | Meaning                                                                                      |
| ----------------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `deployment_config_source`                | yes      | directory on the controller to deploy                                                        |
| `deployment_config_destination`           | yes      | directory on the host                                                                        |
| `deployment_config_file_owner` / `_group` | no       | ownership of every file (default `root`)                                                     |
| `deployment_config_file_mode`             | no       | mode of every file (default `preserve`); set `0600`/`0640` when the tree carries credentials |

`cleanup` entrypoint: `deployment_config_location` — removes that directory
entirely.

## Output

`deployment_config_changed` is set **inline**, as soon as the files are written,
so a caller can `when: deployment_config_changed` a reload in the same run. (A
handler also sets it, but handlers run at the end of the play — too late for a
"reload if changed" task.)

## Usage

```yaml
- ansible.builtin.include_role:
    name: richard_lynch.common.deployment_config
  vars:
    deployment_config_source: "{{ role_path }}/files"
    deployment_config_destination: /opt/example-app
    deployment_config_file_mode: "0640"

- name: Reload only if the config changed
  ansible.builtin.command:
    docker compose -f /opt/example-app/compose.yml up -d --force-recreate
  when: deployment_config_changed
```

## Behaviour worth knowing

- A file named `*.managed.*` (e.g. `app.managed.conf`) is copied only if
  **absent** — a local edit on the host survives every later run.
- **Every** `.j2` under the source is rendered. A credential template you render
  separately (at a tighter mode) must live outside the source tree, or it is
  written twice — once here at the shared mode.
- Files on the host that are not in the source are reported (at `-v`) but
  **never deleted**.

## Testing

`molecule test` converges for real into a scratch directory on the controller:
copy vs render, mode/ownership, the managed-file rule, the inline changed flag
(true, then false on a no-op), and the `cleanup` entrypoint.
