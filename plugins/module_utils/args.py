# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The connection argument spec every HTTP-API module shares.

It matches ``doc_fragments/http_connection.py`` option for option; the unit tests compare the two
so documentation and behaviour cannot drift apart silently.
"""

from __future__ import annotations

import copy

_BASE = {
    "url": {"type": "str"},
    "token": {"type": "str", "no_log": True},
    "validate_certs": {"type": "bool", "default": True},
    "ca_path": {"type": "path"},
    "timeout": {"type": "int", "default": 30},
}


def http_argument_spec(
    token_required: bool = True,
    url_default: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Return a fresh argument_spec dict: url, token (no_log), validate_certs, ca_path, timeout.

    * ``url`` is required unless ``url_default`` is given.
    * ``token`` is always ``no_log``; required unless ``token_required=False``.
    * ``extra`` adds module-specific options. Redefining a connection option is refused: a module
      that silently replaced ``token`` could drop its ``no_log``.
    """
    spec = copy.deepcopy(_BASE)
    if url_default is None:
        spec["url"]["required"] = True
    else:
        spec["url"]["default"] = url_default
    if token_required:
        spec["token"]["required"] = True
    for name, opt in (extra or {}).items():
        if name in spec:
            raise ValueError(
                "http_argument_spec: %r is a connection option and cannot be redefined" % name
            )
        spec[name] = copy.deepcopy(opt)
    return spec
