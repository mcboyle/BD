from pathlib import Path


def test_frontend_links_phone_share_manifest():
    index = Path("frontend/index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/static/manifest.json"' in index


if __name__ == "__main__":
    test_frontend_links_phone_share_manifest()
