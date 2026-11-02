# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""List the 1Password items a YAML file reads through the `onepassword_cached` lookup.

Built for a PREREQUISITE PREFLIGHT: before a converge that reads credentials starts changing
anything, probe every 1Password item it will read. Without it, a converge can get past its backup
steps and then die half-way on `Failed to fetch '<item>'` because an item was never created. This
filter is how the preflight knows which items those are, read from the same files the converge
reads, so the list cannot drift from them.

The lookup is matched by its short name under ANY collection
(`richard_lynch.common.onepassword_cached`,`<namespace>.<collection>.onepassword_cached`, or the bare name), so the filter keeps working
when the plugin moves collection.

For each lookup it returns:
  item       the literal item title, or None when the title is a variable
  item_var   that variable's name, when the title is not a literal
  field      the `field=` argument (the plugin's default, `password`, when absent)
  optional   True when the lookup passes `missing='empty'` (absence is a supported state)
  guard      the variable an `{{ lookup(...) if <guard> ... else '' }}` expression is
             conditional on, or None. The caller decides whether the guard is on.

Parentheses are matched rather than regex-skipped, because the vault argument itself can contain
them (`vault=onepassword_vault | default('Example-Vault')`).
"""

from __future__ import annotations

import re

# lookup( 'ns.coll.onepassword_cached'  — either quote, any (or no) collection prefix.
_CALL = re.compile(
    r"lookup\(\s*(?P<q>['\"])(?:[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"onepassword_cached(?P=q)"
)
_FIRST_ARG = re.compile(
    r"\s*,\s*(?:'(?P<item>[^']+)'|\"(?P<item2>[^\"]+)\"|(?P<var>[A-Za-z_][A-Za-z0-9_]*))"
)
_FIELD = re.compile(r"field\s*=\s*['\"]([^'\"]+)['\"]")
_MISSING_EMPTY = re.compile(r"missing\s*=\s*['\"]empty['\"]")
_GUARD = re.compile(r"\A\s*if\s+\(?\s*(?P<guard>[A-Za-z_][A-Za-z0-9_]*)")


def _close_paren(text, start):
    """Index of the ')' closing the '(' at text[start], skipping quoted strings."""
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def onepassword_refs(text):
    refs = []
    seen = set()
    pos = 0
    while True:
        call = _CALL.search(text, pos)
        if not call:
            break
        at = call.start()
        open_at = at + len("lookup")
        close_at = _close_paren(text, open_at)
        if close_at < 0:
            break
        args = text[call.end() : close_at]  # everything after the plugin name
        pos = close_at + 1
        line_start = text.rfind("\n", 0, at) + 1
        if text[line_start:at].lstrip().startswith("#"):
            continue  # a YAML comment that quotes a lookup is not a read
        first = _FIRST_ARG.match(args)
        if not first:
            continue
        item = first.group("item") or first.group("item2")
        item_var = None if item else first.group("var")
        field_m = _FIELD.search(args)
        guard_m = _GUARD.match(text[close_at + 1 :])
        ref = {
            "item": item,
            "item_var": item_var,
            "field": field_m.group(1) if field_m else "password",
            "optional": bool(_MISSING_EMPTY.search(args)),
            "guard": guard_m.group("guard") if guard_m else None,
        }
        key = (ref["item"], ref["item_var"], ref["field"], ref["guard"])
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


class FilterModule(object):
    def filters(self):
        return {"onepassword_refs": onepassword_refs}
