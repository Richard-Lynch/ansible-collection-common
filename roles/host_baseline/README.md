<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# host_baseline

Owns a host's **baseline OS state** — the settings no service role covers and everyone assumes
somebody else set: sshd's authentication posture, unattended-upgrades, and the automatic-reboot
policy.

## Why it exists

A host whose services are all rebuildable from roles can still have a base OS that nothing owns.
An sshd that accepts password authentication because no role had an opinion about it is one later
`useradd` with a weak password away from a real exposure, and nothing would notice. This role
makes that posture a declared, verified setting.

## Usage

```yaml
- ansible.builtin.include_role:
    name: richard_lynch.common.host_baseline
  vars:
    baseline_apply: true            # generate-don't-apply; default false
```

Inputs are declared in `meta/argument_specs.py` (the `.yml` and `defaults/main.yml` are generated
from it — never hand-edit those). Defaults as shipped:

| Variable | Default | Meaning |
|---|---|---|
| `baseline_ssh_password_auth` | `false` | `PasswordAuthentication` in the drop-in |
| `baseline_ssh_permit_root_login` | `prohibit-password` | `PermitRootLogin` in the drop-in |
| `baseline_unattended_upgrades` | `true` | install and keep `unattended-upgrades` |
| `baseline_unattended_auto_reboot` | `false` | reboots stay a deliberate act |
| `baseline_apply` | `false` | report the diff only, unless explicitly set |

## How sshd is changed, and why it is safe to run remotely

A bad sshd config on a remote host with one access path is a lockout recoverable only from the
provider console. Four things prevent that, in order:

1. **A drop-in, never an edit of `sshd_config`.** The distribution's file stays pristine and the
   change reverts with one `rm` plus a reload, not a merge.
2. **`sshd` validates the file before it is installed** (`validate: sshd -t -f %s`). A typo fails
   the task rather than the next login.
3. **Reload, not restart.** A reload re-reads the config without dropping existing connections, so
   the session running the play survives even if the new config were wrong.
4. **The effective config is read back with `sshd -T` after the reload, and a FRESH connection is
   proven** via `reset_connection` + `wait_for_connection`. The already-open session would keep
   working even if sshd had started refusing new ones — that read-back is what turns "the config
   looks right" into "we can still get in".

The reload is deliberately **not a handler**. Handlers run at end of play, which would put the
reload *after* the verification gate — the gate would then check the old config and pass while the
new one had never been loaded.

## The `sshd -T` synonym trap

`sshd -T` reports the **effective** config, and it normalises some keywords: a config saying
`PermitRootLogin prohibit-password` is reported back as `without-password`. They are the same
setting — the older spelling is simply what `-T` emits.

A literal string comparison therefore **fails against a config that applied perfectly**, and the
natural reaction to that false alarm is to weaken the gate. The role keeps an explicit synonym table
(`_baseline_root_login_synonyms` in `tasks/main.yml`) and compares against it, so the gate stays
strict in the direction that matters: a host whose effective setting genuinely differs from the
declaration still fails.

## Tests

`molecule/default` deliberately **never converges the role** — `baseline_apply` is never true, no
sshd is reloaded, nothing is written outside a temp dir. Converging it in CI would rewrite the
effective sshd config of whatever host the runner happens to be.

Instead it exercises the two pieces of logic that carry the risk, against the role's own artifacts:

- renders `templates/sshd-baseline.conf.j2` with the shipped defaults (must be locked down) **and**
  with explicit overrides (must honour them — a template that always emitted the safe value would
  otherwise pass every security assertion while ignoring its inputs);
- parses the synonym table **out of `tasks/main.yml`** and runs real `sshd -T` spellings through it,
  including negatives that must be rejected.

The table is read from the role rather than retyped, so it cannot assert a copy against itself.
Dropping `without-password` from the table, widening the table until a wrong config passes, or
making the template ignore its inputs each turns the scenario red.

## Applied to

Any host whose base OS no other role owns — above all an internet-facing one. Apply it in the
host's site play before its services, so the SSH posture is settled before anything else is.
