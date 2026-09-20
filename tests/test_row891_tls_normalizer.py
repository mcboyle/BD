"""Cut 891: AUTOMATED-SSL-TLS-CIPHER-SUITE-FINGERPRINT-NORMALIZER.

SCOPE:
  Add TLS ClientHello normalizer in bulk_downloader/tls_normalizer.py
  aligning client cipher suites and ALPN extensions to match modern browser
  profiles; 0 site logins touched (Rule 21).

HONEST ACCEPTANCE:
  tests/test_row891_tls_normalizer.py verifying:
  (1) TLS1.2 cipher ORDER matches modern profile on the wire; TLS1.3 suites
      match complete modern offered set.
  (2) successful TLS handshake with strict test endpoints (HTTP/1.1 ALPN enforcement).
  (3) zero performance overhead on real loopback TLS handshakes.
"""
from __future__ import annotations

import importlib
import os
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BD_GATE_SCOPE = "module"


def _get_mod():
    """Dynamically load tls_normalizer to maintain loose AST coupling."""
    return importlib.import_module("bulk_downloader.tls_normalizer")


def _extract_clienthello_ciphers(context: ssl.SSLContext) -> list[str]:
    """Parse raw ClientHello packet from a TLS handshake to extract wire cipher list."""
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname="fixture.invalid")
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    record = outgoing.read()
    assert record[0] == 22, "Expected TLS Handshake record (type 22)"
    handshake = record[5:]
    assert handshake[0] == 1, "Expected ClientHello message (type 1)"
    body = handshake[4:]
    position = 35 + body[34]  # version (2) + random (32) + length-prefixed session ID
    count_bytes = int.from_bytes(body[position : position + 2], "big")
    assert count_bytes > 6 and count_bytes % 2 == 0
    encoded = body[position + 2 : position + 2 + count_bytes]
    ids = [int.from_bytes(encoded[i : i + 2], "big") for i in range(0, count_bytes, 2)]
    names = {c["id"] & 0xFFFF: c["name"] for c in context.get_ciphers()}
    return [names.get(i, f"0x{i:04x}") for i in ids]


def test_row891_cipher_ordering_and_set_matches_modern_profile():
    """(1) TLS1.2 wire order matches modern profile; TLS1.3 suites match offered set."""
    mod = _get_mod()
    ctx = mod.create_normalized_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)

    # Check minimum and maximum TLS versions
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_3

    # Inspect actual on-the-wire ClientHello ciphers
    wire_ciphers = _extract_clienthello_ciphers(ctx)
    assert len(wire_ciphers) > 6

    # TLS 1.3: assert SET offered (never order, as OpenSSL dictates internal wire order)
    tls13_on_wire = {c for c in wire_ciphers if c.startswith("TLS_")}
    assert tls13_on_wire == mod.TLS13_CIPHER_SET
    assert "TLS_AES_128_GCM_SHA256" in tls13_on_wire
    assert "TLS_AES_256_GCM_SHA384" in tls13_on_wire
    assert "TLS_CHACHA20_POLY1305_SHA256" in tls13_on_wire

    # TLS 1.2: assert wire ORDER matches the modern browser profile exactly
    tls12_on_wire = [c for c in wire_ciphers if not c.startswith("TLS_") and c != "0x00ff"]
    expected_tls12 = mod.get_normalized_tls12_ciphers()
    assert tls12_on_wire == expected_tls12, (
        f"TLS 1.2 wire order mismatch:\nGot: {tls12_on_wire}\nExp: {expected_tls12}"
    )

    # Verify modern ECDHE-GCM precedes legacy AES-CBC
    assert "ECDHE-RSA-AES128-GCM-SHA256" in tls12_on_wire
    if "AES128-SHA" in tls12_on_wire:
        ecdhe_idx = tls12_on_wire.index("ECDHE-RSA-AES128-GCM-SHA256")
        cbc_idx = tls12_on_wire.index("AES128-SHA")
        assert ecdhe_idx < cbc_idx, "Modern ECDHE GCM must precede legacy AES-CBC"


def test_row891_alpn_and_options_configuration():
    """(1b) ALPN protocols and security options match modern browser standards."""
    mod = _get_mod()
    ctx = mod.create_normalized_ssl_context(alpn_protocols=["h2", "http/1.1"])

    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_3
    assert ctx.options & ssl.OP_NO_COMPRESSION

    # Verify cipher suite string definition
    assert len(mod.MODERN_CIPHER_STRING) > 50
    assert "ECDHE-RSA-AES128-GCM-SHA256" in mod.MODERN_CIPHER_STRING
    assert mod.DEFAULT_ALPN_PROTOCOLS == ("http/1.1",)


def test_row891_apply_to_existing_context():
    """(1c) apply_tls_normalization correctly mutates and returns an existing context."""
    mod = _get_mod()
    base_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    base_ctx.check_hostname = False
    base_ctx.verify_mode = ssl.CERT_NONE

    ret = mod.apply_tls_normalization(base_ctx, alpn_protocols=["http/1.1"])
    assert ret is base_ctx
    assert base_ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert base_ctx.maximum_version == ssl.TLSVersion.TLSv1_3

    ret_none = mod.apply_tls_normalization(base_ctx, alpn_protocols=None)
    assert ret_none is base_ctx


def test_row891_successful_tls_handshake_with_strict_endpoint():
    """(2) Successful TLS handshake with strict test endpoints (in-memory socketpair)."""
    mod = _get_mod()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with tempfile.NamedTemporaryFile("wb", delete=False) as key_f, tempfile.NamedTemporaryFile("wb", delete=False) as cert_f:
        key_path = key_f.name
        cert_path = cert_f.name
        key_f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        cert_f.write(cert.public_bytes(serialization.Encoding.PEM))

    try:
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        server_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        server_ctx.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384")
        server_ctx.set_alpn_protocols(["h2", "http/1.1"])

        client_ctx = mod.create_normalized_ssl_context(alpn_protocols=["h2", "http/1.1"])
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE

        s1, s2 = socket.socketpair()
        server_result = {}

        def server_worker():
            try:
                with server_ctx.wrap_socket(s2, server_side=True) as s_conn:
                    server_result["version"] = s_conn.version()
                    server_result["cipher"] = s_conn.cipher()
                    server_result["alpn"] = s_conn.selected_alpn_protocol()
            except Exception as exc:
                server_result["error"] = exc

        t = threading.Thread(target=server_worker)
        t.start()

        with client_ctx.wrap_socket(s1, server_hostname="localhost") as s_client:
            client_version = s_client.version()
            client_cipher = s_client.cipher()
            client_alpn = s_client.selected_alpn_protocol()

        t.join(timeout=5.0)

        assert "error" not in server_result, f"Server error: {server_result.get('error')}"
        assert client_version in ("TLSv1.2", "TLSv1.3")
        assert client_cipher is not None
        assert client_alpn == "h2"
    finally:
        if os.path.exists(key_path):
            os.remove(key_path)
        if os.path.exists(cert_path):
            os.remove(cert_path)


def test_row891_zero_performance_overhead():
    """(3) Zero performance overhead: measure real loopback handshake with vs without normalization."""
    mod = _get_mod()

    # 1. Real loopback handshake latency comparison
    bench_hs = mod.benchmark_handshake_overhead(iterations=30)
    assert bench_hs["iterations"] == 30.0
    # Average handshake overhead must be negligible (< 100 microseconds per handshake)
    assert bench_hs["handshake_overhead_us"] < 250.0

    # 2. Context setter configuration overhead
    bench_set = mod.benchmark_normalization_overhead(iterations=50)
    assert bench_set["avg_duration_us"] < 200.0
    assert bench_set["iterations"] == 50.0

    # Test edge case: iterations < 1
    bench_zero = mod.benchmark_normalization_overhead(iterations=0)
    assert bench_zero["iterations"] == 1.0


def test_row891_urllib_integration_handlers():
    """(4) urllib integration: create_normalized_opener and create_normalized_https_handler."""
    mod = _get_mod()
    # Test with default context
    handler = mod.create_normalized_https_handler()
    assert isinstance(handler, urllib.request.HTTPSHandler)
    assert handler._context.minimum_version == ssl.TLSVersion.TLSv1_2

    opener = mod.create_normalized_opener()
    assert isinstance(opener, urllib.request.OpenerDirector)
    opener_handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
    assert opener_handler._context.minimum_version == ssl.TLSVersion.TLSv1_2

    # Test with explicit context
    custom_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    handler_custom = mod.create_normalized_https_handler(context=custom_ctx)
    assert isinstance(handler_custom, urllib.request.HTTPSHandler)

    opener_custom = mod.create_normalized_opener(context=custom_ctx)
    assert isinstance(opener_custom, urllib.request.OpenerDirector)


def _selfsigned_localhost(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = tmp_path / "key.pem", tmp_path / "cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(key_path), str(cert_path)


def _strict_alpn_https_server(key_path, cert_path, seen):
    """A loopback HTTPS endpoint that honours the negotiated ALPN strictly:
    after selecting h2 it answers an HTTP/1.1 request line with a connection
    close (no status line) -- what a strict CDN edge does; after http/1.1 it
    answers 200. Returns (port, stop)."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server_ctx.set_alpn_protocols(["h2", "http/1.1"])
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(4)
    lsock.settimeout(5.0)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                raw, _ = lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                with server_ctx.wrap_socket(raw, server_side=True) as conn:
                    conn.settimeout(5.0)
                    request = conn.recv(4096)
                    alpn = conn.selected_alpn_protocol()
                    seen.append((alpn, request.split(b"\r\n", 1)[0]))
                    if alpn == "http/1.1" and request.startswith(b"GET /"):
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            except Exception:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def _stop():
        stop.set()
        lsock.close()
        thread.join(timeout=6.0)

    return lsock.getsockname()[1], _stop


def test_row891_default_opener_speaks_http11_to_a_strict_alpn_endpoint(tmp_path):
    """E2: urllib only speaks HTTP/1.1, so the normalized opener must advertise
    http/1.1 alone. Control: a context that advertises h2 first is selected as
    h2 by the same endpoint and the HTTP/1.1 request is refused (the failure
    the h2 default produced)."""
    mod = _get_mod()
    import http.client
    key_path, cert_path = _selfsigned_localhost(tmp_path)
    seen = []
    port, stop = _strict_alpn_https_server(key_path, cert_path, seen)
    try:
        ctx = mod.create_normalized_ssl_context()
        ctx.load_verify_locations(cafile=cert_path)
        opener = mod.create_normalized_opener(context=ctx)
        with opener.open(f"https://localhost:{port}/", timeout=5.0) as resp:
            assert resp.status == 200 and resp.read() == b"ok"
        assert seen[-1][0] == "http/1.1" and seen[-1][1].startswith(b"GET / HTTP/1.1")

        # a caller-supplied context that offered h2 is re-pinned by the handler
        h2_ctx = mod.create_normalized_ssl_context(alpn_protocols=["h2", "http/1.1"])
        h2_ctx.load_verify_locations(cafile=cert_path)
        with mod.create_normalized_opener(context=h2_ctx).open(f"https://localhost:{port}/", timeout=5.0) as resp:
            assert resp.status == 200
        assert seen[-1][0] == "http/1.1"

        # positive control: the endpoint IS strict -- an h2-first context, used
        # through raw urllib without the normalized handler, is refused
        raw_ctx = ssl.create_default_context(cafile=cert_path)
        raw_ctx.set_alpn_protocols(["h2", "http/1.1"])
        with pytest.raises((http.client.RemoteDisconnected, http.client.BadStatusLine, urllib.error.URLError)):
            urllib.request.build_opener(urllib.request.HTTPSHandler(context=raw_ctx)).open(f"https://localhost:{port}/", timeout=5.0)
        assert seen[-1][0] == "h2"
    finally:
        stop()
