import pytest

from bulk_downloader import vpn_socks

BD_GATE_SCOPE = "module"


class FakeSocket:
    def __init__(self, fail):
        self.fail = fail
        self.closed = False

    def settimeout(self, timeout):
        pass

    def connect(self, address):
        if self.fail:
            raise self.fail

    def close(self):
        self.closed = True


def test_failed_outbound_connect_closes_socket(monkeypatch):
    made = []
    fail = [None, ConnectionRefusedError("refused"), TimeoutError("timed out")]

    def socket_factory(*args):
        sock = FakeSocket(fail.pop(0))
        made.append(sock)
        return sock

    monkeypatch.setattr(vpn_socks.socket, "socket", socket_factory)
    proxy = vpn_socks.SocksProxy("127.0.0.1", 0)

    success = proxy._connect_outbound("192.0.2.1", 443)
    assert success is made[0] and not success.closed

    with pytest.raises(ConnectionRefusedError, match="refused"):
        proxy._connect_outbound("192.0.2.2", 443)
    assert made[1].closed

    # lens (B1): a connect TIMEOUT (OSError subclass, not ConnectionRefusedError)
    # must close the socket too -- the common failure on a dead VPN peer.
    with pytest.raises(TimeoutError, match="timed out"):
        proxy._connect_outbound("192.0.2.3", 443)
    assert made[2].closed
