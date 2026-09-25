"""The SOCKS bridge must reject a malformed CONNECT target."""

import pytest

from bulk_downloader.download_egress import _connect_authority

BD_GATE_SCOPE = "module"


def test_bracketed_connect_authority_rejects_trailing_junk():
    assert _connect_authority("[::1]:8443") == ("::1", 8443)
    assert _connect_authority("[::1]") == ("::1", 443)  # lens (B1): bare bracket keeps the 443 default
    with pytest.raises(ValueError, match="malformed IPv6 CONNECT authority"):
        _connect_authority("[::1]wrong-target")
