<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# config_networkd

Owns a host's **systemd-networkd** configuration for one interface: renders
`/etc/systemd/network/10-<interface>.network` (DHCP by default, or static), and
disables ifupdown's `networking.service` so networkd is the only DHCP client on
the interface.

## Inputs

See `meta/argument_specs.py`; defaults (with examples) in `defaults/main.yml`.
All optional.

| Option                            | Meaning                               |
| --------------------------------- | ------------------------------------- |
| `networkd_config_interface`       | interface to match (default `eth0`)   |
| `networkd_config_address`         | static primary CIDR; empty means DHCP |
| `networkd_config_gateway`         | gateway for a static host             |
| `networkd_config_dns`             | resolvers for a static host           |
| `networkd_config_extra_addresses` | further CIDRs the host holds          |

## Usage

```yaml
# host_vars/example-dns-01.yml — a DHCP server cannot take its own address from DHCP
networkd_config_address: "192.0.2.3/24"
networkd_config_gateway: "192.0.2.1"
networkd_config_dns: ["127.0.0.1"]
networkd_config_extra_addresses: ["192.0.2.2/24"] # the service address
```

## The role owns the whole file

The `.network` file is re-rendered in full on every run, so an address added any
other way (a `lineinfile`, a persisted `ip addr add`) is removed by the next run
and lost at the next reboot. Declare every address in
`networkd_config_extra_addresses`.

## Testing

`molecule test` (render tier) renders the role's template through the role's own
address composition (`tasks/_addresses.yml`) for DHCP, static-with-extras, and
extras-only declarations. It does not touch the controller's network.
