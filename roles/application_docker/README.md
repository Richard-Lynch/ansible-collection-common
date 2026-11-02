<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# application_docker

Installs Docker and owns its daemon configuration:

- installs through `geerlingguy.docker`, **pinned per distro** (Debian and
  Ubuntu use different version strings, so each family gets its own pin; see
  `defaults/main.yml` for why);
- writes `/etc/docker/daemon.json` with **bounded container logs** (json-file,
  50m x 3) merged with any `application_docker_daemon_config`, validated as JSON
  before it is installed;
- writes a systemd **drop-in** (never a replacement unit) keeping `dockerd` on
  the unix socket.

## Inputs

See `meta/argument_specs.py`; defaults in `defaults/main.yml`. Commonly set:

| Option                                          | Meaning                                                         |
| ----------------------------------------------- | --------------------------------------------------------------- |
| `application_docker_pin`                        | `false` takes whatever the distro's Docker repo serves          |
| `application_docker_version_debian` / `_ubuntu` | the per-distro pins                                             |
| `application_docker_log_opts`                   | json-file rotation (`max-size`, `max-file`)                     |
| `application_docker_daemon_config`              | extra `daemon.json` keys                                        |
| `docker_expose_tcp`                             | **leave false** — `true` adds an unauthenticated TCP API socket |

## Requirements

`geerlingguy.docker` on the roles path.

## Safety notes

- A daemon.json Docker cannot parse stops `dockerd` from starting, so the file
  is validated first.
- Log options apply to containers **created after** the restart; recreate
  existing ones.
- Moving a pin downward can wipe Docker's image store. Change a pin
  deliberately.

## Testing

`molecule test` (render tier) renders both templates from the role and evaluates
the role's own package-pin expression for Debian, Ubuntu and pin-off. It does
not install Docker.
