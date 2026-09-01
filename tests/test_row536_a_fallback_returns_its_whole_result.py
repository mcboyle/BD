"""Row 536: a fallback that returns a short tuple kills the operator flow.

`runner_challenge._handle_captcha_check` chains two download fallbacks and
unpacks FIVE values from each::

    runner_challenge.py:68   ok, msg, fn, sz, fetched = self._try_ytdlp_fallback(...)
    runner_challenge.py:77   ok, msg, fn, sz, fetched = self._try_gallerydl_fallback(...)

Measured at base 511913b2 by AST over `bulk_downloader/runner_extractors.py`:

    _try_ytdlp_fallback      10 returns -- 8 four-tuples, 2 five-tuples
    _try_gallerydl_fallback  11 returns -- 6 four-tuples, 5 five-tuples

The shipped live configuration has ``use_ytdlp_fallback`` unset and
``captcha_api_key`` empty, so the auto-solver declines, the very first
statement of the ytdlp fallback returns its four-tuple "ytdlp_fallback
disabled", and the unpack raises ``ValueError: not enough values to unpack
(expected 5, got 4)``. The job is recorded as ``failed: worker error: ...``
and the entire needs_review + screenshot + captcha_type + "Take over to solve
it manually" flow never runs.

WHY THIS FILE FIXES BOTH METHODS AND NOT ONLY THE ONE THE ROW NAMES. Repairing
`_try_ytdlp_fallback` alone moves the ValueError from line 68 to line 77 and
changes nothing an operator can see: ``use_gallerydl_fallback`` is unset too,
so its first return is a four-tuple by the same defect. Row 536's consequence
is a property of the CHAIN, and no test can drive it green through the real
seam while either link is short. One coherent safety contract: every return
path of either fallback carries its whole result.

The fifth element is bytes ACTUALLY FETCHED, which is not the same number as
the file size -- yt-dlp's "has already been downloaded" outcome moved zero
bytes over a file that exists and has a size. Two of the tests below pin that
distinction so the arity cannot be satisfied by a constant.
"""
from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bulk_downloader import runner_challenge as rc
from bulk_downloader import runner_extractors as rx
from bulk_downloader import ytdlp_updater
from bulk_downloader.constants import CAPTCHA_SELECTORS

BD_GATE_SCOPE = "module"

_EXTRACTORS_PY = Path(rx.__file__)

# The DOM an hCaptcha challenge presents. Deliberately a SET of real selector
# strings rather than "match everything": a page that answers yes to every
# query would satisfy _has_captcha and detect_captcha_type for the wrong
# reason, and would classify as "turnstile" (the first signature tried).
_HCAPTCHA_DOM = {
    "iframe[src*='hcaptcha.com/captcha']",   # CAPTCHA_SELECTORS[0]
    "iframe[src*='hcaptcha.com']",           # _TYPE_SIGNATURES hcaptcha[0]
    ".h-captcha iframe",
}


class _Locator:
    def __init__(self, present):
        self._present = present

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._present else 0

    def is_visible(self, timeout=None):
        return self._present


class _CaptchaPage:
    """A page showing an hCaptcha widget. Records every selector queried so a
    test can prove the seam was actually walked rather than short-circuited."""

    url = "https://example.invalid/scene/1"

    def __init__(self):
        self.queried = []

    def locator(self, sel):
        self.queried.append(sel)
        return _Locator(sel in _HCAPTCHA_DOM)


class _Runner(rc.ChallengeMixin, rx.ExtractorsMixin):
    """The two mixins the real SiteRunner composes, over a recording surface.

    Nothing on the captcha path is stubbed: _has_captcha, _try_captcha_solve,
    _try_ytdlp_fallback and _try_gallerydl_fallback are the production bodies.
    Only the runner's I/O collaborators (_update_job, _screenshot, log_event)
    are recorders, and the two fallback entries are counted so a test can
    assert BOTH unpack sites were reached.
    """

    def __init__(self, config, site_id="row536-site"):
        self.config = config
        self.site_id = site_id
        self._captcha_stats = {"submitted": 0, "failed": 0, "timeouts": 0}
        self.jobs = []
        self.events = []
        self.shots = 0
        self.fallback_calls = []

    # -- collaborators -------------------------------------------------
    def _update_job(self, url, status, message, **kw):
        self.jobs.append({"url": url, "status": status, "message": message, **kw})

    def _screenshot(self, page, url):
        self.shots += 1
        return f"/shots/{self.site_id}-{self.shots}.png"

    def log_event(self, *args, **kw):
        self.events.append((args, kw))

    def _download_proxy_url(self):
        return None

    # -- counted passthroughs to the real fallbacks --------------------
    def _try_ytdlp_fallback(self, url, fail_reason=""):
        self.fallback_calls.append("ytdlp")
        return rx.ExtractorsMixin._try_ytdlp_fallback(self, url, fail_reason)

    def _try_gallerydl_fallback(self, url, fail_reason=""):
        self.fallback_calls.append("gallerydl")
        return rx.ExtractorsMixin._try_gallerydl_fallback(self, url, fail_reason)


def _shipped_config(**over):
    """The sole configured site's shape for this defect: both fallbacks off,
    no captcha API key. Overrides are explicit so a test that departs from the
    shipped shape says so."""
    cfg = {"name": "row536", "captcha_api_key": ""}
    cfg.update(over)
    return cfg


@pytest.fixture()
def db_rows(monkeypatch):
    """Capture what runner_challenge writes to history. db_log and
    history_title_kwargs are module-level imports in runner_challenge, so they
    are patched in THAT namespace, which is the name the code actually calls."""
    rows = []

    def _db_log(site_id, name, url, status, filename, size, msg, *a, **kw):
        rows.append({"site_id": site_id, "name": name, "url": url,
                     "status": status, "filename": filename, "size": size,
                     "msg": msg, "extra_pos": a, **kw})

    monkeypatch.setattr(rc, "db_log", _db_log)
    monkeypatch.setattr(rc, "history_title_kwargs", lambda *_a, **_k: {})
    return rows


# ── Preconditions ────────────────────────────────────────────────────

def test_preconditions_shipped_config_declines_every_solver(db_rows):
    """Assert the shape this defect needs BEFORE any verdict depends on it.

    Without this, a green needs_review could come from a page with no captcha,
    a solver that succeeded, or a fallback that was never consulted.
    """
    page = _CaptchaPage()
    runner = _Runner(_shipped_config())

    # The shipped flags, read the way production reads them.
    assert runner.config.get("use_ytdlp_fallback", False) is False
    assert runner.config.get("use_gallerydl_fallback", False) is False
    assert (runner.config.get("captcha_api_key") or "").strip() == ""

    # Nonzero seam: the fixture page really does trip the production detector,
    # and classifies as hcaptcha rather than the first-tried turnstile.
    assert runner._has_captcha(page) is True
    assert len(page.queried) > 0
    from bulk_downloader import captcha_resolver as cr
    assert cr.detect_captcha_type(page) == "hcaptcha"

    # The auto-solver declines on the empty key without touching the network.
    assert runner._try_captcha_solve(page) is False
    assert runner._captcha_stats["submitted"] == 0

    # And the first return of each fallback is the disabled arm.
    ytdlp = rx.ExtractorsMixin._try_ytdlp_fallback(runner, page.url)
    gdl = rx.ExtractorsMixin._try_gallerydl_fallback(runner, page.url)
    assert ytdlp[0] is False and ytdlp[1] == "ytdlp_fallback disabled"
    assert gdl[0] is False and gdl[1] == "gallerydl_fallback disabled"


# ── RED: the operator-visible consequence ────────────────────────────

def test_unsolvable_captcha_reaches_needs_review_in_the_shipped_config(db_rows):
    """RED at base 511913b2 with:

        ValueError: not enough values to unpack (expected 5, got 4)

    raised at runner_challenge.py:68. This is the whole of row 536: the job
    never reaches needs_review, the screenshot is never taken, captcha_type
    never reaches the queue UI, and the operator is never told to take over.
    """
    page = _CaptchaPage()
    runner = _Runner(_shipped_config())

    handled = runner._handle_captcha_check(page, page.url)

    # Both unpack sites were traversed -- the ytdlp link alone is not the seam.
    assert runner.fallback_calls == ["ytdlp", "gallerydl"]

    # The caller is told the URL was handled.
    assert handled is False

    # The operator flow ran, in full.
    assert len(runner.jobs) == 1, runner.jobs
    job = runner.jobs[0]
    assert job["status"] == "needs_review"
    assert job["captcha_type"] == "hcaptcha"
    assert job["screenshot"] == "/shots/row536-site-1.png"
    assert runner.shots == 1
    assert "Take over to solve it manually" in job["message"]
    assert "hcaptcha" in job["message"]

    # And history records it as needs_review, not as a worker error.
    assert len(db_rows) == 1, db_rows
    assert db_rows[0]["status"] == "needs_review"
    assert db_rows[0]["msg"] == "captcha challenge: hcaptcha"
    assert runner._captcha_encounters and len(runner._captcha_encounters) == 1


# ── Negative controls: the fifth element is a measurement ─────────────

def _no_netns():
    @contextlib.contextmanager
    def _cm(*_a, **_kw):
        yield None
    return _cm


def _arm_ytdlp(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda: ["/bin/true"])
    monkeypatch.setattr(rx.netns_isolation, "capture_netns", _no_netns())
    monkeypatch.setattr(
        rx.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""))


def test_a_real_transfer_reports_the_bytes_it_moved(monkeypatch, tmp_path, db_rows):
    """Negative control, direction 1: the guard was not satisfied by a
    hardcoded 0. A genuine yt-dlp download must report its byte count."""
    media = tmp_path / "scene.mp4"
    media.write_bytes(b"\x00" * 4242)
    assert media.stat().st_size == 4242

    _arm_ytdlp(monkeypatch, f"[download] Destination: {media}\n")
    runner = _Runner(_shipped_config(use_ytdlp_fallback=True,
                                     download_dir=str(tmp_path)))
    page = _CaptchaPage()

    assert runner._handle_captcha_check(page, page.url) is False
    assert runner.fallback_calls == ["ytdlp"]          # gallery-dl not reached
    assert runner.shots == 0                            # no needs_review path

    assert len(runner.jobs) == 1 and runner.jobs[0]["status"] == "done"
    assert runner.jobs[0]["file_size"] == 4242
    assert runner.jobs[0]["filename"] == str(media)
    assert len(db_rows) == 1
    assert db_rows[0]["status"] == "done"
    assert db_rows[0]["size"] == 4242
    assert db_rows[0]["bytes_fetched"] == 4242
    assert db_rows[0]["msg"] == "Downloaded via yt-dlp fallback"


def test_an_already_present_file_reports_zero_bytes_fetched(monkeypatch, tmp_path, db_rows):
    """Negative control, direction 2, and the discriminator that matters.

    Padding every return with `size` would pass the test above and be just as
    wrong: yt-dlp's "has already been downloaded" outcome moved NOTHING over a
    file that exists and has a nonzero size. file_size and bytes_fetched must
    disagree here, which no constant fifth element can do.
    """
    media = tmp_path / "already.mp4"
    media.write_bytes(b"\x00" * 777)

    _arm_ytdlp(monkeypatch, f"[download] {media} has already been downloaded\n")
    runner = _Runner(_shipped_config(use_ytdlp_fallback=True,
                                     download_dir=str(tmp_path)))
    page = _CaptchaPage()

    assert runner._handle_captcha_check(page, page.url) is False
    assert len(db_rows) == 1
    assert db_rows[0]["status"] == "done"
    assert db_rows[0]["size"] == 777           # the file is there, and sized
    assert db_rows[0]["bytes_fetched"] == 0    # and BD moved none of it
    assert db_rows[0]["msg"] == "Already present (yt-dlp reported no download)"


def test_a_failed_subprocess_still_returns_its_whole_result(monkeypatch, tmp_path, db_rows):
    """An enabled fallback whose subprocess exits nonzero is a DIFFERENT return
    path from the disabled arm, and it is on the operator's route to
    needs_review. It must not die at the unpack either."""
    _arm_ytdlp(monkeypatch, "", returncode=1)
    monkeypatch.setattr(rx.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(
                            returncode=1, stdout="", stderr="ERROR: unsupported URL"))
    runner = _Runner(_shipped_config(use_ytdlp_fallback=True,
                                     download_dir=str(tmp_path)))
    page = _CaptchaPage()

    assert runner._handle_captcha_check(page, page.url) is False
    assert runner.fallback_calls == ["ytdlp", "gallerydl"]
    assert runner.jobs[0]["status"] == "needs_review"
    assert db_rows[0]["status"] == "needs_review"


# ── Structural gate over the return paths no runtime test reaches ────

def _returns(method_name):
    tree = ast.parse(_EXTRACTORS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ExtractorsMixin":
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == method_name:
                    return [r for r in ast.walk(fn) if isinstance(r, ast.Return)]
    raise AssertionError(f"{method_name} not found in ExtractorsMixin")


@pytest.mark.parametrize(
    "method,floor",
    [("_try_ytdlp_fallback", 10), ("_try_gallerydl_fallback", 11)],
)
def test_every_return_path_carries_the_whole_result(method, floor):
    """Most of these paths need a broken PATH, an unwritable directory or a
    downed VPN tunnel to reach at runtime. The chained unpack in
    runner_challenge is unconditional, so arity is a static property of the
    method and can be proved statically for all of them.

    The floor is the measured return count at base 511913b2. A refactor that
    deletes return paths must fail here rather than silently shrink the
    denominator this gate surveys.
    """
    returns = _returns(method)
    assert len(returns) >= floor, (
        f"{method} has {len(returns)} returns, fewer than the {floor} measured "
        "at base -- the gate's denominator shrank")
    short = [(r.lineno, len(r.value.elts) if isinstance(r.value, ast.Tuple) else None)
             for r in returns
             if not (isinstance(r.value, ast.Tuple) and len(r.value.elts) == 5)]
    assert short == [], (
        f"{method} returns a short/non-tuple result at {short}; "
        "runner_challenge unpacks five values from every one of them")


def test_the_documented_contract_names_five_values():
    """A docstring that still promises four values is how the next caller
    reintroduces this defect."""
    src = _EXTRACTORS_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    docs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ExtractorsMixin":
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name.endswith("_fallback"):
                    docs[fn.name] = ast.get_docstring(fn) or ""
    assert "_try_ytdlp_fallback" in docs and "_try_gallerydl_fallback" in docs
    doc = docs["_try_ytdlp_fallback"]
    assert "bytes_fetched" in doc, doc[-400:]
    assert "(ok: bool, message: str, filename: str|None, size: int)" not in doc


def test_the_unpack_site_still_wants_five(db_rows):
    """The gate above is only meaningful while runner_challenge unpacks five.
    If a future edit changes the caller to four, this file must be revisited
    rather than quietly guarding an arity nobody needs."""
    src = Path(rc.__file__).read_text(encoding="utf-8")
    sites = [ln.strip() for ln in src.splitlines()
             if "_fallback(url," in ln and "=" in ln]
    assert len(sites) == 2, sites
    for line in sites:
        targets = line.split("=", 1)[0]
        assert len([t for t in targets.split(",") if t.strip()]) == 5, line


def test_captcha_selector_population_is_nonzero():
    """The fixture page is built from real selector strings; an empty
    CAPTCHA_SELECTORS would make _has_captcha vacuously False and every
    verdict above unreachable."""
    assert len(CAPTCHA_SELECTORS) > 0
    assert _HCAPTCHA_DOM & set(CAPTCHA_SELECTORS)
