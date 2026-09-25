"""OpenVPN readiness is valid only while its process is alive."""

from bulk_downloader import vpn_config, vpn_openvpn
from bulk_downloader.vpn import Tunnel

BD_GATE_SCOPE = "module"


def test_ready_marker_cannot_mask_exited_process(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_openvpn, "_OVPN_BINARY", "/fake/openvpn")
    monkeypatch.setattr(vpn_openvpn, "CONF_DIR", tmp_path)
    monkeypatch.setattr(vpn_openvpn, "_ensure_conf_dir", lambda: None)
    monkeypatch.setattr(vpn_config, "resolve_secrets", lambda cfg: cfg)
    monkeypatch.setattr(vpn_openvpn, "_terminate", lambda _proc: None)

    class Process:
        stdout = None

        def __init__(self, exited):
            self.exited = exited

        def poll(self):
            return 1 if self.exited else None

    process = {"value": Process(False)}
    monkeypatch.setattr(vpn_openvpn.subprocess, "Popen", lambda *_a, **_kw: process["value"])

    failure = {"value": False}

    def ready_output(_proc, ready_event, failed_event, _log_ring, parsed):
        parsed["ip"] = "10.0.0.1"
        parsed["iface"] = "tun0"
        ready_event.set()
        if failure["value"]:
            failed_event.set()

    monkeypatch.setattr(vpn_openvpn, "_watch_openvpn_output", ready_output)

    class Thread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(vpn_openvpn.threading, "Thread", Thread)

    class Proxy:
        started = 0

        def __init__(self, **_kwargs):
            pass

        def start(self):
            Proxy.started += 1

        def is_alive(self):
            return True

    monkeypatch.setattr(vpn_openvpn, "SocksProxy", Proxy)

    def tunnel(tunnel_id):
        return Tunnel(tunnel_id=tunnel_id, name=tunnel_id, provider="generic",
                      backend="openvpn", config={"ovpn": "client\n"}, socks_port=12345)

    alive = tunnel("alive")
    failed = tunnel("failed")
    dead = tunnel("dead")
    try:
        assert vpn_openvpn.start(alive) is True
        assert vpn_openvpn.is_running(alive) is True

        failure["value"] = True
        assert vpn_openvpn.start(failed) is False
        failure["value"] = False
        process["value"] = Process(True)
        assert vpn_openvpn.start(dead) is False
        assert vpn_openvpn.is_running(dead) is False
        assert Proxy.started == 1

        # lens (B1): the process may die BETWEEN the readiness check and the
        # SOCKS proxy start; the post-proxy guard must stop the proxy it just
        # started and report failure, not hand back a dead tunnel.
        class LateDeath(Process):
            polls = 0

            def poll(self):
                LateDeath.polls += 1
                return None if LateDeath.polls <= 1 else 1

        class StoppableProxy(Proxy):
            stopped = 0

            def stop(self):
                StoppableProxy.stopped += 1

        monkeypatch.setattr(vpn_openvpn, "SocksProxy", StoppableProxy)
        process["value"] = LateDeath(False)
        late = tunnel("late")
        assert vpn_openvpn.start(late) is False
        assert vpn_openvpn.is_running(late) is False
        assert StoppableProxy.stopped == 1
    finally:
        with vpn_openvpn._handles_lock:
            vpn_openvpn._handles.pop("alive", None)
            vpn_openvpn._handles.pop("failed", None)
            vpn_openvpn._handles.pop("dead", None)
            vpn_openvpn._handles.pop("late", None)
