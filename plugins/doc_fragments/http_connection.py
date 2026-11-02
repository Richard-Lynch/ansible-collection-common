# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Documentation for the options ``module_utils.args.http_argument_spec()`` emits.

Documents the factory's DEFAULTS (url and token required). A module calling the factory with
``url_default=`` or ``token_required=False`` overrides that option in its own DOCUMENTATION.
"""

from __future__ import annotations


class ModuleDocFragment:
    DOCUMENTATION = r"""
options:
  url:
    description:
      - Base URL of the API, including the scheme, for example C(https://api.example.com).
      - Paths are joined to it with exactly one slash.
    type: str
    required: true
  token:
    description:
      - Credential sent with every request (by default as an C(Authorization: Bearer) header).
      - Never logged, never returned, and redacted from error messages and diffs.
    type: str
    required: true
  validate_certs:
    description:
      - Verify the server's TLS certificate.
      - Set to V(false) only for a host whose certificate cannot be verified, and prefer
        O(ca_path) over turning verification off.
    type: bool
    default: true
  ca_path:
    description:
      - PEM file of CA certificates to trust when verifying the server's certificate.
    type: path
  timeout:
    description:
      - Seconds to wait for each HTTP request before treating it as failed.
    type: int
    default: 30
notes:
  - Idempotent requests (GET, PUT, DELETE) are retried on HTTP 429, 502, 503 and 504 with a
    bounded backoff; a creating POST is sent exactly once.
"""
