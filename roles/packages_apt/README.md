<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# packages_apt

Installs or removes a declared list of apt packages, after an
`apt update && upgrade` (cache valid for an hour) unless
`packages_apt_skip_update` is set.

## Inputs

See `meta/argument_specs.py`.

```yaml
packages_apt_skip_update: false
packages_apt_packages:
  - { name: curl, state: present }
  - { name: example-tool, state: latest, install_recommends: true }
  - { name: old-thing, state: absent }
```

`state` is one of `present`, `latest`, `absent`, `build-dep`, `fixed`;
`install_recommends` defaults to false. A malformed entry is rejected by the
argspec before apt runs.

`update` entrypoint: only the update/upgrade.

## Testing

`molecule test` (argspec tier): an empty list runs cleanly, and a bad `state` or
a missing `name` is rejected. Installing packages needs root on an apt host and
is not exercised.
