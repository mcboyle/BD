from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_links_phone_share_manifest():
    index = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/static/manifest.json"' in index


if __name__ == "__main__":
    test_frontend_links_phone_share_manifest()
