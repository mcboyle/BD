"""fx-adulttime-grant-exists: a bare-leaf download whose name is already taken
must finish, not die in the probed-tier rename.

Live, test3 2026-09-30 03:36Z (results/test3/adulttime.md): adulttime's
window.open grant named the file by its route leaf ``mp4``, the website title
renamed it "The Meat Professor - Episode 1 [1080p].mp4" -- a name an earlier
download of the same scene (00:13Z) already held -- so the staging reservation
landed the 828 MB transfer as "..._1.mp4". The probed-tier reconcile then
compared the probed name with that ``_1`` path, saw a different, existing name,
and called ``safe_dest(str(new_dest))``; ``safe_dest`` calls ``path.exists()``:

    [BD-GEN-000] 'str' object has no attribute 'exists'

The row failed after the file was complete and the retry fetched it again.

Two defects, one path:
  * the call site stringified an absolute Path before ``detect.safe_dest``,
    which takes a Path by design (test_v3_66_840_extractor_dest_resolution
    pins why a str must not be coerced there).
  * the reconcile treated the reservation's collision suffix as a different
    name, so even without the crash it would rename ``_1`` to ``_2``.

The grant fixture is row 722's (``test_row722_relative_download_href_provenance``):
no browser, no live site; the probe height is stubbed like tpl95-cumlouder-2.
"""

from __future__ import annotations

import ast
import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader import detect
from bulk_downloader import runner_transport as transport

BD_GATE_SCOPE = "module"

PAGE_URL = "https://members.example/en/video/partneranimation/The-Meat-Professor/289575"
GRANT = "/movieaction/download/289575/1080p/mp4?codec=h264&impressionUUID=u1"
TAKEN = "289575 [1080p].mp4"  # the scene-url name for this grant
NEW_BYTES = b"new-transfer" * 512
OLD_BYTES = b"earlier-download-of-this-scene" * 64


class _ParentPage:
    url = PAGE_URL

    def __init__(self):
        self.open_intercepted = False
        self.grant_url = None

    def evaluate(self, script):
        if script == transport._POPUP_GRANT_ARM:
            self.open_intercepted = True
            self.grant_url = None
            return None
        if script == transport._POPUP_GRANT_READ:
            return self.grant_url
        if script == transport._POPUP_GRANT_DISARM:
            self.open_intercepted = False
            self.grant_url = None
            return None
        raise AssertionError(f"unexpected page script: {script!r}")

    @contextmanager
    def expect_download(self, *, timeout):
        yield None
        raise PWTimeout("no download event on the parent page")

    def title(self):
        return "The Meat Professor - Episode 1"


class _QualityRow:
    def __init__(self, page):
        self.page = page

    def get_attribute(self, name):
        assert name == "href"

    def click(self):
        assert self.page.open_intercepted
        self.page.grant_url = GRANT


class _Runner(transport.TransportMixin):
    def __init__(self, probed_height):
        self.site_id = "adulttime"
        self.config = {
            "name": "adulttime",
            "use_http_dl": True,
            "verify_hash": False,
            "verify_integrity": False,
            "filename_template": "{filename}{ext}",
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("fx-adulttime-grant-exists")
        self.probed_height = probed_height
        self.fetched_to = []
        self.failures = []
        self.status = []

    def _http_download(self, _page_url, _page, _ctx, _file_url, final_path):
        Path(final_path).write_bytes(NEW_BYTES)
        self.fetched_to.append(Path(final_path).name)
        return (len(NEW_BYTES), len(NEW_BYTES))

    def _pw_save(self, dl, final_path):  # pragma: no cover - must not be used
        raise AssertionError("no download event exists to save")

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _probe_video_height(self, _path):
        return self.probed_height

    def _embed_metadata_if_mp4(self, *_a, **_kw):
        return None

    def _screenshot(self, *_args, **_kwargs):
        return ""

    def _update_job(self, _url, state, message="", **_kwargs):
        self.status.append((state, message))

    def _handle_failure(self, url, message):
        self.failures.append((url, message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def _drive(tmp_path, monkeypatch, probed_height, taken=(TAKEN,)):
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    work = tmp_path / "bdhome"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    dl_dir = tmp_path / "adulttime"
    dl_dir.mkdir()
    for name in taken:
        (dl_dir / name).write_bytes(OLD_BYTES)
    page = _ParentPage()
    runner = _Runner(probed_height)
    runner._do_download(
        page,
        object(),
        page.url,
        {
            "locator": _QualityRow(page),
            "score": 1080,
            "size": 0,
            "text": "Full HD 1080p",
            "_all_candidates": [],
        },
        dl_dir,
        "1080p",
    )
    return runner, dl_dir


def _str_wrapped_safe_dest_calls():
    """Every ``safe_dest(str(...))`` in the package: file:line."""
    pkg = Path(detect.__file__).resolve().parent
    hits = []
    for src in sorted(pkg.rglob("*.py")):
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and n.args):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            arg = n.args[0]
            if (
                name == "safe_dest"
                and isinstance(arg, ast.Call)
                and getattr(arg.func, "id", None) == "str"
            ):
                hits.append(f"{src.relative_to(pkg.parent)}:{n.lineno}")
    return hits


def test_no_caller_hands_safe_dest_a_str():
    """safe_dest takes a Path by design (test_v3_66_840_extractor_dest_resolution:
    coercing a str would resolve a bare name against CWD). A caller that
    stringifies an absolute Path first crashes it -- the live defect."""
    hits = _str_wrapped_safe_dest_calls()
    assert hits == [], f"FX-GRANT-EXISTS: safe_dest(str(...)) at {hits}"


def test_the_probe_finds_a_str_wrapped_call(tmp_path, monkeypatch):
    """Positive control: the scan reports a planted safe_dest(str(p))."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "detect.py").write_text("")
    (pkg / "m.py").write_text("from .detect import safe_dest\nx = safe_dest(str(p))\n")
    monkeypatch.setattr(detect, "__file__", str(pkg / "detect.py"))
    assert _str_wrapped_safe_dest_calls() == ["pkg/m.py:2"]


def test_a_taken_name_does_not_fail_the_row_after_the_file_landed(
    tmp_path, monkeypatch
):
    """RED at 521a2f08: AttributeError 'str' object has no attribute 'exists'."""
    runner, dl_dir = _drive(tmp_path, monkeypatch, probed_height=1080)
    done = [m for s, m in runner.status if s == "done"]
    assert done, (
        f"FX-GRANT-EXISTS: the row did not finish; status {runner.status}, "
        f"failures {runner.failures}"
    )
    assert runner.failures == []
    assert (dl_dir / TAKEN).read_bytes() == OLD_BYTES, "the earlier file was touched"


def test_the_collision_suffix_is_not_renamed_again(tmp_path, monkeypatch):
    """The probed tier matches the chosen name: the reserved '_1' stays '_1'."""
    runner, dl_dir = _drive(tmp_path, monkeypatch, probed_height=1080)
    assert runner.fetched_to == ["289575 [1080p]_1.mp4"]
    assert sorted(p.name for p in dl_dir.iterdir()) == [
        "289575 [1080p].mp4",
        "289575 [1080p]_1.mp4",
    ], f"FX-GRANT-EXISTS: landed {sorted(p.name for p in dl_dir.iterdir())}"
    assert (dl_dir / "289575 [1080p]_1.mp4").read_bytes() == NEW_BYTES


def test_control_a_different_probed_tier_is_still_renamed(tmp_path, monkeypatch):
    """The reconcile still runs when the probe disagrees with the chosen tier."""
    runner, dl_dir = _drive(tmp_path, monkeypatch, probed_height=720)
    assert (dl_dir / "289575 [720p].mp4").read_bytes() == NEW_BYTES, (
        f"FX-GRANT-EXISTS: probed 720p not renamed: {sorted(p.name for p in dl_dir.iterdir())}"
    )
    assert (dl_dir / TAKEN).read_bytes() == OLD_BYTES
    assert [m for s, m in runner.status if s == "done"]


def test_a_taken_probed_name_gets_its_own_suffix(tmp_path, monkeypatch):
    """The probed name is ALSO taken: safe_dest suffixes it, nothing is overwritten."""
    runner, dl_dir = _drive(
        tmp_path, monkeypatch, probed_height=720, taken=(TAKEN, "289575 [720p].mp4")
    )
    assert [m for s, m in runner.status if s == "done"], (
        f"FX-GRANT-EXISTS: status {runner.status}, failures {runner.failures}"
    )
    assert (dl_dir / "289575 [720p]_1.mp4").read_bytes() == NEW_BYTES, (
        f"FX-GRANT-EXISTS: landed {sorted(p.name for p in dl_dir.iterdir())}"
    )
    assert (dl_dir / "289575 [720p].mp4").read_bytes() == OLD_BYTES
    assert (dl_dir / TAKEN).read_bytes() == OLD_BYTES
