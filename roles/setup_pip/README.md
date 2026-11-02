<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# setup_pip

Prepares a Debian-family host for installing into the **system** Python with
pip:

1. `apt upgrade` (failure tolerated);
2. removes the PEP 668 `EXTERNALLY-MANAGED` marker of **every** system Python
   found directly under `setup_pip_stdlib_root` (default `/usr/lib`, i.e.
   `/usr/lib/python3.X/EXTERNALLY-MANAGED`);
3. installs `pip` from apt.

Removing the marker is a deliberate choice to let system pip install. Prefer a
virtualenv (`packages_pip_virtualenv`) or the distro's `python3-*` package where
either will do.

## Inputs

`setup_pip_stdlib_root` — see `meta/argument_specs.py`.

`pep668` entrypoint (`tasks_from: pep668`): only step 2.

## Testing

`molecule test` runs the `pep668` entrypoint against a fake stdlib root: markers
for two Python versions go, an unrelated stdlib file and a same-named file one
level deeper stay. The apt steps need root on a Debian host and are not
exercised.
