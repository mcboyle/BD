"""F-RUN02-02 + F-RUN02-03 -- fail-closed VPN egress for the two remaining
in-process httpx clients (Track-K follow-on).

Two clients bypassed the fail-closed VPN download proxy, so a ``vpn_required``
site whose tunnel is down/killed leaked the real IP:

  * F-RUN02-02: ``runner_transport._do_probe_fetch`` (GCW probe: samples the
    first bytes of the media over ``httpx.stream``).
  * F-RUN02-03: ``session_keeper._heartbeat_httpx_fallback`` (auth heartbeat
    via ``httpx.Client``).

Mirrors the Track-K ``_do_direct_http_download`` binding: resolve the effective
download proxy first, FAIL CLOSED on ``VPNRequiredError`` (never build an
unproxied client), and thread ``proxy=`` into the client. Structural source-body
assertions -- RED on pristine (no proxy=), GREEN after the cut -- plus the pure
decision is already covered by test_track_k_vpn_bind.effective_download_proxy_*.
"""
import os
import re
import glob


def _pkg():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "bulk_downloader")


def _runner_src():
    p = _pkg()
    files = [os.path.join(p, "runner.py")] + sorted(glob.glob(os.path.join(p, "runner_*.py")))
    return "\n".join(open(f, encoding="utf-8").read() for f in files)


def _sk_src():
    return open(os.path.join(_pkg(), "session_keeper.py"), encoding="utf-8").read()


def _body(src, name):
    """Isolate a method body from `def name(` up to the next same-indent def."""
    m = re.search(rf"def {re.escape(name)}\(.*?(?=\n    def )", src, re.S)
    assert m, f"could not locate {name}"
    return m.group(0)


# ---- F-RUN02-02: probe fetch (runner_transport) ---------------------------
def test_probe_fetch_resolves_download_proxy():
    body = _body(_runner_src(), "_do_probe_fetch")
    assert "_download_proxy_url(" in body, \
        "probe fetch must resolve the effective download proxy (self._download_proxy_url())"


def test_probe_fetch_fails_closed_on_vpn_required():
    body = _body(_runner_src(), "_do_probe_fetch")
    assert "VPNRequiredError" in body, \
        "probe fetch must handle VPNRequiredError (fail closed, not leak on clear net)"
    assert "needs_review" in body  # the probe's fail-closed verdict


def test_probe_fetch_stream_is_proxied():
    body = _body(_runner_src(), "_do_probe_fetch")
    streams = list(re.finditer(r"httpx\.stream\((.*?)\)", body, re.S))
    assert streams, "expected an httpx.stream in the probe body"
    for m in streams:
        assert "proxy=" in m.group(1), \
            "probe httpx.stream must pass proxy= (fail-closed VPN binding); found unproxied stream"


# ---- F-RUN02-03: heartbeat fallback (session_keeper) ----------------------
def test_heartbeat_resolves_download_proxy():
    body = _body(_sk_src(), "_heartbeat_httpx_fallback")
    assert "effective_download_proxy" in body, \
        "heartbeat must resolve the effective download proxy before building the client"


def test_heartbeat_fails_closed_on_vpn_required():
    body = _body(_sk_src(), "_heartbeat_httpx_fallback")
    assert "VPNRequiredError" in body, \
        "heartbeat must fail closed on VPNRequiredError instead of beating on the clear net"


def test_heartbeat_client_is_proxied():
    body = _body(_sk_src(), "_heartbeat_httpx_fallback")
    clients = list(re.finditer(r"httpx\.Client\((.*?)\)", body, re.S))
    assert clients, "expected an httpx.Client in the heartbeat body"
    for m in clients:
        assert "proxy=" in m.group(1), \
            "heartbeat httpx.Client must pass proxy= (fail-closed VPN binding); found unproxied client"


if __name__ == "__main__":
    import traceback
    names = [k for k in sorted(globals()) if k.startswith("test_")]
    for n in names:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
