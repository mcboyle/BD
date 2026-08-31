"""Row 429: a 'done' row that produced NO FILE must not dedup the real download.

THE DEFECT (db.py, ``db_find_url_in_history``). The default-ON exact-URL dedup
gate asked one question --

    SELECT ... FROM history WHERE url=? AND status='done'

-- and answered a different one: "this URL was already successfully
downloaded". Those are the same question only while every 'done' row means a
file was produced, and db_log's own contract (db.py, the ``bytes_fetched``
block) says the opposite in so many words: a done row can be a NO-TRANSFER
record.

TWO PRODUCERS WRITE EXACTLY SUCH A ROW, FOR THE PAGE URL:

  1. The GCW probe (runner_transport ``_do_probe_fetch``) streams at most
     256 KB of the media URL, aborts, and SAVES NOTHING -- then logs
     ``done`` with a suggested filename and ``bytes_fetched=recv``. Its own
     comment says a "consumer wanting 'a file was produced' must also require
     one". ``_dedup_preflight`` is that consumer and did not.
  2. The no-download-dir click path (runner ``_process_one``) logs ``done``
     with an EMPTY filename and ``bytes_fetched=0`` after clicking a locator
     and assuming the browser took responsibility.

Note the probe row carries BOTH a nonempty filename and ``bytes_fetched > 0``,
so no filter over those two columns can separate it from a real download. The
match has to require FILE-PRODUCED evidence, and the schema already carries
exactly that: db_log's done path calls ``library.library_record`` only for an
ABSOLUTE path, and its v3.66.837 comment names the GCW probe as the caller
that "produced no file at all" and so "records nothing". Attribution through
``history.library_id`` -> ``library.file_path`` is therefore the evidence, and
it is the same evidence ``db_skip_identity`` reads for the same reason.

Before the fix, a URL that was only probed -- or clicked with no download dir
-- was converted into ``skipped_duplicate`` on EVERY later queue, forever, and
the file could never be fetched unless the operator found ``force_download``.
That is the mission failing in the mission-critical direction: the product
exists to fetch the file, and a probe that fetched nothing permanently blocked
the real fetch.

THE DELETED-FILE CHOICE, STATED DELIBERATELY. A recorded path whose file is no
longer on disk does NOT count as a prior download: the gate requires the file
to exist NOW. This matches ``db_skip_identity``'s "same" arm, which keeps
looking rather than skipping on the strength of a row alone, and it fails in
the only safe direction -- a needless re-download is recoverable, a permanent
skip of a file the operator no longer has is not. UNKNOWN is never permission
(CLAUDE.md A7), so a schema that cannot express attribution at all also
answers "not a prior download" rather than "duplicate".

WHAT IS DRIVEN FOR REAL. The probe row is produced by calling the real
``_do_probe_fetch`` on a real ``SiteRunner`` over a stubbed transport, and the
genuine download is produced by the real ``_do_download``. The click path lives
mid-way through ``_process_one`` behind a live Playwright browser, so its row
is seeded with the producer's literal db_log arguments, and a source-shape
assertion pins those arguments to the producer so the seed cannot drift away
from what the branch writes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE_ID = "row429site"
_SITE_NAME = "Example Site"
_PROBE_URL = "https://members.example.test/scene/probed-only"
_CLICK_URL = "https://members.example.test/scene/clicked-no-dl-dir"
_REAL_URL = "https://members.example.test/scene/really-downloaded"
_GONE_URL = "https://members.example.test/scene/downloaded-then-deleted"

_PROBE_SUGGESTED = "probed-only-1080p.mp4"
# An ISO-BMFF header, so _looks_like_media -> True and _probe_outcome -> "done".
_PROBE_BODY = b"\x00\x00\x00\x18ftypmp42" + b"P" * 4096


# ── the fake transports ─────────────────────────────────────────────────────

class _Locator:
    def __init__(self, href: str):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):  # pragma: no cover - the fake transport is pinned instead
        raise AssertionError("the fake transport must be used, not a click")


class _FakePage:
    def __init__(self, url: str, title: str):
        self.url = url
        self._title = title

    def title(self):
        return self._title

    def evaluate(self, _script):
        return {"og_title": self._title, "document_title": self._title, "h1": ""}


class _FakeDownload:
    """Playwright's download handle, as ``_do_probe_fetch`` uses it."""

    def __init__(self, url: str):
        self.url = url
        self.cancelled = 0

    def cancel(self):
        self.cancelled += 1


class _FakeCtx:
    def cookies(self):
        return []


class _FakeResponse:
    def __init__(self, body: bytes, ctype: str, status: int = 206):
        self.status_code = status
        self.headers = {"Content-Type": ctype,
                        "Content-Length": str(len(body))}
        self._body = body

    def iter_bytes(self):
        # `step` is a name rather than a literal on purpose: a literal here
        # spells `body[i:i + 1024]`, which tools/build_source_window_hashes.py
        # reads as a fixed-width SOURCE WINDOW over a corpus it then tries to
        # resolve statically -- and cannot, because the subject is `self`.
        step = 1024
        for i in range(0, len(self._body), step):
            yield self._body[i:i + step]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Transport:
    """The HTTP arm, instrumented: it writes real bytes and reports them."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def install(self, runner, payload_for):
        def _http_download(page_url, page, ctx, file_url, final_path):
            payload = payload_for(page_url)
            Path(final_path).parent.mkdir(parents=True, exist_ok=True)
            Path(final_path).write_bytes(payload)
            self.calls.append((page_url, str(final_path)))
            return len(payload), len(payload)

        def _pw_save(dl, final_path):
            raise AssertionError(
                "the browser arm ran; this fixture pins the HTTP arm so the "
                "transfer counts below measure one known path")

        runner._http_download = _http_download
        runner._pw_save = _pw_save


# ── fixture ─────────────────────────────────────────────────────────────────

def _new_runner(download_dir):
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    return SiteRunner(
        _SITE_ID,
        {
            "name": _SITE_NAME,
            "download_dir": str(download_dir),
            # Per-URL, so nothing in this file depends on a name collision.
            "filename_template": "{title}",
            "skip_if_exists": True,
            "verify_integrity": False,
            "verify_hash": False,
            "use_http_dl": True,
            "learned": {"download": {"row_selectors": ["a.download"],
                                     "url_attribute": "href"}},
        },
    )


@pytest.fixture
def runner_env(clean_workdir):
    download_dir = clean_workdir / "downloads"
    download_dir.mkdir()
    runner = _new_runner(download_dir)
    transport = _Transport()
    transport.install(runner, lambda url: b"REAL BYTES for " + url.encode())
    try:
        yield runner, transport, download_dir
    finally:
        try:
            runner.stop()
            runner._stop_auto_retry()
        except Exception:
            pass


# ── helpers ─────────────────────────────────────────────────────────────────

def _history(url=None):
    """History rows. ``library_id`` only exists after migration v5, and one
    test below deliberately runs on the pre-migration base schema, so the
    column is selected only when it is really there."""
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
        projection = ("id, site_id, url, status, filename, file_size, "
                      "message, bytes_fetched")
        if "library_id" in cols:
            projection += ", library_id"
        sql = f"SELECT {projection} FROM history"
        params = []
        if url:
            sql += " WHERE url=?"
            params.append(url)
        sql += " ORDER BY id"
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def _library():
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, file_path, history_id FROM library ORDER BY id"
        ).fetchall()]


def _preflight(runner, url, job=None):
    return runner._dedup_preflight(url, job if job is not None else {})


def _run_real_download(runner, download_dir, url, title):
    best = {"locator": _Locator("https://cdn.example.test/media.mp4"),
            "text": "Download 1080p", "score": 1080, "size": 0,
            "_via_learned": True, "_learned_sel": "a.download",
            "_all_candidates": []}
    runner._do_download(_FakePage(url, title), None, url, best,
                        Path(download_dir), "1080p")


def _run_real_probe(runner, monkeypatch, url):
    """Drive the REAL GCW probe producer over a stubbed httpx.stream."""
    from bulk_downloader import runner_transport

    assert runner_transport._HTTPX_AVAILABLE, (
        "httpx is unavailable, so _do_probe_fetch takes its needs_review "
        "early-return and this test would measure nothing")
    # The verdict this fixture depends on, asserted rather than assumed.
    assert runner_transport.TransportMixin._probe_outcome(
        206, len(_PROBE_BODY), "video/mp4", _PROBE_BODY[:512]) == "done"

    seen = {}

    def _fake_stream(method, file_url, **kw):
        seen["url"] = file_url
        return _FakeResponse(_PROBE_BODY, "video/mp4")

    monkeypatch.setattr(runner_transport.httpx, "stream", _fake_stream)
    dl = _FakeDownload("https://cdn.example.test/probe-media.mp4")
    runner._do_probe_fetch(url, _FakePage(url, "Probed Only"), _FakeCtx(),
                           dl, {}, "1080p", _PROBE_SUGGESTED)
    assert seen.get("url") == dl.url, seen
    assert dl.cancelled == 1, "the probe must cancel Playwright's own download"


def _seed_click_row(url):
    """Seed the no-download-dir click row with the producer's literal args.

    That branch sits mid-way through ``_process_one`` behind a live Playwright
    browser, so it cannot be driven here. The assertion below pins the seed to
    the producer's source, so the seed cannot silently drift from the branch it
    stands in for. This is a shape check on the producer, not runtime evidence
    that the branch ran.
    """
    src = Path(__file__).resolve().parents[1] / "bulk_downloader" / "runner.py"
    text = src.read_text(encoding="utf-8")
    needle = 'db_log(self.site_id,self.config.get("name","?"),url,"done","",0,"",'
    assert text.count(needle) == 1, (
        f"the no-dl-dir click producer's db_log call was not found exactly "
        f"once in {src}; this seed no longer matches its producer")
    assert "bytes_fetched=0," in text.split(needle, 1)[1][:200]

    from bulk_downloader.db import db_log
    db_log(_SITE_ID, _SITE_NAME, url, "done", "", 0, "", bytes_fetched=0)


# ── RED 1: the probe row, produced for real ─────────────────────────────────

def test_a_probe_that_saved_nothing_does_not_dedup(runner_env, monkeypatch):
    runner, transport, download_dir = runner_env

    _run_real_probe(runner, monkeypatch, _PROBE_URL)

    # PRECONDITIONS: exactly the row the defect needs, measured not assumed.
    rows = _history(_PROBE_URL)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == "done", row
    assert row["filename"] == _PROBE_SUGGESTED, row
    assert row["bytes_fetched"] == len(_PROBE_BODY) > 0, row
    assert row["file_size"] == len(_PROBE_BODY), row
    assert row["library_id"] is None, row
    assert _library() == [], "the probe must not have recorded a library row"
    # PRECONDITION: nothing was saved. The probe path needs no download dir,
    # and this one is empty.
    assert list(Path(download_dir).iterdir()) == []
    assert transport.calls == []

    # The gate itself.
    from bulk_downloader.db import db_find_url_in_history
    assert db_find_url_in_history(_PROBE_URL) is None, (
        "a probe row that saved nothing is being read as a prior successful "
        "download")
    assert _preflight(runner, _PROBE_URL) is None, (
        "the real fetch of a URL that was only probed is skipped as a "
        "duplicate -- it can never be downloaded")


def test_the_real_download_proceeds_after_a_probe(runner_env, monkeypatch):
    """GREEN reaches the transfer, not just a None from the gate."""
    runner, transport, download_dir = runner_env

    _run_real_probe(runner, monkeypatch, _PROBE_URL)
    assert len(_history(_PROBE_URL)) == 1
    assert transport.calls == []

    assert _preflight(runner, _PROBE_URL) is None
    _run_real_download(runner, download_dir, _PROBE_URL, "Probed Only")

    assert len(transport.calls) == 1, transport.calls
    landed = Path(transport.calls[0][1])
    assert landed.is_file() and landed.stat().st_size > 0
    lib = _library()
    assert len(lib) == 1 and lib[0]["file_path"] == str(landed), lib
    done = [r for r in _history(_PROBE_URL) if r["library_id"] is not None]
    assert len(done) == 1 and done[0]["library_id"] == lib[0]["id"], done


# ── RED 2: the click row ────────────────────────────────────────────────────

def test_a_click_with_no_download_dir_does_not_dedup(runner_env):
    runner, transport, download_dir = runner_env

    _seed_click_row(_CLICK_URL)

    rows = _history(_CLICK_URL)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == "done", row
    assert row["filename"] == "", row
    assert row["bytes_fetched"] == 0, row
    assert row["library_id"] is None, row
    assert _library() == []
    assert list(Path(download_dir).iterdir()) == []

    from bulk_downloader.db import db_find_url_in_history
    assert db_find_url_in_history(_CLICK_URL) is None
    assert _preflight(runner, _CLICK_URL) is None, (
        "a click with no download dir permanently blocks the real download")

    # ...and the real transfer then happens, which is the point.
    _run_real_download(runner, download_dir, _CLICK_URL, "Clicked No Dl Dir")
    assert len(transport.calls) == 1, transport.calls
    landed = Path(transport.calls[0][1])
    assert landed.is_file() and landed.stat().st_size > 0
    lib = _library()
    assert len(lib) == 1 and lib[0]["file_path"] == str(landed), lib
    attributed = [r for r in _history(_CLICK_URL) if r["library_id"] is not None]
    assert len(attributed) == 1 and attributed[0]["library_id"] == lib[0]["id"]


# ── NEGATIVE CONTROL 1: a genuine download STILL dedups ─────────────────────

def test_a_genuine_prior_download_is_still_deduped(runner_env):
    runner, transport, download_dir = runner_env

    _run_real_download(runner, download_dir, _REAL_URL, "Really Downloaded")

    # PRECONDITIONS: a real file, nonzero bytes, attributed.
    assert len(transport.calls) == 1, transport.calls
    landed = Path(transport.calls[0][1])
    assert landed.is_file()
    size = landed.stat().st_size
    assert size > 0
    rows = _history(_REAL_URL)
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "done" and rows[0]["bytes_fetched"] == size
    lib = _library()
    assert len(lib) == 1 and lib[0]["file_path"] == str(landed)
    assert rows[0]["library_id"] == lib[0]["id"]

    from bulk_downloader.db import db_find_url_in_history
    hit = db_find_url_in_history(_REAL_URL)
    assert hit is not None, "dedup was turned OFF for a real prior download"
    assert hit["id"] == rows[0]["id"]
    # The returned shape consumers read is unchanged.
    assert set(hit) == {"id", "site_id", "site_name", "url", "filename",
                        "file_size", "ts"}, sorted(hit)

    msg = _preflight(runner, _REAL_URL)
    assert msg and f"history #{rows[0]['id']}" in msg, msg
    # And the preflight really did skip: no second transfer.
    assert len(transport.calls) == 1, transport.calls


# ── NEGATIVE CONTROL 2: the file has since been deleted ─────────────────────

def test_a_download_whose_file_is_gone_is_not_proof(runner_env):
    """Deliberate choice: the gate requires the file to exist NOW.

    A row alone proves nothing about what is on disk, and a permanent skip of
    a file the operator no longer has is unrecoverable, while a needless
    re-download is not. Same rule ``db_skip_identity`` already applies.
    """
    runner, transport, download_dir = runner_env

    _run_real_download(runner, download_dir, _GONE_URL, "Downloaded Then Deleted")
    assert len(transport.calls) == 1
    landed = Path(transport.calls[0][1])
    assert landed.is_file()

    from bulk_downloader.db import db_find_url_in_history
    assert db_find_url_in_history(_GONE_URL) is not None, (
        "precondition: while the file is on disk this URL dedups")

    os.unlink(landed)
    assert not landed.exists()
    # The row and its library attribution are untouched -- only the file went.
    rows = _history(_GONE_URL)
    assert len(rows) == 1 and rows[0]["library_id"] is not None
    assert len(_library()) == 1

    assert db_find_url_in_history(_GONE_URL) is None
    assert _preflight(runner, _GONE_URL) is None


# ── the ordering the LIMIT 1 shape got wrong ────────────────────────────────

def test_a_later_probe_row_does_not_mask_the_genuine_download(
    runner_env, monkeypatch,
):
    """A probe AFTER a real download must not hide it.

    The newest row for the URL is then the probe, so a ``LIMIT 1`` over the
    attributed set is not enough -- the gate has to walk id DESC and answer
    with the newest row that still has a file.
    """
    runner, transport, download_dir = runner_env

    _run_real_download(runner, download_dir, _REAL_URL, "Really Downloaded")
    genuine = _history(_REAL_URL)
    assert len(genuine) == 1 and genuine[0]["library_id"] is not None

    _run_real_probe(runner, monkeypatch, _REAL_URL)
    rows = _history(_REAL_URL)
    assert len(rows) == 2, rows
    assert rows[-1]["id"] > genuine[0]["id"]
    assert rows[-1]["library_id"] is None, (
        "precondition: the newest row is the probe row, unattributed")

    from bulk_downloader.db import db_find_url_in_history
    hit = db_find_url_in_history(_REAL_URL)
    assert hit is not None and hit["id"] == genuine[0]["id"], hit


# ── UNKNOWN is never permission, and fail-soft stays fail-soft ──────────────

def test_a_schema_that_cannot_express_attribution_answers_unknown(clean_workdir):
    """Base schema only (``db_init``, no migrations): no library table.

    File evidence is unmeasurable, so the answer is "not a prior download",
    never "duplicate".
    """
    from bulk_downloader.db import db_conn, db_find_url_in_history, db_init, db_log

    db_init()
    with db_conn() as cx:
        assert cx.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='library'").fetchone()[0] == 0, (
            "precondition: db_init must not create the library table")
    db_log(_SITE_ID, _SITE_NAME, _REAL_URL, "done", "x.mp4", 10, "")
    assert len(_history(_REAL_URL)) == 1
    assert db_find_url_in_history(_REAL_URL) is None


def test_the_gate_stays_fail_soft(runner_env, monkeypatch):
    """Any error -> None. Dedup must never block a legitimate download."""
    runner, transport, download_dir = runner_env
    from bulk_downloader import db

    def _boom(*a, **kw):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(db, "db_conn", _boom)
    assert db.db_find_url_in_history(_REAL_URL) is None
    assert db.db_find_url_in_history("") is None
    assert db.db_find_url_in_history(None) is None
