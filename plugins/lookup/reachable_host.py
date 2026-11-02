# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import absolute_import, annotations, division, print_function

__metaclass__ = type

DOCUMENTATION = """
    name: reachable_host
    author: Richard Lynch (@Richard-Lynch)
    version_added: "1.0.0"
    short_description: Pick the first of a roaming host's names that answers, robust to a stale controller resolver
    description:
        - Given candidate DNS NAMES in preference order, returns the first whose address answers
          on C(port), and the LAST one as the unprobed fallback. Always returns a NAME, never an
          address.
        - The address that is probed comes from a DIRECT DNS query (C(dig)), not from the
          controller's system resolver, whose cache can hold "no such name" for minutes after a
          roaming device wakes or changes network.
        - The chosen name's system resolution is then compared with DNS. On a disagreement the
          macOS resolver cache is flushed (C(sudo -n), never prompting) and re-checked; if it still
          disagrees the lookup FAILS with a message naming the disagreement and the fix, rather
          than letting ssh report an opaque "Could not resolve hostname".
        - The verdict is cached PER ANSIBLE RUN (one probe per run, not one per evaluation);
          a "preferred name is down" verdict only for 15 seconds, so a run that wakes the device
          and then uses it is not stuck with the pre-wake answer.
        - The decision logic lives in C(plugins/plugin_utils/reachable_host.py) and is unit-tested
          there without a network.
    options:
        _terms:
            description: Candidate names, most preferred first. At least one.
            required: true
            type: list
            elements: str
        port:
            description: TCP port that must answer for a candidate to count as up.
            default: 22
            type: int
    notes:
        - Set C(REACHABLE_HOST_RESOLVER_FLUSH=0) to forbid the automatic flush (the lookup then
          fails with the fix instead).
        - Set C(REACHABLE_HOST_TRACE=<file>) to append one JSON line per evaluation (pid, cache
          hit, choice), to measure how often a run evaluates it.
        - The automatic resolver flush is implemented for a macOS controller only.
"""

EXAMPLES = """
# host_vars/<device>.yml — a device that is sometimes wired and sometimes on WiFi.
ansible_host: "{{ lookup('richard_lynch.common.reachable_host', 'device-wired.example.com', 'device-wifi.example.com') }}"
"""

RETURN = """
_raw:
    description: The chosen candidate name (a one-element list, as for any lookup).
    type: list
    elements: str
"""

import json
import os
import time

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display
from ansible_collections.richard_lynch.common.plugins.plugin_utils.reachable_host import (
    is_address,
    resolve,
)

display = Display()


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        self.set_options(var_options=variables, direct=kwargs)
        port = int(self.get_option("port"))
        candidates = [str(t).strip() for t in terms if str(t).strip()]
        if not candidates:
            raise AnsibleError("richard_lynch.common.reachable_host: no candidate names were given")
        for name in candidates:
            # A NAME, never an address: an ansible_host value outlives the run and gets copied
            # into whatever reads it.
            if is_address(name):
                raise AnsibleError(
                    f"richard_lynch.common.reachable_host: '{name}' is an address; candidates must be DNS names "
                    "(an ansible_host IP outlives the run and gets copied into whatever reads it)."
                )

        choice, from_cache = resolve(candidates, port)

        trace = os.environ.get("REACHABLE_HOST_TRACE")
        if trace:
            try:
                with open(trace, "a") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "pid": os.getpid(),
                                "cache_hit": from_cache,
                                "choice": choice.name,
                                "error": bool(choice.error),
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass

        if not from_cache:
            for note in choice.notes:
                display.warning(f"reachable_host: {note}")
        if choice.error:
            raise AnsibleError(f"richard_lynch.common.reachable_host: {choice.error}")
        return [choice.name]
