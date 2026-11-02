# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures: a stdlib fake HTTP(S) server in a thread, and a module harness.

The fake ENFORCES a small generic API rather than replaying calls: it checks the bearer token,
rewrites what it stores the way real servers do (server-managed ``id``/``updated_at`` that change on
every write, list ordering it chooses), echoes the presented credential in its error bodies (so a
redaction failure is visible), and can be told to fail AFTER applying a write (a partial write).

``ansible-test units`` puts the collection root on sys.path itself; plain ``pytest`` gets it from
here, so the same files run both ways.
"""

from __future__ import annotations

import datetime
import http.server
import json
import os
import sys
import threading
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
# tests/unit -> common -> richard_lynch -> ansible_collections -> <root that holds ansible_collections>
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The fakes listen on loopback and must never be reached through a proxy. Declaring that in the
# environment also keeps urllib off macOS's SystemConfiguration proxy lookup, which aborts the
# process ("objc ... fork() was called") under ansible-test's forked pytest workers.
_no_proxy = [p for p in os.environ.get("no_proxy", "").split(",") if p]
for _host in ("127.0.0.1", "localhost"):
    if _host not in _no_proxy:
        _no_proxy.append(_host)
os.environ["no_proxy"] = os.environ["NO_PROXY"] = ",".join(_no_proxy)

GOOD_TOKEN = "tok-fixture-credential-for-unit-tests"


class FakeState:
    def __init__(self):
        self.items = {}
        self.requests = []  # (method, path)
        self.writes = 0
        self.seq = 0
        self.flaky = {}  # path -> [statuses to return before succeeding]
        self.fail_after_write = None  # status to return AFTER applying the next write
        self.ignore_writes = False  # accept writes with 200 and store nothing
        self.echo_secret = None  # a value the server will echo in every response body
        self.slow_s = 0.0
        self.headers_seen = []  # one dict of received headers per request
        self.redirect_to = None  # where /redirect points (absolute or relative)
        self.redirect_status = 302
        self.error_body = None  # raw body /error answers 400 with


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output clean
        pass

    @property
    def st(self) -> FakeState:
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, status, payload=None, headers=None, raw=None):
        body = (
            raw
            if raw is not None
            else (json.dumps(payload).encode() if payload is not None else b"")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null") if n else None

    def _handle(self, method):
        st = self.st
        st.requests.append((method, self.path))
        st.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
        if self.path.startswith("/redirect"):
            # Before the auth check, as a real redirector would be: it answers anyone.
            return self._send(st.redirect_status, {}, {"Location": st.redirect_to})
        if st.slow_s:
            time.sleep(st.slow_s)
        queued = st.flaky.get(self.path)
        if queued:
            status = queued.pop(0)
            hdrs = {"Retry-After": "7"} if status == 429 else {}
            return self._send(status, {"error": "try again"}, hdrs)
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer " + GOOD_TOKEN:
            # Real servers do this: quote back what they were sent.
            return self._send(401, {"error": "invalid credentials: %s" % auth})
        if self.path.startswith("/items/"):
            return self._items(method, self.path[len("/items/") :])
        if self.path == "/echo":
            return self._send(200, {"echo": st.echo_secret, "api_key": st.echo_secret})
        if self.path == "/error":
            return self._send(400, raw=st.error_body.encode())
        if self.path == "/text":
            return self._send(200, raw=b"plain text, not json")
        return self._send(404, {"error": "no route %s" % self.path})

    def _items(self, method, name):
        st = self.st
        if method == "GET":
            item = st.items.get(name)
            return (
                self._send(200, item) if item else self._send(404, {"error": "no item %s" % name})
            )
        if method in ("PUT", "DELETE"):
            st.writes += 1
            if not st.ignore_writes:
                if method == "PUT":
                    body = self._body() or {}
                    st.seq += 1
                    st.items[name] = {
                        "name": name,
                        "value": body.get("value"),
                        # the server chooses the order it stores a list in
                        "tags": sorted(body.get("tags") or [], reverse=True),
                        "id": st.seq,
                        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                else:
                    st.items.pop(name, None)
            if st.fail_after_write:
                status, st.fail_after_write = st.fail_after_write, None
                return self._send(
                    status,
                    {"error": "backend hiccup, auth was %s" % self.headers.get("Authorization")},
                )
            return self._send(200, st.items.get(name) or {})
        return self._send(405, {"error": "method"})

    def do_GET(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_POST(self):
        self._handle("POST")


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def _serve(ssl_context=None):
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.state = FakeState()  # type: ignore[attr-defined]
    if ssl_context is not None:
        srv.socket = ssl_context.wrap_socket(srv.socket, server_side=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    scheme = "https" if ssl_context is not None else "http"
    return srv, "%s://127.0.0.1:%d" % (scheme, srv.server_address[1])


@pytest.fixture
def fake_http():
    srv, url = _serve()
    srv.url = url  # type: ignore[attr-defined]
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def self_signed(tmp_path_factory):
    """A self-signed cert for 127.0.0.1: (cert_path, key_path)."""
    # A hard import, not importorskip: a skipped TLS test reads as a passed one.
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    d = tmp_path_factory.mktemp("tls")
    cert_path, key_path = d / "cert.pem", d / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


@pytest.fixture
def fake_http2():
    """A second origin (different port), to receive cross-origin redirects."""
    srv, url = _serve()
    srv.url = url  # type: ignore[attr-defined]
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def fake_https(self_signed):
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*self_signed)
    srv, url = _serve(ctx)
    srv.url = url  # type: ignore[attr-defined]
    srv.ca_path = self_signed[0]  # type: ignore[attr-defined]
    yield srv
    srv.shutdown()
    srv.server_close()


# -- module harness ------------------------------------------------------------------------------


class ExitJson(Exception):
    def __init__(self, result):
        super().__init__("exit_json")
        self.result = result


class FailJson(Exception):
    def __init__(self, result):
        super().__init__("fail_json")
        self.result = result


@pytest.fixture
def make_module(monkeypatch, tmp_path):
    """Build a REAL AnsibleModule from args; exit_json/fail_json raise with the payload exactly as
    the library handed it -- BEFORE AnsibleModule's own no_log filtering, so a redaction test
    proves this library's redaction rather than Ansible's."""
    from ansible.module_utils import basic
    from ansible.module_utils.testing import patch_module_args

    def build(argument_spec, args, check_mode=False):
        full = dict(args)
        full["_ansible_check_mode"] = check_mode
        full["_ansible_tmpdir"] = str(tmp_path)
        full["_ansible_remote_tmp"] = str(tmp_path)
        with patch_module_args(full):
            module = basic.AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

        def exit_json(**kw):
            raise ExitJson(kw)

        def fail_json(**kw):
            raise FailJson(kw)

        module.exit_json = exit_json
        module.fail_json = fail_json
        return module

    build.ExitJson = ExitJson  # type: ignore[attr-defined]
    build.FailJson = FailJson  # type: ignore[attr-defined]
    return build
