"""F-CBD03-01 -- aiassist Ollama LAN-only gate is DNS-rebind-vulnerable.

validate_endpoint resolves + checks the endpoint host is LAN at SAVE time (and
trusts .local/.lan suffixes with no lookup at all), but the request path
(_post_json -> urlopen) re-resolves at connect time. A rebind DNS returns a LAN
IP at save and a public/internal IP at request, defeating the "LAN-only" gate.
The fix pins the resolved LAN IP at request time: re-resolve NOW, refuse unless
EVERY address is LAN/loopback, and connect to a validated IP (Host header
preserved). Pure-logic testable by faking getaddrinfo -- no network.
"""
import os
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import bulk_downloader.aiassist as ai


def _fake_getaddrinfo(ip):
    return lambda *a, **k: [(2, 1, 6, "", (ip, 0))]


def test_pin_refuses_public_rebind():
    orig = ai.socket.getaddrinfo
    ai.socket.getaddrinfo = _fake_getaddrinfo("8.8.8.8")  # public
    try:
        raised = False
        try:
            ai._pin_lan_endpoint("http://ollama.example:11434/api/generate")
        except RuntimeError:
            raised = True
        assert raised, "endpoint resolving to a public IP must be refused (rebind defense)"
    finally:
        ai.socket.getaddrinfo = orig


def test_pin_allows_and_pins_lan():
    orig = ai.socket.getaddrinfo
    ai.socket.getaddrinfo = _fake_getaddrinfo("192.168.1.9")  # LAN
    try:
        pinned, host_hdr = ai._pin_lan_endpoint("http://ollama.lan:11434/api/generate")
        assert "192.168.1.9" in pinned, f"must pin the validated LAN IP: {pinned}"
        assert host_hdr and "ollama.lan" in host_hdr, f"Host header must be preserved: {host_hdr}"
    finally:
        ai.socket.getaddrinfo = orig


def test_post_json_blocks_public_rebind_before_network():
    orig = ai.socket.getaddrinfo
    ai._config["endpoint"] = "http://ollama.example:11434"
    ai.socket.getaddrinfo = _fake_getaddrinfo("8.8.8.8")  # public
    try:
        raised = ""
        try:
            ai._post_json("/api/generate", {"x": 1}, timeout=1)
        except RuntimeError as e:
            raised = str(e).lower()
        assert ("public" in raised or "rebind" in raised or "refus" in raised), \
            f"_post_json must refuse a public-resolving endpoint via the LAN guard, not a network error: {raised!r}"
    finally:
        ai.socket.getaddrinfo = orig
        ai._config["endpoint"] = "http://localhost:11434"


if __name__ == "__main__":
    import traceback
    for n in ["test_pin_refuses_public_rebind", "test_pin_allows_and_pins_lan",
              "test_post_json_blocks_public_rebind_before_network"]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
