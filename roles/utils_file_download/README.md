<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# utils_file_download

Downloads a file from a URL to a path on the host, **unless a copy younger than
`file_download_ttl_days` is already there**. Intended for large, slow-changing
artefacts (OS images, firmware) where re-downloading on every run is waste.

## Inputs

See `meta/argument_specs.py`.

| Option                   | Required | Default | Meaning                                                                                  |
| ------------------------ | -------- | ------- | ---------------------------------------------------------------------------------------- |
| `file_download_url`      | yes      |         | source URL (`https://`, `file://`, ...)                                                  |
| `file_download_path`     | yes      |         | destination path                                                                         |
| `file_download_ttl_days` | no       | 1000    | re-download only when the existing file is at least this old                             |
| `file_download_timeout`  | no       | 300     | seconds                                                                                  |
| `file_download_checksum` | no       | `""`    | `<algo>:<hex>` or `<algo>:<url of checksum file>`; a mismatch FAILS and deletes the file |
| `file_download_mode`     | no       | `0644`  | mode of the downloaded file                                                              |

## Usage

```yaml
- ansible.builtin.include_role:
    name: richard_lynch.common.utils_file_download
  vars:
    file_download_url: "https://example.com/images/example-os.iso"
    file_download_path: /var/lib/images/example-os.iso
    file_download_checksum: "sha256:https://example.com/images/SHA256SUMS"
```

## Notes

- Supply a checksum for anything where silent corruption matters. Without one a
  truncated download is indistinguishable from a good one until something fails
  to boot.
- The age check uses the file's ctime, so `touch`-ing or re-chmodding a file
  resets its TTL.

## Testing

`molecule test` runs offline against a `file://` fixture: the checksum-verified
download and its mode, the TTL skip (a local copy inside the TTL survives a
second call), and a checksum mismatch that must fail and leave no file behind.
