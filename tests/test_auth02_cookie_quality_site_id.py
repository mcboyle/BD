"""RED-first repro for F-AUTH02-03.

``cookie_quality._load_cookies`` builds the fallback cookie path
``INSTALL_DIR/cookies/<site_id>.json`` without validating ``site_id``, so a
traversing id (``../secret``) resolves OUTSIDE the ``cookies/`` dir and is
handed to the cookie loader -> arbitrary-file read. After the fix an invalid
``site_id`` is rejected before the join, so the loader is never handed an
escaped path.

Pristine-source RED: the loader is called with a path outside ``cookies/``.
"""
import os


def test_traversing_site_id_cannot_escape_cookies_dir(tmp_path, monkeypatch):
    import bulk_downloader.constants as const
    monkeypatch.setattr(const, "INSTALL_DIR", str(tmp_path), raising=False)

    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    # the escaped target must exist so the pristine is_file() check passes
    (tmp_path / "secret.json").write_text("[]", encoding="utf-8")

    import bulk_downloader.cookies as ck
    seen = []
    monkeypatch.setattr(ck, "load_cookies_from_file",
                        lambda p: (seen.append(p), [])[1])

    from bulk_downloader import cookie_quality as cq
    cq._load_cookies("../secret", s_cfg_entry=None)

    cdir = os.path.realpath(str(cookies_dir))
    for p in seen:
        assert os.path.realpath(p).startswith(cdir), \
            f"cookie loader handed a path outside cookies/: {p}"


def test_valid_site_id_still_loads(tmp_path, monkeypatch):
    import bulk_downloader.constants as const
    monkeypatch.setattr(const, "INSTALL_DIR", str(tmp_path), raising=False)
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    (cookies_dir / "good_site.json").write_text("[]", encoding="utf-8")
    import bulk_downloader.cookies as ck
    seen = []
    monkeypatch.setattr(ck, "load_cookies_from_file",
                        lambda p: (seen.append(p), [])[1])
    from bulk_downloader import cookie_quality as cq
    cq._load_cookies("good_site", s_cfg_entry=None)
    assert any("good_site.json" in p for p in seen), seen
