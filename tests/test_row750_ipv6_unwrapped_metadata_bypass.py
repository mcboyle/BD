"""Row 750 — IPV6-UNWRAPPED-METADATA-BYPASS.

An IPv6 address can CARRY an IPv4 address inside it. 169.254.169.254 — the
cloud-metadata endpoint, the single most valuable SSRF target in the tree — has
at least four distinct IPv6 spellings, and the interesting thing about them is
what the stdlib says when you ask about the OUTER address:

    ::ffff:169.254.169.254   is_link_local FALSE   (is_reserved True, by luck)
    2002:a9fe:a9fe::         is_link_local FALSE   is_reserved FALSE
    2001:0:...:5601:5601     is_link_local FALSE   is_reserved FALSE
    64:ff9b::a9fe:a9fe       is_link_local FALSE   (is_reserved True, by luck)

Both host classifiers in this tree read the outer address only, so the two
spellings the stdlib flags nothing on — 6to4 and Teredo — walked straight
through `bulk_downloader.hooks._host_ok_for_hook`, which is the predicate that
deliberately RELAXES private and loopback so the plex/jellyfin/home-assistant
integrations can reach the LAN. Relaxation plus outer-only classification is the
whole defect: a never-allowed address wearing an IPv6 costume is not private, is
not reserved, is not link-local, and so it is allowed.

WHAT THIS GATE PINS.

1. Every distinct metadata spelling is refused by BOTH classifiers, and the
   count of refusals equals the corpus — so a spelling cannot be dropped from
   the corpus to make the gate pass, and a partial fix that catches mapped but
   not 6to4 fails on the count.

2. The provider classifier must refuse them BY NAME (link-local). It already
   refused every spelling before this row, but for the wrong reason: 6to4 and
   Teredo are inside `is_private` on CPython 3.12, so the refusal was an
   accident of a stdlib range table rather than a decision about metadata. A
   refusal whose reason is wrong is a refusal that moves when the stdlib moves.

3. The never-allowed checks run BEFORE the private relaxation. This is asserted
   behaviourally, not structurally: 6to4 metadata has is_private True on the
   outer address, so if the unwrap ran after the relaxation the hook predicate
   would allow it. A comment cannot satisfy this and neither can a reordering
   that happens to leave the same result.

4. The relaxation SURVIVES. 6to4 of 192.168.1.10 and 6to4 of a public address
   still pass the hook predicate. A blanket "refuse all 6to4" would satisfy
   every assertion in point 1 and is exactly the over-correction this control
   exists to catch.

DELIBERATELY NOT CLAIMED: that any of these spellings is reachable from a
request in production, or that a hook target is routable to metadata. Row 749
owns the 687/703 routing question and it is unproven. This gate is about what
the classifiers decide when they are asked, which is measurable here and now.
"""
from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest  # noqa: F401  (harmless under the custom runner)

from bulk_downloader.hooks import _host_ok_for_hook, _validate_webhook_url
from bulk_downloader.provider_resolve_impl._common import (
    HostSafetyReason,
    SSRFBlocked,
    _SSRFGuardedTransport_factory,
    _classify_ip,
    _is_safe_public_host,
)

BD_GATE_SCOPE = "module"

_METADATA_V4 = ipaddress.IPv4Address("169.254.169.254")
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")

# Distinct spellings of the SAME never-allowed destination, as a caller would
# write them. Kept as strings rather than addresses because that is the shape
# the predicates take, and because two spellings ("::ffff:169.254.169.254" and
# "::ffff:a9fe:a9fe") normalise to one address while remaining two things a
# config file can contain.
_METADATA_SPELLINGS = (
    ("plain", "169.254.169.254"),
    ("mapped-dotted", "::ffff:169.254.169.254"),
    ("mapped-hex", "::ffff:a9fe:a9fe"),
    ("6to4", "2002:a9fe:a9fe::"),
    ("6to4-with-host-bits", "2002:a9fe:a9fe:1::5"),
    ("teredo-client", "2001:0:4136:e378:8000:63bf:5601:5601"),
    ("nat64-well-known", "64:ff9b::a9fe:a9fe"),
)

# Addresses that must KEEP passing. Each one would break under a different bad
# fix, which is why they are not a single blanket control.
_STILL_ALLOWED_BY_THE_HOOK_PREDICATE = (
    ("global-ipv6", "2606:4700:4700::1111"),        # breaks if v6 is refused wholesale
    ("6to4-of-public", "2002:0102:0304::"),          # breaks if 6to4 is refused wholesale
    ("6to4-of-private", "2002:c0a8:010a::"),         # breaks if the relaxation is skipped for embedded v4
    ("private-v4", "192.168.1.10"),                  # the LAN integrations
    ("loopback-v4", "127.0.0.1"),                    # the LAN integrations
)


def _host_arg(literal: str) -> str:
    """A v6 literal reaches these predicates bracketed, as it does from a URL."""
    return f"[{literal}]" if ":" in literal else literal


def _v6_dns_answer(literal: str):
    """One controlled DNS answer, without asking a real resolver."""
    return (socket.AF_INET6, socket.SOCK_STREAM, 0, "", (literal, 443, 0, 0))


def _embedded_ipv4_independently(literal: str):
    """Re-derive the carried IPv4 addresses WITHOUT the product helpers.

    If this used the code under test, a broken unwrap would quietly empty the
    corpus and every assertion below would pass over nothing.
    """
    addr = ipaddress.ip_address(literal)
    if isinstance(addr, ipaddress.IPv4Address):
        return (addr,)
    carried = []
    if addr.ipv4_mapped is not None:
        carried.append(addr.ipv4_mapped)
    if addr.sixtofour is not None:
        carried.append(addr.sixtofour)
    if addr.teredo:
        carried.extend(addr.teredo)
    if addr in _NAT64_WELL_KNOWN:
        carried.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return tuple(carried)


def test_the_corpus_really_carries_the_metadata_address_before_any_verdict():
    """The fixture proves its own shape: nonzero, and every member carries it."""
    assert len(_METADATA_SPELLINGS) == 7, (
        "the metadata corpus changed size; a spelling may have been dropped to "
        "make a refusal count pass"
    )
    missing = [
        label for label, literal in _METADATA_SPELLINGS
        if _METADATA_V4 not in _embedded_ipv4_independently(literal)
    ]
    assert not missing, (
        f"these corpus members do not actually carry {_METADATA_V4}: {missing} — "
        f"the corpus would be asserting refusals of addresses that are not the "
        f"metadata endpoint at all"
    )
    labels = [label for label, _ in _METADATA_SPELLINGS]
    assert len(set(labels)) == len(labels), f"duplicate corpus labels: {labels}"


def test_every_metadata_spelling_is_refused_by_the_hook_predicate():
    """`_host_ok_for_hook` relaxes private/loopback; it must still refuse these."""
    allowed = [
        label for label, literal in _METADATA_SPELLINGS
        if _host_ok_for_hook(_host_arg(literal))[0]
    ]
    refused = len(_METADATA_SPELLINGS) - len(allowed)
    assert not allowed, (
        f"_host_ok_for_hook ALLOWED these spellings of {_METADATA_V4}: {allowed} — "
        f"an IPv6 address carrying the cloud-metadata endpoint reached a webhook "
        f"target because only the outer address was classified"
    )
    assert refused == len(_METADATA_SPELLINGS) == 7, (
        f"expected all 7 corpus spellings refused, got {refused}"
    )


def test_every_metadata_spelling_is_refused_by_the_provider_classifier_by_name():
    """The provider refusal must say link-local, not land on a lucky range."""
    codes = {}
    for label, literal in _METADATA_SPELLINGS:
        ok, message = _classify_ip(ipaddress.ip_address(literal), literal)
        codes[label] = "ALLOWED" if ok else message.code
    wrong = {
        label: code for label, code in codes.items()
        if code is not HostSafetyReason.LINK_LOCAL
    }
    assert not wrong, (
        f"_classify_ip did not refuse these as link-local/metadata: {wrong} — "
        f"PRIVATE or RESERVED here means the refusal came from a stdlib range "
        f"table that happens to contain the wrapper prefix, not from a decision "
        f"about the address it carries"
    )
    assert len(codes) == len(_METADATA_SPELLINGS) == 7


def test_literal_host_path_classifies_embedded_metadata_by_name():
    """A parsed literal must still reach the shared classifier."""
    ok, message = _is_safe_public_host("2002:a9fe:a9fe::")
    assert not ok and message.code is HostSafetyReason.LINK_LOCAL, (
        "the literal-host path bypassed _classify_ip instead of refusing the "
        "embedded metadata address as link-local"
    )


def test_dns_host_path_classifies_embedded_metadata_by_name(monkeypatch):
    """A DNS result must be judged on the IPv4 destination it carries."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_v6_dns_answer("2002:a9fe:a9fe::")],
    )
    ok, message = _is_safe_public_host("metadata-through-dns.example")
    assert not ok and message.code is HostSafetyReason.LINK_LOCAL, (
        "the DNS-host path bypassed _classify_ip instead of refusing the "
        "embedded metadata address as link-local"
    )


def test_transport_path_classifies_embedded_metadata_by_name(monkeypatch):
    """Connect-time DNS must not send an embedded metadata target onward."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_v6_dns_answer("2002:a9fe:a9fe::")],
    )
    transport = _SSRFGuardedTransport_factory()()
    try:
        request = httpx.Request("GET", "https://metadata-through-transport.example/")
        with pytest.raises(SSRFBlocked) as blocked:
            transport._prepare_request(request)
    finally:
        transport.close()
    assert "link-local" in str(blocked.value)
    assert "169.254.169.254" in str(blocked.value)


def test_webhook_validator_uses_the_hook_metadata_classifier():
    """Webhook validation must preserve the hook predicate's metadata refusal."""
    ok, reason = _validate_webhook_url("https://[2002:a9fe:a9fe::]/webhook")
    assert not ok
    assert "link-local" in reason
    assert "169.254.169.254" in reason


def test_the_hook_relaxation_survives_the_unwrap():
    """The negative control: a blanket refusal passes every test above."""
    wrongly_refused = {}
    for label, literal in _STILL_ALLOWED_BY_THE_HOOK_PREDICATE:
        ok, reason = _host_ok_for_hook(_host_arg(literal))
        if not ok:
            wrongly_refused[label] = reason
    assert not wrongly_refused, (
        f"_host_ok_for_hook refused addresses the LAN integrations depend on: "
        f"{wrongly_refused} — the never-allowed checks must run over the carried "
        f"IPv4 without withdrawing the private/loopback relaxation"
    )


def test_the_provider_classifier_stays_public_only():
    """The provider is not the hook predicate: private and loopback still lose."""
    allowed, _ = _classify_ip(ipaddress.ip_address("2606:4700:4700::1111"), "cdn")
    assert allowed, "a global IPv6 address must still pass the public-only classifier"
    for literal, expected in (
        ("192.168.1.10", HostSafetyReason.PRIVATE),
        ("127.0.0.1", HostSafetyReason.LOOPBACK),
    ):
        ok, message = _classify_ip(ipaddress.ip_address(literal), literal)
        assert not ok and message.code is expected, (
            f"{literal} should still be refused as {expected}, got "
            f"{'ALLOWED' if ok else message.code}"
        )
