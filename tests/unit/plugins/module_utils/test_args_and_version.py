# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""http_argument_spec <-> doc fragment agreement, COMMON_API_VERSION / require_common, and the
Python 3.9 floor of every module_utils file."""

from __future__ import annotations

import ast
import os

import pytest
import yaml
from ansible_collections.richard_lynch.common.plugins.doc_fragments.http_connection import (
    ModuleDocFragment,
)
from ansible_collections.richard_lynch.common.plugins.module_utils import (
    COMMON_API_VERSION,
    CommonVersionError,
    require_common,
)
from ansible_collections.richard_lynch.common.plugins.module_utils.args import (
    http_argument_spec,
)

PLUGINS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "plugins")
)


def test_spec_shape_and_defaults():
    spec = http_argument_spec()
    assert set(spec) == {"url", "token", "validate_certs", "ca_path", "timeout"}
    assert spec["token"]["no_log"] is True and spec["token"]["required"] is True
    assert spec["url"]["required"] is True
    assert spec["validate_certs"]["default"] is True
    assert spec["timeout"]["default"] == 30


def test_spec_options():
    spec = http_argument_spec(token_required=False, url_default="https://api.example.com")
    assert "required" not in spec["token"] and spec["token"]["no_log"] is True
    assert spec["url"] == {"type": "str", "default": "https://api.example.com"}


def test_spec_is_fresh_each_call():
    a = http_argument_spec()
    a["timeout"]["default"] = 1
    assert http_argument_spec()["timeout"]["default"] == 30


def test_extra_cannot_redefine_a_connection_option():
    spec = http_argument_spec(extra={"state": {"type": "str", "default": "present"}})
    assert spec["state"]["default"] == "present"
    with pytest.raises(ValueError):
        http_argument_spec(extra={"token": {"type": "str"}})


def test_doc_fragment_matches_the_factory_defaults():
    """The cheap form of validate-modules: documentation and spec cannot drift."""
    doc = yaml.safe_load(ModuleDocFragment.DOCUMENTATION)["options"]
    spec = http_argument_spec()
    assert set(doc) == set(spec)
    for name, opt in spec.items():
        assert doc[name]["type"] == opt["type"], name
        assert doc[name].get("default") == opt.get("default"), name
        assert bool(doc[name].get("required", False)) == bool(opt.get("required", False)), name


def test_api_version_and_require_common():
    assert COMMON_API_VERSION == (1, 0)
    require_common(">=1.0,<2")
    require_common("==1.0")
    with pytest.raises(CommonVersionError) as ei:
        require_common(">=1.1,<2")
    assert "1.0" in str(ei.value) and ">=1.1,<2" in str(ei.value)
    with pytest.raises(CommonVersionError):
        require_common(">=2", version=(1, 9))
    require_common(">=1.0,<2", version=(1, 9))
    with pytest.raises(CommonVersionError):
        require_common("<2", version=(2, 0))


@pytest.mark.parametrize("bad", ["", "1.0", ">=x", ">=1.0;<2", "~=1.0"])
def test_malformed_spec_is_an_error_not_a_pass(bad):
    with pytest.raises(ValueError):
        require_common(bad)


def _py_files():
    for d, _dirs, files in os.walk(PLUGINS):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(d, f)


def test_plugins_found():
    assert len(list(_py_files())) >= 7


@pytest.mark.parametrize("path", sorted(_py_files()), ids=os.path.basename)
def test_python_39_floor_and_licence_header(path):
    """3.9 syntax, and `from __future__ import annotations` so `X | None` in an annotation
    never evaluates on a 3.9 target, and an SPDX header on every file."""
    src = open(path, encoding="utf-8").read()
    assert "SPDX-License-Identifier: GPL-3.0-or-later" in src.split("\n\n", 1)[0] + src[:400]
    tree = ast.parse(src, feature_version=(3, 9))
    futures = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
    assert any(a.name == "annotations" for n in futures for a in n.names), path
    assert not any(
        type(n).__name__ == "Match" for n in ast.walk(tree)
    ), "match statement needs 3.10"


def test_version_is_a_file_not_the_package():
    """AnsiballZ replaces module_utils/__init__.py with an empty stub on the target, so anything a
    module needs must be a FILE under module_utils. __init__.py may re-export; it may not define."""
    import ast
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parents[4] / "plugins" / "module_utils"
    tree = ast.parse((pkg / "__init__.py").read_text(encoding="utf-8"))
    defined = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
    ] + [
        t.id
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    ]
    assert defined == [], f"module_utils/__init__.py defines {defined}; move them to a file"
    from ansible_collections.richard_lynch.common.plugins.module_utils import version

    assert version.COMMON_API_VERSION == (1, 0)
    version.require_common(">=1.0,<2")
