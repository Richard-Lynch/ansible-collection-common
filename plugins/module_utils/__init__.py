# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""richard_lynch.common module_utils: the generic base for HTTP-API modules.

Keep this file free of definitions. AnsiballZ ships module_utils FILES and replaces a package's
__init__.py with an empty stub on the target, so anything defined here exists for unit tests and
the controller and NOT for a running module. The API contract (``COMMON_API_VERSION``,
``require_common``) is in version.py; the re-export below is for in-process callers only, and
a module must import version.py by name (test_version_is_a_file_not_the_package).

"""

from __future__ import annotations

from ansible_collections.richard_lynch.common.plugins.module_utils.version import (  # noqa: F401
    COMMON_API_VERSION,
    CommonVersionError,
    require_common,
)
