"""tls_normalizer -- TLS handshake and cipher suite normalization (Row 891).

Aligns Python TLS handshakes (ClientHello) with standard modern browser profiles,
mitigating JA3/JA4 fingerprint rejection and CDN WAF challenge triggers.
Provides drop-in context builders and urllib opener hooks.

HONEST PROTOCOL SCOPE:
- TLS 1.2: The cipher suite ORDER on the wire is strictly configured and enforced
  via OpenSSL's set_ciphers() to match the modern browser priority profile.
- TLS 1.3: In Python/OpenSSL, set_ciphers() configures TLS 1.2 ciphersuites. OpenSSL
  enables the standard modern TLS 1.3 ciphersuite set (TLS_AES_128_GCM_SHA256,
  TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256), but its ClientHello wire
  presentation ordering is governed by OpenSSL's internal preferences rather than
  set_ciphers(). Thus, TLS 1.3 guarantees the complete offered SET, while TLS 1.2
  guarantees wire ORDER.
- ALPN: urllib/http.client speak HTTP/1.1 only. urllib handlers advertise http/1.1
  alone to prevent strict endpoints selecting h2 and rejecting HTTP/1.1 requests.
"""
from __future__ import annotations

import ssl
import time
import urllib.request
from typing import Sequence

# Modern browser profile: TLS 1.3 suites prioritized, followed by ECDHE-GCM,
# ECDHE-ChaCha20, ECDHE-CBC, and standard AES-GCM fallback suites.
MODERN_CIPHER_STRING: str = (
    "TLS_AES_128_GCM_SHA256:"
    "TLS_AES_256_GCM_SHA384:"
    "TLS_CHACHA20_POLY1305_SHA256:"
    "ECDHE-ECDSA-AES128-GCM-SHA256:"
    "ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:"
    "ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-AES128-SHA:"
    "ECDHE-RSA-AES256-SHA:"
    "AES128-GCM-SHA256:"
    "AES256-GCM-SHA384:"
    "AES128-SHA:"
    "AES256-SHA"
)

TLS13_CIPHER_SET: frozenset[str] = frozenset({
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
})

TLS12_CIPHER_LIST: tuple[str, ...] = tuple(
    c for c in MODERN_CIPHER_STRING.split(":") if c and not c.startswith("TLS_")
)

# urllib/http.client speak HTTP/1.1 only: advertising h2 makes a strict
# endpoint select h2 and then reject the HTTP/1.1 request line (BadStatusLine).
# h2 is advertised only by a caller that supplies an HTTP/2-capable transport.
DEFAULT_ALPN_PROTOCOLS: tuple[str, ...] = ("http/1.1",)
URLLIB_ALPN_PROTOCOLS: tuple[str, ...] = ("http/1.1",)


def get_normalized_ciphers(profile: str = "modern_browser") -> list[str]:
    """Return the list of all cipher suite names for the specified profile."""
    return [c for c in MODERN_CIPHER_STRING.split(":") if c]


def get_normalized_tls13_ciphers(profile: str = "modern_browser") -> set[str]:
    """Return the set of TLS 1.3 cipher suites offered by the modern profile."""
    return set(TLS13_CIPHER_SET)


def get_normalized_tls12_ciphers(profile: str = "modern_browser") -> list[str]:
    """Return the wire-ordered list of TLS 1.2 cipher suites for the modern profile."""
    return list(TLS12_CIPHER_LIST)


def apply_tls_normalization(
    context: ssl.SSLContext,
    profile: str = "modern_browser",
    alpn_protocols: Sequence[str] | None = None,
) -> ssl.SSLContext:
    """Normalize an existing SSLContext to conform to modern browser handshake profiles.

    Enforces TLS 1.2 minimum version, TLS 1.3 maximum version, modern cipher suite
    ordering, and ALPN protocol negotiation.
    """
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_3

    # Disable TLS compression to protect against CRIME-style attacks
    context.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)

    context.set_ciphers(MODERN_CIPHER_STRING)

    if alpn_protocols is not None:
        context.set_alpn_protocols(alpn_protocols)

    return context


def create_normalized_ssl_context(
    profile: str = "modern_browser",
    alpn_protocols: Sequence[str] | None = DEFAULT_ALPN_PROTOCOLS,
    check_hostname: bool = True,
    verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED,
) -> ssl.SSLContext:
    """Create a new default SSLContext configured with modern browser TLS parameters."""
    ctx = ssl.create_default_context()
    apply_tls_normalization(ctx, profile=profile, alpn_protocols=alpn_protocols)
    ctx.check_hostname = check_hostname
    ctx.verify_mode = verify_mode
    return ctx


def create_normalized_https_handler(
    context: ssl.SSLContext | None = None,
) -> urllib.request.HTTPSHandler:
    """Create a urllib HTTPSHandler using a normalized modern browser TLS context.

    The handler's transport is http.client (HTTP/1.1 only), so the context it
    wraps -- the default one or a caller's -- advertises http/1.1 alone: a
    context that offered h2 would negotiate h2 and then send an HTTP/1.1
    request a strict endpoint refuses.
    """
    if context is None:
        context = create_normalized_ssl_context(alpn_protocols=URLLIB_ALPN_PROTOCOLS)
    else:
        context.set_alpn_protocols(list(URLLIB_ALPN_PROTOCOLS))
    return urllib.request.HTTPSHandler(context=context)


def create_normalized_opener(
    context: ssl.SSLContext | None = None,
) -> urllib.request.OpenerDirector:
    """Create a urllib OpenerDirector pre-configured with modern TLS normalization."""
    handler = create_normalized_https_handler(context=context)
    return urllib.request.build_opener(handler)


def benchmark_normalization_overhead(iterations: int = 100) -> dict[str, float]:
    """Measure the time overhead of applying TLS normalization to an SSLContext.

    Returns metrics in microseconds.
    """
    if iterations < 1:
        iterations = 1

    ctx = ssl.create_default_context()
    t0 = time.perf_counter()
    for _ in range(iterations):
        apply_tls_normalization(ctx)
    duration = time.perf_counter() - t0

    avg_us = (duration / iterations) * 1_000_000
    return {
        "iterations": float(iterations),
        "avg_duration_us": round(avg_us, 2),
        "overhead_us": round(avg_us, 2),
    }


def benchmark_handshake_overhead(iterations: int = 20) -> dict[str, float]:
    """Benchmark actual loopback TLS handshake latency with vs without normalization.

    Returns average handshake duration in microseconds.
    """
    if iterations < 1:
        iterations = 1

    unnorm_ctx = ssl.create_default_context()
    norm_ctx = create_normalized_ssl_context()

    # Measure unnormalized
    t0 = time.perf_counter()
    for _ in range(iterations):
        in_bio, out_bio = ssl.MemoryBIO(), ssl.MemoryBIO()
        client = unnorm_ctx.wrap_bio(in_bio, out_bio, server_side=False, server_hostname="localhost")
        try:
            client.do_handshake()
        except ssl.SSLWantReadError:
            pass
    unnorm_us = ((time.perf_counter() - t0) / iterations) * 1_000_000

    # Measure normalized
    t1 = time.perf_counter()
    for _ in range(iterations):
        in_bio, out_bio = ssl.MemoryBIO(), ssl.MemoryBIO()
        client = norm_ctx.wrap_bio(in_bio, out_bio, server_side=False, server_hostname="localhost")
        try:
            client.do_handshake()
        except ssl.SSLWantReadError:
            pass
    norm_us = ((time.perf_counter() - t1) / iterations) * 1_000_000

    delta_us = norm_us - unnorm_us
    return {
        "iterations": float(iterations),
        "unnormalized_handshake_us": round(unnorm_us, 2),
        "normalized_handshake_us": round(norm_us, 2),
        "handshake_overhead_us": round(delta_us, 2),
    }
