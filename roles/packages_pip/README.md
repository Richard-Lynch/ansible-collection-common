<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# packages_pip

Installs or removes a declared list of pip packages — into a virtualenv when
`packages_pip_virtualenv` is set, otherwise into the system Python.

## Inputs

See `meta/argument_specs.py`.

```yaml
packages_pip_virtualenv: /opt/example-app/venv
packages_pip_virtualenv_command: "python3 -m venv"
packages_pip_packages:
  - { name: requests, state: present }
  - { name: example-lib, state: present, version: "1.2.3" }
```

`packages_pip_skip_setup` (default **true**): set it false to run the `setup`
entrypoint first, which delegates to the `setup_pip` role (apt upgrade, remove
the PEP 668 marker, install pip) — needed only for installs into the SYSTEM
Python of a Debian-family host.

## Testing

`molecule test` installs into a scratch virtualenv (offline: the package is pip
itself) and checks the argspec rejects a bad `state`.
