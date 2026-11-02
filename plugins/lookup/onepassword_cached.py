# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import absolute_import, annotations, division, print_function

__metaclass__ = type

DOCUMENTATION = """
    name: onepassword_cached
    author: Richard Lynch (@Richard-Lynch)
    version_added: "1.0.0"
    short_description: Caching wrapper around community.general.onepassword
    description:
        - Wraps the community.general.onepassword lookup with a local file cache.
        - On first call for an item+field+vault combination, fetches from 1Password
          and writes the result to a JSON cache file.
        - Subsequent calls return the cached value without hitting the 1Password CLI.
        - Delete the cache file (see C(cache_file)) to clear it.
    options:
        field:
            description: Field to return from the 1Password item.
            default: password
            type: str
        vault:
            description: Vault containing the item.
            type: str
        section:
            description: Item section containing the field.
            type: str
        cache_file:
            description:
                - Path to the JSON cache file.
                - A RELATIVE path is resolved against the PROJECT directory, which is the directory
                  holding the active C(ansible.cfg) (the C(ansible_config_file) variable). When no
                  config file is in use it is resolved against the CURRENT WORKING DIRECTORY of the
                  C(ansible-playbook) process instead, so the same play run from two directories
                  reads and writes two different caches.
                - An ABSOLUTE path is used as given.
                - The file is written mode 0600 and holds plaintext secrets; gitignore it.
            default: .credentials_cache.json
            type: str
        missing:
            description:
                - How to behave when an item cannot be fetched from 1Password.
                - C(fail) (default) raises, matching community.general.onepassword.
                - C(empty) returns an empty string instead, for genuinely-optional
                  credentials where absence is a supported state rather than a failure.
                - A miss is cached either way, so an absent item costs at most ONE
                  1Password call ever rather than one per run.
                - NOT named C(errors) - Ansible RESERVES that kwarg for lookup()/query()
                  themselves and strips it before the plugin ever sees it, so an option by
                  that name silently keeps its default forever.
            default: fail
            choices: [fail, empty]
            type: str
        refresh:
            description:
                - Ignore any cached value for this read, fetch from 1Password, and re-cache the result.
                - For a value that was READ before it existed. A miss under C(missing=empty) is
                  cached as an empty string, so an item created later in the same run (or any later
                  run) stays invisible, and a config that consumes it is written with no credential.
                - Costs one real C(op) call per use.
            default: false
            type: bool
    notes:
        - Drop-in replacement for C(community.general.onepassword) with identical parameters.
        - The cache file holds plaintext secrets and must never be committed.
        - Where the cache lives depends on how Ansible was started (see C(cache_file)). Run every
          play from the project, with its C(ansible.cfg) in effect, or pass an absolute
          C(cache_file); otherwise a cache warmed from one directory is cold from another, and each
          cold read is a real C(op) call.
"""

EXAMPLES = """
- name: Read a service password (cached after the first call)
  ansible.builtin.debug:
    msg: "{{ lookup('richard_lynch.common.onepassword_cached', 'service/example-app/db-password', field='password', vault=onepassword_vault) }}"
  no_log: true
"""

import json
import os

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

CACHE = {}

DEFAULT_CACHE_FILE = ".credentials_cache.json"


def resolve_cache_path(cache_file_name, variables=None, cwd=None):
    """Where the cache file lives, as documented on the ``cache_file`` option.

    A relative ``cache_file_name`` is joined to the directory of ``ansible_config_file`` when that
    variable is set (the project's ansible.cfg), and to the working directory otherwise. An
    absolute one wins outright (``os.path.join`` discards the base for an absolute second part).
    ``cwd`` is injectable so the fallback is testable without chdir.
    """
    config_file = (variables or {}).get("ansible_config_file")
    if config_file:
        project_root = os.path.dirname(config_file)
    else:
        project_root = cwd if cwd is not None else os.getcwd()
    return os.path.join(project_root, cache_file_name or DEFAULT_CACHE_FILE)


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        self.set_options(var_options=variables, direct=kwargs)

        field = kwargs.get("field", "password")
        vault = kwargs.get("vault", None)
        section = kwargs.get("section", None)
        cache_file_name = kwargs.get("cache_file", DEFAULT_CACHE_FILE)
        # Read via get_option, NOT kwargs.get: set_options() above has already parsed and
        # normalised the documented options.
        missing = self.get_option("missing") or "fail"
        refresh = bool(self.get_option("refresh"))

        cache_file = resolve_cache_path(cache_file_name, variables)

        # Load cache from disk on first access
        global CACHE
        if not CACHE and os.path.isfile(cache_file):
            try:
                with open(cache_file, "r") as f:
                    CACHE = json.load(f)
                display.vvv(f"onepassword_cached: loaded cache from {cache_file}")
            except (json.JSONDecodeError, IOError):
                CACHE = {}

        results = []
        cache_dirty = False

        for term in terms:
            cache_key = f"{vault or '_default'}:{term}:{field}:{section or ''}"

            if cache_key in CACHE and not refresh:
                display.vvv(f"onepassword_cached: cache hit for {term}")
                results.append(CACHE[cache_key])
            else:
                display.vvv(
                    f"onepassword_cached: cache miss for {term}, fetching from 1Password"
                )
                # Build kwargs for the upstream lookup
                upstream_kwargs = {"field": field}
                if vault:
                    upstream_kwargs["vault"] = vault
                if section:
                    upstream_kwargs["section"] = section

                try:
                    from ansible.plugins.loader import lookup_loader

                    upstream = lookup_loader.get(
                        "community.general.onepassword",
                        loader=self._loader,
                        templar=self._templar,
                    )
                    if upstream is None:
                        raise AnsibleError(
                            "community.general.onepassword lookup plugin not found. "
                            "Install: ansible-galaxy collection install community.general"
                        )
                    value = upstream.run([term], variables=variables, **upstream_kwargs)
                    # `if value:` alone is NOT enough — the upstream lookup returns [None] for an
                    # item it cannot resolve, which is a TRUTHY list holding a falsy element. Taking
                    # value[0] there yields None, which then blows up downstream on `| length`
                    # rather than here where the cause is legible. Check the ELEMENT, not the list.
                    if value and value[0] is not None:
                        result = value[0]
                    else:
                        raise AnsibleError(f"No value returned for {term}")
                except Exception as e:
                    if missing != "empty":
                        raise AnsibleError(
                            f"Failed to fetch '{term}' from 1Password: {e}"
                        )
                    # Cache the miss as well as the hit. Without this an absent optional item
                    # costs a 1Password call on EVERY run, which is the exact cost this plugin
                    # exists to avoid.
                    display.warning(
                        f"onepassword_cached: '{term}' unavailable, returning empty "
                        f"(missing=empty): {e}"
                    )
                    result = ""

                CACHE[cache_key] = result
                cache_dirty = True
                results.append(result)

        # Persist cache to disk if we fetched new values
        if cache_dirty:
            try:
                # Created 0600 rather than chmod-ed after the write, so the plaintext secrets
                # are never readable by other users, not even for the length of the write.
                fd = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(CACHE, f, indent=2)
                display.vvv(f"onepassword_cached: wrote cache to {cache_file}")
            except IOError as e:
                display.warning(f"onepassword_cached: failed to write cache: {e}")

        return results
