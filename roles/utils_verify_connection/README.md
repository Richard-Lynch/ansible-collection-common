<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# utils_verify_connection

After a guest is created or restarted, waits until Ansible can actually use it:

1. while the host's `ansible_host` is empty, refreshes the (dynamic) inventory
   up to `utils_verify_connection_refresh_retries` more times,
   `utils_verify_connection_refresh_delay` seconds apart;
2. **asserts** the host now has an address — a host that never got one FAILS
   here rather than carrying on against an empty `ansible_host`;
3. `wait_for_connection` to the host.

## Inputs

See `meta/argument_specs.py`; defaults in `defaults/main.yml`. Only
`utils_verify_connection_hostname` is required.

## Usage

```yaml
- ansible.builtin.include_role:
    name: richard_lynch.common.utils_verify_connection
  vars:
    utils_verify_connection_hostname: example-guest-01
```

## Why the loop is shaped the way it is

`meta: refresh_inventory` ignores `until:`/`retries:`/`delay:` (all `meta`
actions do), so a retry written on the meta task refreshes exactly once. The
loop therefore wraps an `include_tasks` around the refresh, and the assert after
it is what makes a loop that never succeeds fail instead of fall through green.

## Testing

`molecule test` uses two inventory hosts on the local connection: one with an
address (found and connected to), one without (the hard gate must fail).
