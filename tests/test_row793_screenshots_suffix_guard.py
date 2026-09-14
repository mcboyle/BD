"""Row 793: the screenshot route serves only PNG evidence files."""

BD_GATE_SCOPE = "module"

import pytest


@pytest.fixture
def evidence_client(tmp_path, monkeypatch):
    """The evidence root the route is pointed at, and a client that reads it.

    Round b (row793b) extends this dir with the three cases the correctness lens
    found unpinned: a double extension, an upper-case one, and a file that sits
    directly in the root rather than in a per-site subdir.
    """
    from bulk_downloader import app as bd_app

    evidence_root = tmp_path / "screenshots"
    nested = evidence_root / "site793"
    nested.mkdir(parents=True)
    (nested / "evidence.txt").write_text("not an image", encoding="utf-8")
    (nested / "evidence.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (nested / "secret.png.txt").write_text("not an image either", encoding="utf-8")
    (nested / "EVIDENCE.PNG").write_bytes(b"\x89PNG\r\n\x1a\n")
    (evidence_root / "in_root.txt").write_text("not an image, and not nested", encoding="utf-8")
    # The fixture's shape is asserted nonzero before any verdict: without these
    # the "is rejected" assertions below would pass against an empty dir.
    assert len(list(nested.iterdir())) == 4
    assert len(list(evidence_root.iterdir())) == 2  # site793/ and in_root.txt

    monkeypatch.setattr(bd_app, "SCREENSHOTS_DIR", evidence_root)
    return bd_app.app.test_client()


def test_screenshots_route_rejects_non_png_but_serves_nested_png(evidence_client):
    """A file inside the evidence root is not sufficient authority to serve it."""
    rejected = evidence_client.get("/screenshots/site793/evidence.txt")
    served = evidence_client.get("/screenshots/site793/evidence.png")

    assert rejected.status_code == 400, rejected.get_json()
    assert served.status_code == 200, served.get_data(as_text=False)
    assert served.mimetype == "image/png"


def test_double_extension_is_rejected_by_suffix_not_substring(evidence_client):
    """`secret.png.txt` ENDS in .txt; a guard that only looks for ".png"
    anywhere in the path serves it, which is the arbitrary-file-serve the row
    exists to close."""
    rejected = evidence_client.get("/screenshots/site793/secret.png.txt")

    assert rejected.status_code == 400, (
        "secret.png.txt was served: the guard matched '.png' as a SUBSTRING, not as the suffix",
        rejected.status_code,
        rejected.get_data(as_text=True)[:120],
    )


def test_upper_case_png_extension_is_still_served_as_an_image(evidence_client):
    """`EVIDENCE.PNG` is a PNG. Dropping the .lower() turns the guard into a
    denial of real evidence rather than a hole, so it is pinned separately."""
    served = evidence_client.get("/screenshots/site793/EVIDENCE.PNG")

    assert served.status_code == 200, (
        "EVIDENCE.PNG was refused: the suffix comparison is case-sensitive",
        served.status_code,
        served.get_data(as_text=True)[:120],
    )
    assert served.mimetype == "image/png"


def test_non_png_directly_in_the_evidence_root_is_rejected(evidence_client):
    """The boundary check above the guard has its own in-root branch; an in-root
    non-PNG must still be refused by the suffix guard, not waved through."""
    rejected = evidence_client.get("/screenshots/in_root.txt")

    assert rejected.status_code == 400, (
        "an in-root non-PNG was served: the guard is reached only for nested paths",
        rejected.status_code,
        rejected.get_data(as_text=True)[:120],
    )
