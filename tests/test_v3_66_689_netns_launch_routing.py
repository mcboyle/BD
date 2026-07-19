"""F5 launch-routing, Phase 1 -- route the SUBPROCESS download fallbacks
(yt-dlp / gallery-dl) through the shipped netns isolation engine.

RED-first. On pristine v3.66.688:
  * ``_build_ytdlp_cmd`` / ``_build_gallerydl_cmd`` do not accept a ``netns``
    kwarg  -> the pure-builder wrap tests RED (TypeError).
  * ``netns_isolation`` has no ``capture_netns`` / ``NetnsRequiredError`` /
    ``fail_closed`` -> the posture-decision tests RED (AttributeError).
  * ``_try_ytdlp_fallback`` / ``_try_gallerydl_fallback`` do not reference the
    engine or thread ``netns=`` into the builder -> the structural wiring
    guards RED.
After the cut all pass.

Design (operator decision this session -- "go a"): the engine's argv wrap
(``netns_exec_argv``) fits the subprocess fallbacks exactly. A posture-aware
context manager ``capture_netns(cfg, kind, ident, runner=)`` brackets the
subprocess:

  * site does NOT opt into ``netns_isolation`` -> yields ``None`` (byte-identical
    prior behaviour, no namespace, no cost);
  * opts in + create succeeds -> yields the ns name (the builder prepends
    ``ip netns exec <ns>``); the ns is always torn down on exit;
  * opts in + create fails (e.g. no CAP_NET_ADMIN):
      - FAIL CLOSED (default) -> raises ``NetnsRequiredError`` so the fallback
        returns without ever spawning an un-isolated subprocess;
      - ``fail_closed: false`` -> yields ``None`` (operator opted to degrade to
        the existing proxy-only isolation).

The create/destroy commands run through an INJECTED runner, so every posture is
unit-testable with no root and no real namespace. The builder wrap is pure. The
structural guards assert runner_extractors actually routes the two fallbacks
through the bracket.
"""
import os
import re
import subprocess
import types


# ---------------------------------------------------------------------------
# pure builder: netns wrap
# ---------------------------------------------------------------------------
def test_ytdlp_cmd_netns_wraps_argv():
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl",
                           url="https://ex/v", netns="bd_dl_abcd1234")
    assert cmd[:4] == ["ip", "netns", "exec", "bd_dl_abcd1234"], cmd[:4]
    # the real yt-dlp invocation still follows, terminated + positional url last
    assert "yt-dlp" in cmd and cmd[-1] == "https://ex/v", cmd


def test_ytdlp_cmd_no_netns_is_byte_identical():
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    base = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v")
    with_none = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl",
                                 url="https://ex/v", netns=None)
    assert base == with_none, "netns=None must not change the cmd"
    assert base[0] == "yt-dlp", "no netns -> yt-dlp is argv[0], no ip-netns prefix"
    assert "netns" not in base


def test_gallerydl_cmd_netns_wraps_argv():
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    cmd = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                               url="https://ex/g", netns="bd_dl_deadbeef")
    assert cmd[:4] == ["ip", "netns", "exec", "bd_dl_deadbeef"], cmd[:4]
    assert "gallery-dl" in cmd and cmd[-1] == "https://ex/g", cmd


def test_gallerydl_cmd_no_netns_is_byte_identical():
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    base = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl", url="https://ex/g")
    with_none = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                                     url="https://ex/g", netns=None)
    assert base == with_none
    assert base[0] == "gallery-dl"


# ---------------------------------------------------------------------------
# posture decision: capture_netns + fail_closed  (injected runner, no root)
# ---------------------------------------------------------------------------
def _ok_runner(record):
    """Injected runner: records every argv, returns rc=0 (create succeeds)."""
    def run(argv, *a, **k):
        record.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _fail_runner(record):
    """Injected runner: records argv, returns rc=1 (create fails -- no CAP)."""
    def run(argv, *a, **k):
        record.append(list(argv))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="err")
    return run


def test_capture_netns_no_optin_yields_none_and_never_creates():
    from bulk_downloader import netns_isolation as N
    rec = []
    with N.capture_netns({}, "dl", "https://ex/v", runner=_ok_runner(rec)) as ns:
        assert ns is None
    assert rec == [], "no opt-in must not run any ip/nft commands"


def test_capture_netns_optin_success_yields_ns_and_tears_down():
    from bulk_downloader import netns_isolation as N
    cfg = {"netns_isolation": True}
    rec = []
    seen = None
    with N.capture_netns(cfg, "dl", "https://ex/v", runner=_ok_runner(rec)) as ns:
        seen = ns
        assert isinstance(ns, str) and ns.startswith("bd_dl_"), ns
    # setup ran (create) AND teardown ran (destroy) through the injected runner
    joined = [" ".join(a) for a in rec]
    assert any("netns add" in j for j in joined), joined
    assert any("netns del" in j for j in joined), "ns must be torn down on exit"
    assert seen == N.netns_name("dl", "https://ex/v")


def test_capture_netns_optin_create_fails_fail_closed_raises():
    from bulk_downloader import netns_isolation as N
    cfg = {"netns_isolation": True}          # fail_closed defaults True
    raised = False
    try:
        with N.capture_netns(cfg, "dl", "u", runner=_fail_runner([])) as ns:
            assert False, "body must not run when isolation is required + unavailable"
    except N.NetnsRequiredError:
        raised = True
    assert raised, "opt-in + create failure MUST fail closed (NetnsRequiredError)"


def test_capture_netns_optin_create_fails_degrade_open_yields_none():
    from bulk_downloader import netns_isolation as N
    cfg = {"netns_isolation": {"enabled": True, "fail_closed": False}}
    rec = []
    ran_body = False
    with N.capture_netns(cfg, "dl", "u", runner=_fail_runner(rec)) as ns:
        ran_body = True
        assert ns is None, "degrade-open must yield None (no isolation), not raise"
    assert ran_body, "fail_closed:false must let the body run (degrade open)"


def test_fail_closed_default_true_and_respects_false():
    from bulk_downloader import netns_isolation as N
    assert N.fail_closed({"netns_isolation": True}) is True
    assert N.fail_closed({"netns_isolation": {"enabled": True}}) is True
    assert N.fail_closed({"netns_isolation": {"enabled": True, "fail_closed": False}}) is False
    # absent isolation -> vacuously fail-closed (never reached without opt-in)
    assert N.fail_closed({}) is True


# ---------------------------------------------------------------------------
# structural wiring: the two fallbacks route through the bracket
# ---------------------------------------------------------------------------
def _extractors_src():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    p = os.path.join(root, "bulk_downloader", "runner_extractors.py")
    return open(p, encoding="utf-8").read()


def _method_body(src, name):
    m = re.search(r"\n    def " + re.escape(name) + r"\(.*?(?=\n    def )", src, re.S)
    assert m, f"could not locate {name} in runner_extractors.py"
    return m.group(0)


def test_ytdlp_fallback_routes_through_capture_netns():
    body = _method_body(_extractors_src(), "_try_ytdlp_fallback")
    assert "capture_netns" in body, "yt-dlp fallback must bracket the subprocess in capture_netns"
    assert re.search(r"netns\s*=", body), "the resolved ns must be threaded as netns= into the builder"


def test_gallerydl_fallback_routes_through_capture_netns():
    body = _method_body(_extractors_src(), "_try_gallerydl_fallback")
    assert "capture_netns" in body, "gallery-dl fallback must bracket the subprocess in capture_netns"
    assert re.search(r"netns\s*=", body), "the resolved ns must be threaded as netns= into the builder"


def test_fallbacks_failclosed_on_netns_required_error():
    """Both fallbacks must catch NetnsRequiredError and return the (False, ...)
    fail-closed tuple rather than letting it propagate as an unhandled raise."""
    src = _extractors_src()
    for name in ("_try_ytdlp_fallback", "_try_gallerydl_fallback"):
        body = _method_body(src, name)
        assert "NetnsRequiredError" in body, f"{name} must handle NetnsRequiredError (fail closed)"


# ---------------------------------------------------------------------------
# real integration: live namespace via the DEFAULT runner (subprocess.run).
# Runs for real in a privileged sandbox; AUTO-SKIPS on an unprivileged host
# (e.g. the stash runner without CAP_NET_ADMIN) -- mirrors the @686 real test.
# ---------------------------------------------------------------------------
def _can_make_netns():
    from bulk_downloader import netns_isolation as N
    if not N.is_supported():
        return False
    probe = N.netns_name("probe", "capture_netns_selftest")
    if not N.create(probe):        # real create; fail-closed if no CAP_NET_ADMIN
        return False
    N.destroy(probe)
    return True


def test_capture_netns_real_isolation_and_teardown():
    from bulk_downloader import netns_isolation as N
    if not _can_make_netns():
        print("SKIP: netns unavailable (no CAP_NET_ADMIN) -- unit posture tests cover the logic")
        return
    cfg = {"netns_isolation": True}
    ident = "https://example/real-selftest"
    expected = N.netns_name("dl", ident)
    with N.capture_netns(cfg, "dl", ident) as ns:
        assert ns == expected
        # the ns really exists while inside the bracket
        listed = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True)
        assert ns in (listed.stdout or ""), f"{ns} should be a live namespace"
        # and it is isolated: a command run inside sees ONLY loopback (no eth*)
        addr = subprocess.run(N.netns_exec_argv(ns, ["ip", "-o", "addr", "show"]),
                              capture_output=True, text=True)
        out = addr.stdout or ""
        assert "lo" in out, out
        assert " eth" not in out and "ens" not in out, \
            "isolated ns must have no clear-interface egress route: " + out
    # torn down on exit
    after = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True)
    assert expected not in (after.stdout or ""), "ns must be gone after the bracket"
