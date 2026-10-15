"""O1789 manifest-url-filter-gaps r1 (R228-C F3; coverage R216-D A1, R218-C A1 = R219-D S1).

F3: deep_detect.manifests._local_host folds a host the way a resolver does (NFKC + IDNA)
    before judging it: fullwidth "１２７.0.0.1" / "ｌｏｃａｌｈｏｓｔ" are local hosts.
Backstop: the PUBLIC_ONLY guarded transport never delegates a fullwidth loopback URL and
    refuses its IDNA form (127.0.0.1) as loopback.
Coverage: 6to4 / NAT64 / Teredo literals carrying 127.0.0.1 are refused by the transport;
    queue_templates held-value lookup is keyed by (URL, name): a mask on another URL drops.
Each test carries a MUFG-<item> diagnostic; every fix test has a control beside it.
Failure lines carry counts and routes only, never a header value.
"""
from __future__ import annotations

import json

import httpx
import pytest

from bulk_downloader import queue_templates as qt
from bulk_downloader import streaming_manifest as sm
from bulk_downloader.deep_detect import manifests as M
from bulk_downloader.ssrf_transport import PUBLIC_ONLY, GuardedTransportRefused, guarded_transport

BD_GATE_SCOPE = "module"
PUB = "https://cdn.test/m/master.m3u8"
MPD_URL = "https://cdn.test/d/manifest.mpd"
LOCAL_MPD_URL = "http://127.0.0.1:8080/d/manifest.mpd"
FW_LOOPBACK = "１２７.0.0.1"  # １２７.0.0.1 (fullwidth digits)
FW_LOCALHOST = "ｌｏｃａｌｈｏｓｔ"  # ｌｏｃａｌｈｏｓｔ
FOLDED_LOCAL = [FW_LOOPBACK, FW_LOCALHOST, FW_LOOPBACK + ".", "１０.0.0.5", "0x７f.1",
                "２１３０７０６４３３", "::１",
                "ＬＯＣＡＬＨＯＳＴ", "a.ｌｏｃａｌｈｏｓｔ"]
FOLDED_NOT_LOCAL = ["ｅｘａｍｐｌｅ.com", "８.8.8.8", "bücher.example",
                    "xn--bcher-kva.example", FW_LOOPBACK + ".example.com", "a..b", "x" * 64 + ".example", "127.0.0.1.example.com"]


# ── F3: a host is judged after the NFKC / IDNA fold a resolver applies ───────
@pytest.mark.parametrize("host", FOLDED_LOCAL)
def test_f3_folded_local_host_is_local(host):
    assert M._local_host(host) is True, f"MUFG-F3: _local_host({host!r}) -> False (fullwidth / folded local host passes)"


@pytest.mark.parametrize("host", FOLDED_NOT_LOCAL)
def test_f3_control_folded_public_or_name_not_local(host):
    assert M._local_host(host) is False, f"MUFG-F3: _local_host({host!r}) -> True (public / name judged local)"


def test_f3_control_plain_spellings_unchanged():
    assert all(M._local_host(h) for h in ["localhost", "127.0.0.1", "::1", "2130706433", "0x7f.1", "10.0.0.5"])
    assert not any(M._local_host(h) for h in ["8.8.8.8", "example.com", "134744072", "cdn.test"])


@pytest.mark.parametrize("host", [FW_LOOPBACK + ":8080", FW_LOCALHOST, "[::１]"])
def test_f3_folded_local_variant_refused_end_to_end(host):
    text = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=9000,RESOLUTION=1920x1080\n"
            f"http://{host}/admin.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360\nv360.m3u8\n")
    deep = [v["url"] for v in M.parse_hls_master(text, base_url=PUB)["variants"]]
    assert deep == ["https://cdn.test/m/v360.m3u8"], f"MUFG-F3: deep_detect keeps a variant on folded local host {host!r}: {deep}"
    stream = [v.uri for v in sm.parse_streaming_manifest(text, base_url=PUB).variants]
    assert stream == ["https://cdn.test/m/v360.m3u8"], f"MUFG-F3: streaming_manifest keeps a variant on folded local host {host!r}: {stream}"


def test_f3_control_folded_public_variant_admitted():
    host = "ｃｄｎ.test"  # ｃｄｎ.test -> cdn.test, a name
    text = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\nhttp://{host}/v.m3u8\n"
    assert [v["url"] for v in M.parse_hls_master(text, base_url=PUB)["variants"]] == [f"http://{host}/v.m3u8"]


# ── Backstop + coverage: the PUBLIC_ONLY transport ──────────────────────────
_PUBLIC_V4 = "93.184.216.34"


@pytest.fixture
def recorder(monkeypatch):
    """The socket-opening base transport, replaced: every request that would
    have left the process is recorded and answered 200."""
    seen: list = []

    def handle(self, request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return seen


def test_backstop_fullwidth_loopback_refused_after_idna_and_never_delegated(recorder):
    idna_form = FW_LOOPBACK.encode("idna").decode("ascii")
    assert idna_form == "127.0.0.1", f"MUFG-BACKSTOP: IDNA of fullwidth loopback is {idna_form!r}"
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        with pytest.raises(GuardedTransportRefused) as refused:
            cl.get(f"http://{idna_form}:8080/x")
        assert refused.value.reason == "loopback", f"MUFG-BACKSTOP: reason {refused.value.reason}"
        with pytest.raises((httpx.InvalidURL, GuardedTransportRefused)):
            cl.get(f"http://{FW_LOOPBACK}:8080/x")
    assert recorder == [], f"MUFG-BACKSTOP: a fullwidth / IDNA loopback request left the process: {recorder}"


@pytest.mark.parametrize("host,reason", [
    ("[2002:7f00:1::]", "private"),                           # 6to4 carrying 127.0.0.1
    ("[64:ff9b::7f00:1]", "reserved"),                        # NAT64 well-known prefix carrying 127.0.0.1
    ("[2001:0:4136:e378:8000:63bf:3fff:fdd2]", "private"),    # Teredo
])
def test_coverage_6to4_nat64_teredo_literals_refused(recorder, host, reason):
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        with pytest.raises(GuardedTransportRefused) as refused:
            cl.get(f"http://{host}:8080/x")
    assert refused.value.reason == reason, f"MUFG-SSRF-TUNNEL {host} reason={refused.value.reason}"
    assert recorder == [], f"MUFG-SSRF-TUNNEL {host} delegated: {recorder}"


def test_coverage_control_public_literal_admitted(recorder):
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        assert cl.get(f"http://{_PUBLIC_V4}/x").status_code == 200
    assert len(recorder) == 1, f"MUFG-SSRF-TUNNEL control: {len(recorder)} delegated"


# ── Coverage: queue_templates held lookup is keyed by (URL, name) ───────────
CANARY = "mufgR1canary7f"
U1 = "https://h.example/v/1"
U2 = "https://h.example/v/2"
ENTRY1 = f"{U1}\tAuthorization=Bearer {CANARY}"
MASK = "[REDACTED]"  # db.QUEUE_HEADER_MASK, a literal so the test fails on behaviour


def test_qhr_held_lookup_keyed_by_url_and_name():
    held = qt.held_header_values([ENTRY1])
    other = qt.unmasked_entry(f"{U2}\tAuthorization={MASK}\tReferer=r", held)
    assert other == f"{U2}\tReferer=r", f"MUFG-QHR-KEY: mask on another URL resolved (canary x{other.count(CANARY)}, mask x{other.count(MASK)})"
    own = qt.unmasked_entry(f"{U1}\tAuthorization={MASK}", held)
    assert own == ENTRY1, f"MUFG-QHR-KEY control: own URL not restored (canary x{own.count(CANARY)})"


def test_qhr_import_mask_on_new_url_for_a_name_held_elsewhere_is_dropped(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader import app_sites_queue, db
    sid = fresh_app.post("/api/sites", json={"name": "mufg"}).get_json()["id"]
    A.runners[sid].load_urls([ENTRY1])
    rows = [{"url": f"{U1}\tAuthorization={MASK}"}, {"url": f"{U2}\tAuthorization={MASK}\tReferer=r"}]
    with A.app.test_request_context(f"/api/sites/{sid}/queue/import", method="POST",
                                    json={"rows": rows, "mode": "replace"}):
        out = app_sites_queue.api_queue_import(sid)
    resp, code = out if isinstance(out, tuple) else (out, out.status_code)
    body = resp.get_json()
    assert code == 200 and body["added"] == 2 and body.get("headers_dropped") == 1, \
        f"MUFG-QHR-IMPORT code={code} added={body.get('added')} dropped={body.get('headers_dropped')}"
    jobs = A.runners[sid].jobs
    own_kept = (jobs[U1].get("custom_headers") or {}).get("Authorization") == f"Bearer {CANARY}"
    assert own_kept, "MUFG-QHR-IMPORT control: own URL lost its held value"
    assert jobs[U2].get("custom_headers") == {"Referer": "r"}, \
        f"MUFG-QHR-IMPORT: name held on another URL pulled onto a new URL (names={sorted(jobs[U2].get('custom_headers') or {})})"
    stored = {r["url"]: r.get("custom_headers") or "" for r in db.queue_load(sid)}
    assert MASK not in json.dumps(stored), "MUFG-QHR-IMPORT: mask stored as a value"
    assert CANARY not in str(stored.get(U2)), "MUFG-QHR-IMPORT: foreign value stored on the new URL"
