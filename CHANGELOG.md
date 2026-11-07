<!--
Copyright: (c) 2026, Richard Lynch
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Changelog

All notable changes to this collection are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the collection
uses [Semantic Versioning](https://semver.org/).

## [1.0.3] — 2026-11-07

### Changed

- The CI, release and disclose callers use ansible-devkit `v0.4.0`; the disclose caller passes
  no secrets and the repository carries a `.disclose-allow`. Renovate config (the shared devkit
  preset, pinned at `v0.4.1`), `.python-version` and the dependency-check baseline
  `deps-baseline.json` are added; `devkit.toml` gains the `[dependencies]` section that names it.

### Fixed

- `secrets_manager` runs its read-only actions under `--check`. The item lookup and the vault
  listing are commands, which a check run skips, so the next task parses empty output and the
  role fails at "Record whether the item exists". Both reads are `check_mode: false` (they
  change nothing), so `get`, `list` and `ensure` of an existing item return real values in a
  check run. An action that would write (create an absent item, roll or remove an existing one)
  reports the write and ends the role, instead of returning a value that was never stored or
  failing on a backup that was never written. The molecule scenario runs the reads in check
  mode against a stub `op`, and `roll` under a real `--check`.

## [1.0.2] — 2026-11-03

### Added

- `meta/main.yml` with `galaxy_info` (author, description, license, minimum Ansible version, tags)
  for every role, so each role carries a description on Galaxy.

### Changed

- CI, disclosure and release callers use ansible-devkit v0.2.2.

## [1.0.1] — 2026-11-03

The first release published to Galaxy; 1.0.0 exists only as the git tag `v1.0.0`.

### Changed

- CI, disclosure and release callers use ansible-devkit v0.2.0; `.github/actionlint.yaml`
  added, so the repository passes `devkit conform`.

## [1.0.0] — 2026-11-02

### Added

- `module_utils` base for HTTP-API modules (`COMMON_API_VERSION = (1, 0)`):
  `transport`, `args`, `errors`, `redact` and `desired_state`, with the
  `http_connection` doc fragment.
- Lookup `onepassword_cached`: a caching wrapper around
  `community.general.onepassword`, with `missing` and `refresh` options and a
  documented cache location (beside the active `ansible.cfg`, else the working
  directory).
- Lookup `reachable_host` and its `plugin_utils.reachable_host` decision logic:
  choose the first answering DNS name of a roaming host, from a direct DNS
  query, with a run-scoped verdict cache kept in a per-user directory that is
  verified (owner, mode, no symlinks) before use.
- Filter `onepassword_refs`: list the `onepassword_cached` reads in a YAML text.
- Roles `deployment_config`, `secrets_manager`, `utils_file_download`,
  `utils_verify_connection`, `application_docker`, `config_networkd`,
  `packages_apt`, `packages_pip`, `setup_pip` and `host_baseline`, each with an
  argument spec, a README and a molecule scenario.

