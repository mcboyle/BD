BD_GATE_SCOPE = "module"

import sys
from unittest.mock import MagicMock

from bulk_downloader.extractors_dl8 import (
    predict_badoink_filenames,
    probe_badoink_candidates,
)


def test_default_probe_reaches_4k_after_higher_tiers_fail(monkeypatch):
    candidates = predict_badoink_filenames(
        "https://cdn.badoinkvr.com/SCENE_trailer.mp4"
    )
    assert len(candidates) >= 10
    assert candidates[8].tier == 2160

    fake_httpx = MagicMock()
    fake_httpx.RequestError = Exception
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    fake_httpx.Client.return_value = client
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    def probe(available):
        client.head.reset_mock()
        client.head.side_effect = lambda url, **_kw: MagicMock(
            status_code=200 if available in url else 404
        )
        return probe_badoink_candidates(candidates)

    assert probe("_5k_").tier == 2880
    assert probe("_4k_HEVC_").tier == 2160
    assert client.head.call_count == 9
