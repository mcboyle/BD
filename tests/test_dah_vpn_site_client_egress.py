"""dah-vpn (deep-adapters-http F1): an in-process client built for a site leaves the way the site leaves.

Five clients carried a site's cookies or fetched from its origin through an unproxied transport while the
payload clients beside them resolved the site's egress: the JSON-API lookup, the tier probe, the parallel
download's resume-validator HEAD, the dl8 Badoink candidate probe and the cover-art fetch. They now ask
``TransportMixin._site_client_egress`` first, which refuses (no client is built) when the egress cannot be
resolved or a ``vpn_required`` site has none.

Merged rows (deep-adapters-transport F1 F2 F3 F15): the resolver they all share, ``_download_proxy_url``,
itself fails closed -- any resolver failure on a ``vpn_required`` site, a required site with no tunnel, an
unreadable ``proxy`` / ``proxy_pool`` -- and a pool member no longer outranks a tripped kill switch, rotation
is fair while a member flaps, and the direct download feeds the pool's health.

Nothing here touches the network: every transport the code asks ``ssrf_transport.guarded_transport`` for is
recorded (with the ``proxy=`` it was asked to use) and answered by an in-process ``httpx.MockTransport``.
"""
import ast
import inspect
import types
from pathlib import Path

import httpx
import pytest

from bulk_downloader import (extractors_dl8, extractors_jsonapi, mp4_metadata, runner_extractors,
                             runner_integrity, runner_transport, ssrf_transport, tier_probe, vpn_runtime)

BD_GATE_SCOPE = "module"

HOST = "93.184.216.34"          # a public literal: passes the public-host checks; never contacted
PROXY = "socks5://127.0.0.1:19999"
SID = "dahvpn01"
SCENE = {"title": "t", "media": [{"name": "h264", "sources": [
    {"resolution": 1080, "height": 1080, "width": 1920, "url": f"http://{HOST}/v_1080p.mp4"}]}]}


class _Runner(runner_extractors.ExtractorsMixin, runner_integrity.IntegrityMixin, runner_transport.TransportMixin):
    """The three real mixins on a bare object: no browser, no DB, no threads of its own."""


@pytest.fixture
def rig(monkeypatch, tmp_path):
    built, sent = [], []

    def handler(request):
        sent.append((request.method, str(request.url), request.headers.get("cookie", "")))
        if "/heresphere/" in str(request.url):
            return httpx.Response(200, json=SCENE)
        if request.method == "HEAD" and request.url.path == "/big.mp4":
            return httpx.Response(200, headers={"etag": '"e1"', "content-length": "4096"})
        return httpx.Response(404)

    dead_proxies = set()

    def recording_transport(policy, **kw):
        owner = next((f.function for f in inspect.stack()[1:5] if f.function != "recording_transport"), "?")
        built.append((owner, kw.get("proxy")))
        if kw.get("proxy") in dead_proxies:

            def refuse(request):
                raise httpx.ConnectError("proxy unreachable", request=request)

            return httpx.MockTransport(refuse)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(ssrf_transport, "guarded_transport", recording_transport)
    monkeypatch.setattr(vpn_runtime, "_site_to_tunnel", dict(vpn_runtime._site_to_tunnel))
    monkeypatch.setattr(vpn_runtime, "_site_required", dict(vpn_runtime._site_required))
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)

    r = object.__new__(_Runner)
    r.site_id = SID
    r.cookies = [{"name": "sess", "value": "SITE-SECRET", "domain": HOST, "path": "/"}]
    r.config = {"name": "fixture", "use_curl_cffi": False, "use_jsonapi": True,
                "jsonapi_url": f"http://{HOST}/heresphere", "jsonapi_protocol": "heresphere",
                "tier_probe_enabled": True, "tier_probe_pattern": r"_(?P<tier>\d{3,4})p\.mp4",
                "tier_probe_ladder": "2160,1080"}
    r.events = []
    r._update_job = lambda *a, **k: None
    r.log_event = lambda kind, msg="", **k: r.events.append((kind, msg))
    return types.SimpleNamespace(
        r=r, built=built, sent=sent, tmp=tmp_path, monkeypatch=monkeypatch, dead_proxies=dead_proxies
    )


# ---------------------------------------------------------------- the five clients, driven through the runner
def _jsonapi(rig):
    return rig.r._try_jsonapi_extractor(f"http://{HOST}/scene/12345")


def _tier(rig):
    return rig.r._probe_for_higher_tier(f"http://{HOST}/v_1080p.mp4", referer=f"http://{HOST}/")


def _parallel(rig):
    ctx = types.SimpleNamespace(cookies=lambda *a, **k: list(rig.r.cookies))
    try:
        rig.r._http_download_parallel(f"http://{HOST}/scene/1", ctx, f"http://{HOST}/big.mp4",
                                      Path(rig.tmp) / "big.mp4", 8 * 1024 * 1024, 2)
    except Exception as e:           # the transfer itself is not under test: it ends after the HEAD
        return type(e).__name__
    return "returned"


def _dl8(rig):
    cands = [extractors_dl8.BadoinkCandidate(url=f"http://{HOST}/m_2160p.mp4", tier=2160, suffix="_2160p")]
    result = types.SimpleNamespace(ok=True, via="badoink_predict", badoink_candidates=cands,
                                   url=f"http://{HOST}/trailer.mp4", tier=1080, error="", error_detail="")
    rig.monkeypatch.setattr(runner_extractors._dl8, "extract_from_page", lambda *a, **k: result)
    try:
        return rig.r._try_dl8_extractor(f"http://{HOST}/scene/1", object())
    except Exception as e:           # what follows a failed probe is not under test
        return type(e).__name__


def _cover(rig):
    rig.monkeypatch.setattr(mp4_metadata, "is_available", lambda: True)
    video = Path(rig.tmp) / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)  # an MP4 by extension and by magic
    return rig.r._embed_metadata_if_mp4(
        str(video), title="t", source_url=f"http://{HOST}/scene/1", thumbnail_url=f"http://{HOST}/c.jpg"
    )


CLIENTS = [("jsonapi", _jsonapi, "fetch_scene"), ("tier_probe", _tier, "probe_higher_tiers"),
           ("parallel_head", _parallel, "_http_download_parallel"), ("dl8", _dl8, "probe_badoink_candidates"),
           ("cover", _cover, "fetch_cover")]
IDS = [c[0] for c in CLIENTS]


def _first(rig, owner):
    mine = [proxy for who, proxy in rig.built if who == owner]
    return mine[0] if mine else "NO-CLIENT-BUILT"


@pytest.mark.parametrize("name,drive,owner", CLIENTS, ids=IDS)
def test_a_proxied_sites_client_is_built_on_the_sites_proxy(rig, name, drive, owner):
    rig.r.config["proxy"] = PROXY
    assert rig.r._download_proxy_url() == PROXY                # control: the payload clients' answer
    drive(rig)
    assert _first(rig, owner) == PROXY, f"{name}: client built with proxy={_first(rig, owner)!r} for a proxied site"


@pytest.mark.parametrize("name,drive,owner", CLIENTS, ids=IDS)
def test_a_vpn_required_site_with_its_tunnel_gone_gets_no_client(rig, name, drive, owner):
    vpn_runtime._site_to_tunnel[SID] = "tun-not-registered"
    vpn_runtime._site_required[SID] = True
    with pytest.raises(vpn_runtime.VPNRequiredError):          # control: the payload clients fail closed here
        rig.r._download_proxy_url()
    drive(rig)
    assert (rig.built, rig.sent) == ([], []), f"{name}: built {rig.built}, sent {rig.sent}"


@pytest.mark.parametrize("name,drive,owner", [c for c in CLIENTS if c[0] != "parallel_head"],
                         ids=[i for i in IDS if i != "parallel_head"])
def test_a_vpn_required_site_with_no_tunnel_mapped_gets_no_client(rig, name, drive, owner):
    # the tunnel resolver answers None here (it does not raise): a required site must still not go out clear
    vpn_runtime._site_required[SID] = True
    drive(rig)
    assert (rig.built, rig.sent) == ([], []), f"{name}: built {rig.built}, sent {rig.sent}"


@pytest.mark.parametrize("name,drive,owner", [c for c in CLIENTS if c[0] != "parallel_head"],
                         ids=[i for i in IDS if i != "parallel_head"])
def test_a_requirement_that_cannot_be_read_gets_no_client(rig, name, drive, owner):
    def boom(_sid):
        raise RuntimeError("vpn state unreadable")
    rig.monkeypatch.setattr(vpn_runtime, "is_vpn_required_for_site", boom)
    drive(rig)
    assert (rig.built, rig.sent) == ([], []), f"{name}: built {rig.built}, sent {rig.sent}"


@pytest.mark.parametrize("name,drive,owner", CLIENTS, ids=IDS)
def test_control_a_plain_site_still_gets_its_client_and_sends(rig, name, drive, owner):
    drive(rig)
    assert _first(rig, owner) is None
    assert any(HOST in url for _m, url, _c in rig.sent), f"{name}: nothing was sent for a site with no egress rule"


def test_the_refused_jsonapi_lookup_says_why(rig):
    vpn_runtime._site_to_tunnel[SID] = "tun-not-registered"
    vpn_runtime._site_required[SID] = True
    assert _jsonapi(rig) is False
    assert [k for k, m in rig.r.events if k == "jsonapi_extract_failed" and "egress_refused" in m]


def test_the_refused_tier_probe_keeps_the_known_url(rig):
    vpn_runtime._site_required[SID] = True
    assert _tier(rig) == f"http://{HOST}/v_1080p.mp4"


# ---------------------------------------------------------------- the helper itself
def test_helper_answers(rig):
    r = rig.r
    assert r._site_client_egress("t") == (True, None)
    r.config["proxy"] = "  " + PROXY + " "
    assert r._site_client_egress("t") == (True, PROXY)
    r.config.pop("proxy")
    rig.monkeypatch.setattr(_Runner, "_download_proxy_url", lambda self: (_ for _ in ()).throw(OSError("x")))
    assert r._site_client_egress("t") == (False, None)         # any resolution failure refuses, not only the VPN one


# ---------------------------------------------------------------- tree gate: the transports stay on a proxy
GATED = [(extractors_jsonapi, "fetch_scene"), (tier_probe, "probe_higher_tiers"),
         (extractors_dl8, "probe_badoink_candidates"), (mp4_metadata, "fetch_cover"),
         (runner_transport.TransportMixin, "_http_download_parallel")]


@pytest.mark.parametrize("holder,fn", GATED, ids=[f for _h, f in GATED])
def test_every_guarded_transport_in_these_clients_names_a_proxy(holder, fn):
    src = inspect.getsource(getattr(holder, fn))
    tree = ast.parse("if 1:\n" + src if src.startswith(" ") else src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "guarded_transport"]
    assert calls, f"{fn}: no guarded_transport call found -- the gate is looking at the wrong function"
    bare = [n.lineno for n in calls if "proxy" not in [k.arg for k in n.keywords]]
    assert not bare, f"{fn}: guarded_transport built without proxy= at source line(s) {bare}"


# ================================================================ merged rows: the shared resolver fails closed
RESOLVER_FAILURES = [RuntimeError, KeyError, OSError, AttributeError]


def _require_with_resolver(rig, behaviour):
    vpn_runtime._site_to_tunnel[SID] = "tun-1"
    vpn_runtime._site_required[SID] = True
    rig.monkeypatch.setattr(vpn_runtime, "get_socks_url_for_site", behaviour)


def _raises(exc):
    def resolver(_sid):
        raise exc("resolver broke")

    return resolver


@pytest.mark.parametrize("exc", RESOLVER_FAILURES, ids=[e.__name__ for e in RESOLVER_FAILURES])
def test_f1_any_resolver_failure_on_a_required_site_refuses(rig, exc):
    _require_with_resolver(rig, _raises(exc))
    with pytest.raises(vpn_runtime.VPNRequiredError) as caught:
        rig.r._download_proxy_url()                    # was: "continuing unproxied", return None
    assert exc.__name__ in str(caught.value) and SID in str(caught.value)


def test_f1_a_required_site_with_no_tunnel_mapped_refuses(rig):
    vpn_runtime._site_required[SID] = True
    with pytest.raises(vpn_runtime.VPNRequiredError, match="no tunnel is mapped"):
        rig.r._download_proxy_url()


def test_f1_an_unreadable_requirement_refuses(rig):
    def boom(_sid):
        raise KeyError("state")

    rig.monkeypatch.setattr(vpn_runtime, "is_vpn_required_for_site", boom)
    with pytest.raises(vpn_runtime.VPNRequiredError, match="could not determine"):
        rig.r._download_proxy_url()


def test_f1_controls_the_open_and_the_working_postures_are_kept(rig):
    r = rig.r
    assert r._download_proxy_url() is None                                  # no rule: degrade open
    rig.monkeypatch.setattr(vpn_runtime, "get_socks_url_for_site", _raises(RuntimeError))
    vpn_runtime._site_to_tunnel[SID] = "tun-1"
    assert r._download_proxy_url() is None                                  # NOT required + broken resolver: open
    vpn_runtime._site_required[SID] = True
    rig.monkeypatch.setattr(vpn_runtime, "get_socks_url_for_site", lambda _s: "socks5://127.0.0.1:1081")
    assert r._download_proxy_url() == "socks5://127.0.0.1:1081"             # required + tunnel up
    r.config["proxy"] = PROXY
    assert r._download_proxy_url() == PROXY                                 # explicit proxy still wins


def _direct(rig):
    return rig.r._do_direct_http_download(f"http://{HOST}/scene/1", f"http://{HOST}/f.mp4", str(Path(rig.tmp) / "f.mp4"))


CALL_SITES = CLIENTS + [("direct_download", _direct, "_open_stream")]


@pytest.mark.parametrize("exc", RESOLVER_FAILURES, ids=[e.__name__ for e in RESOLVER_FAILURES])
@pytest.mark.parametrize("name,drive,owner", CALL_SITES, ids=[c[0] for c in CALL_SITES])
def test_f1_no_call_site_builds_a_client_when_the_resolver_fails(rig, name, drive, owner, exc):
    _require_with_resolver(rig, _raises(exc))
    drive(rig)
    assert (rig.built, rig.sent) == ([], []), f"{name}/{exc.__name__}: built {rig.built}, sent {rig.sent}"


def test_f1_every_call_of_the_resolver_sits_in_a_try():
    # census from the filesystem: a refusal must be handled (or re-raised) where it is asked for
    root = Path(runner_transport.__file__).parent
    sites, bare = 0, []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for inner in node.body:
                    guarded.update(id(n) for n in ast.walk(inner))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "_download_proxy_url":
                sites += 1
                if id(node) not in guarded:
                    bare.append(f"{path.name}:{node.lineno}")
    assert sites >= 15, f"only {sites} call sites found -- the census is not seeing the tree"
    assert not bare, f"_download_proxy_url called outside any try: {bare}"


# ---------------------------------------------------------------- F3: the pool does not outrank the kill switch
POOL = ["http://pa.invalid:3128", "http://pb.invalid:3128", "http://pc.invalid:3128"]


def test_f3_a_pool_member_does_not_outrank_a_required_tunnel_that_is_down(rig):
    def killed(_sid):
        raise vpn_runtime.VPNRequiredError("kill switch tripped")

    _require_with_resolver(rig, killed)
    rig.r.config["proxy_pool"] = list(POOL)
    with pytest.raises(vpn_runtime.VPNRequiredError, match="kill switch"):
        rig.r._download_proxy_url()                    # was: POOL[0], resolver never asked


def test_f3_controls_the_pool_still_serves(rig):
    rig.r.config["proxy_pool"] = list(POOL)
    assert rig.r._download_proxy_url() == POOL[0]                           # not required
    _require_with_resolver(rig, lambda _s: "socks5://127.0.0.1:1081")
    assert rig.r._download_proxy_url() == POOL[1]                           # required, tunnel up: pool member


# ---------------------------------------------------------------- F15: unreadable config, fair rotation
@pytest.mark.parametrize("key,value", [
    ("proxy", 5), ("proxy", ["http://p.invalid:1"]), ("proxy_pool", [5]), ("proxy_pool", [b"http://p.invalid:1"]),
    ("proxy_pool", [{"url": "http://p.invalid:1"}]), ("proxy_pool", "http://p.invalid:1"),
    ("proxy_pool", ("http://p.invalid:1",)), ("proxy_pool", ["http://p.invalid:1", "  "]),
], ids=["proxy-int", "proxy-list", "pool-int", "pool-bytes", "pool-dict", "pool-str", "pool-tuple", "pool-blank"])
def test_f15_an_unreadable_proxy_setting_refuses(rig, key, value):
    rig.r.config[key] = value
    with pytest.raises(vpn_runtime.VPNRequiredError, match=key):
        rig.r._download_proxy_url()                    # was: the int/bytes handed out, a TypeError, or the pool ignored
    assert rig.r._site_client_egress("t") == (False, None)


@pytest.mark.parametrize("value", [None, [], ""], ids=["none", "empty-list", "empty-str"])
def test_f15_control_no_pool_is_no_pool(rig, value):
    rig.r.config["proxy_pool"] = value
    assert rig.r._download_proxy_url() is None


def test_f15_rotation_is_fair_while_one_member_flaps():
    from bulk_downloader import proxy_pool

    a, b, c = POOL
    state, picks = {}, []
    for i in range(12):
        # A flaps: down on every even pick, back on every odd one
        state.setdefault("health", {})[a] = {"fails": 0, "down_until": (10**12 if i % 2 == 0 else 0)}
        picks.append(proxy_pool.select_proxy(POOL, state, now=1000.0))
    counts = {u: picks.count(u) for u in POOL}
    assert counts[c] >= 4 and counts[b] >= 4, counts   # was: A 6, B 6, C 0


def test_f15_control_steady_rotation_is_round_robin():
    from bulk_downloader import proxy_pool

    state = {}
    assert [proxy_pool.select_proxy(POOL, state, now=1.0) for _ in range(6)] == POOL * 2


# ---------------------------------------------------------------- F2: the pool's health is fed, and never means "direct"
def test_f2_a_pool_member_that_cannot_be_reached_is_cooled_down(rig):
    rig.r.config["proxy_pool"] = POOL[:2]
    rig.dead_proxies.add(POOL[0])
    used = []
    for _ in range(8):
        del rig.built[:]
        try:
            _direct(rig)
        except Exception:
            pass
        used += [proxy for _who, proxy in rig.built]
    health = rig.r._proxy_pool_state["health"]
    assert health.get(POOL[0], {}).get("down_until", 0) > 0, health      # was: {} -- nothing ever recorded
    assert used[-2:] == [POOL[1], POOL[1]], used                          # was: the dead member kept its half
    assert used.count(POOL[0]) == 3, used                                 # three strikes, then rested


def test_f2_when_every_member_is_cooling_down_the_site_still_leaves_by_a_member(rig):
    from bulk_downloader import proxy_pool

    rig.r.config["proxy_pool"] = POOL[:2]
    rig.r._proxy_pool_state = {}
    for u, until in ((POOL[0], 9e12), (POOL[1], 8e12)):
        proxy_pool._ensure(rig.r._proxy_pool_state)["health"][u] = {"fails": 3, "down_until": until}
    assert rig.r._download_proxy_url() == POOL[1]                         # soonest back; was: None = direct


def test_f2_a_member_that_answers_again_gets_its_strikes_back(rig):
    from bulk_downloader import proxy_pool

    rig.r.config["proxy_pool"] = POOL[:1]
    rig.r._proxy_pool_state = {}
    proxy_pool.record_result(rig.r._proxy_pool_state, POOL[0], False)
    proxy_pool.record_result(rig.r._proxy_pool_state, POOL[0], False)
    assert rig.r._proxy_pool_state["health"][POOL[0]]["fails"] == 2
    _direct(rig)                                                          # the proxy answers (the origin says 404)
    assert rig.r._proxy_pool_state["health"][POOL[0]]["fails"] == 0


def test_f2_control_a_reachable_member_stays_in_rotation(rig):
    rig.r.config["proxy_pool"] = POOL[:2]
    for _ in range(6):
        _direct(rig)
    assert [p for _w, p in rig.built] == POOL[:2] * 3
    assert all(v.get("down_until", 0) == 0 for v in rig.r._proxy_pool_state["health"].values())


# ---------------------------------------------------------------- r2: the **kwargs clients cannot carry a proxy
def test_star_kwargs_clients_cannot_carry_a_proxy():
    """tests/test_row703 cannot see a proxy inside **kwargs and pins the three httpx.Client(**mapping) sites of
    runner_transport by line.  This reads each one: the mapping is a dict literal holding only "timeout", the one key
    ever added is "proxy", and it is popped (handed to guarded_transport) after the last add and before the client."""
    tree = ast.parse(Path(runner_transport.__file__).read_text(encoding="utf-8"))
    judged = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(ast.walk(func))
        for call in nodes:
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "Client"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "httpx"):
                continue
            for kw in call.keywords:
                if kw.arg is not None:
                    continue
                assert isinstance(kw.value, ast.Name), f"DAH-STAR-KWARGS-NOT-A-NAME line {call.lineno}"
                name, line = kw.value.id, call.lineno
                literals = [n for n in nodes if isinstance(n, ast.Assign) and n.lineno < line
                            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
                assert literals, f"DAH-STAR-KWARGS-NO-LITERAL {name} line {line}"
                made = max(literals, key=lambda n: n.lineno)
                assert isinstance(made.value, ast.Dict) and [
                    getattr(k, "value", None) for k in made.value.keys] == ["timeout"], (
                    f"DAH-STAR-KWARGS-MAPPING-NOT-TIMEOUT-ONLY {name} line {made.lineno}")
                adds = [n for n in nodes if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)
                        and isinstance(n.value, ast.Name) and n.value.id == name and made.lineno < n.lineno < line]
                assert all(getattr(n.slice, "value", None) == "proxy" for n in adds), (
                    f"DAH-STAR-KWARGS-OTHER-KEY-ADDED {name} before line {line}")
                methods = [(n.func.attr, n.lineno, [getattr(a, "value", None) for a in n.args]) for n in nodes
                           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                           and isinstance(n.func.value, ast.Name) and n.func.value.id == name
                           and made.lineno < n.lineno < line]
                assert methods and all(m[0] == "pop" and m[2][:1] == ["proxy"] for m in methods), (
                    f"DAH-STAR-KWARGS-PROXY-NOT-POPPED {name} before line {line}: {methods}")
                assert max(m[1] for m in methods) > max([n.lineno for n in adds] or [0]), (
                    f"DAH-STAR-KWARGS-PROXY-ADDED-AFTER-THE-POP {name} before line {line}")
                judged[line] = name
    assert len(judged) == 3, f"DAH-STAR-KWARGS-SITE-COUNT {sorted(judged.items())}"
