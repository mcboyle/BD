"""A skip_if_exists hit must prove the file on disk is the SAME work.

THE DEFECT (runner_transport.py, the "Already have" pre-download check). After
the filename template renders, the branch asked one question -- does a file
sit at ``final_path``? -- and answered a completely different one: "this page's
work is already downloaded". It then wrote

    db_log(..., status='done', filename, existing_size, "already on disk",
           file_path=str(final_path), **history_title_kwargs(self, page_url))

and db_log's done path calls ``library.library_record(file_path, title=...)``,
whose UPDATE carries ``title = CASE WHEN ?<>'' THEN ? ELSE title END``. So a
second scene whose template renders the SAME name produced two wrong records at
once:

  1. scene B's history row said ``done`` over scene A's bytes, and
  2. scene A's library row was RETITLED to scene B.

That is the wrong-file-right-title shape of the 2026-08-29 incident, and it is
reachable on any template that does not vary per scene -- ``{site} -
{resolution}`` is enough, and so is a site whose ``?filename=`` basename is
generic. There was no size check and no content check; ``db_find_filename_
duplicate`` at least compares sizes within 5%. ``safe_dest``, which exists
precisely to resolve same-name-different-content, ran only AFTER this branch
and so never saw the case.

THE CONTRACT. Existence is not identity. The branch is now three-state, per
CLAUDE.md A7:

  SAME       a prior 'done' history row for THIS page_url whose linked library
             file is still on disk -> skip, reporting THAT file.
  DIFFERENT  final_path is attributed by the library to another page_url ->
             do not skip, do not retitle; fall through to safe_dest.
  UNKNOWN    nothing attributes the file to anything -> not permission. Fall
             through to safe_dest as well: do not skip and do not overwrite.

The SAME arm is keyed on the ATTRIBUTED path rather than on final_path, which
is what stops the UNKNOWN arm from accreting ``name_1``, ``name_2``, ...  on
every re-run: once run 1 has landed at ``name_1.mp4``, the url owns that file
and run 2 skips it.
"""
from __future__ import annotations

from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE_ID = "sameworksite"
_URL_A = "https://members.example.test/scene/aaa-first-scene"
_URL_B = "https://members.example.test/scene/bbb-second-scene"
_URL_C = "https://members.example.test/scene/ccc-third-scene"
_TITLE_A = "First Scene"
_TITLE_B = "Second Scene"
_TITLE_C = "Third Scene"

# The whole point: this template renders the SAME basename for every scene on
# the site, so scene B's final_path is scene A's file.
_COLLIDING_TEMPLATE = "{site} - {resolution}"
_COLLIDING_NAME = "Example Site - 1080p.mp4"


class _Locator:
    def __init__(self, href: str):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):  # pragma: no cover - the fake transport never needs it
        raise AssertionError("the fake transport must be used, not a click")


class _FakePage:
    """The minimum Playwright surface ``_do_download`` touches."""

    def __init__(self, url: str, title: str):
        self.url = url
        self._title = title
        self.evaluate_calls = 0

    def title(self):
        return self._title

    def evaluate(self, _script):
        self.evaluate_calls += 1
        return {
            "og_title": self._title,
            "document_title": self._title,
            "h1": "",
        }


class _Transport:
    """A real stand-in for the HTTP arm: it writes bytes and reports them.

    Recording the exact destination each call received is what makes the
    reachability counts in this file measurements rather than assumptions.
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.payloads: dict[str, bytes] = {}

    def install(self, runner, payload_for):
        def _http_download(page_url, page, ctx, file_url, final_path):
            payload = payload_for(page_url)
            Path(final_path).parent.mkdir(parents=True, exist_ok=True)
            Path(final_path).write_bytes(payload)
            self.calls.append((page_url, str(final_path)))
            self.payloads[str(final_path)] = payload
            return len(payload), len(payload)

        def _pw_save(dl, final_path):  # the browser arm must never be the one
            raise AssertionError(
                "the browser arm ran; this fixture pins the HTTP arm so the "
                "transfer count below measures one known path")

        runner._http_download = _http_download
        runner._pw_save = _pw_save


def _runner(clean_workdir, download_dir, template=_COLLIDING_TEMPLATE):
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    runner = SiteRunner(
        _SITE_ID,
        {
            "name": "Example Site",
            "download_dir": str(download_dir),
            "filename_template": template,
            "skip_if_exists": True,
            # ffprobe/hash verification are separate contracts; this fixture is
            # about the pre-download identity question and must not depend on
            # whether ffprobe exists on the host.
            "verify_integrity": False,
            "verify_hash": False,
            "use_http_dl": True,
            "learned": {
                "download": {
                    "row_selectors": ["a.download"],
                    "url_attribute": "href",
                }
            },
        },
    )
    return runner


def _best(name: str):
    return {
        "locator": _Locator(f"https://cdn.example.test/{name}"),
        "text": "Download 1080p",
        "score": 1080,
        # 0, not a real size: the Phase 17.20 sanity check only fires above
        # 1MB advertised, and this fixture is not about that check.
        "size": 0,
        "_via_learned": True,
        "_learned_sel": "a.download",
        "_all_candidates": [],
    }


def _stop(runner):
    try:
        runner.stop()
        runner._stop_auto_retry()
    except Exception:
        pass


def _rows():
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        history = [dict(r) for r in cx.execute(
            "SELECT id, url, status, filename, file_size, message, "
            "bytes_fetched, library_id FROM history ORDER BY id"
        ).fetchall()]
        library = [dict(r) for r in cx.execute(
            "SELECT id, file_path, file_size, file_mtime, title, title_source, history_id "
            "FROM library ORDER BY id"
        ).fetchall()]
    return history, library


def _media(download_dir):
    return sorted(p.name for p in Path(download_dir).iterdir() if p.suffix == ".mp4")


@pytest.fixture
def scene_runner(clean_workdir):
    download_dir = clean_workdir / "downloads"
    download_dir.mkdir()
    runner = _runner(clean_workdir, download_dir)
    transport = _Transport()
    payloads = {
        _URL_A: b"AAAA scene a bytes",
        _URL_B: b"BBBB scene b bytes are different",
        _URL_C: b"CCCC scene c bytes differ again",
    }
    transport.install(runner, lambda url: payloads[url])
    try:
        yield runner, transport, download_dir, payloads
    finally:
        _stop(runner)


def _run(runner, dl_dir, url, title, name=_COLLIDING_NAME):
    page = _FakePage(url, title)
    runner._do_download(page, None, url, _best(name), Path(dl_dir), "1080p")
    return page


# ── The defect, end to end ──────────────────────────────────────────────────

def test_a_second_scene_never_inherits_the_first_scenes_file_or_title(
    scene_runner,
):
    """RED on the defective tree, and the whole point of the cut.

    Before the fix this test recorded, at v3.66.1362:
      history[1] = url=<scene B>, status='done', filename='Example Site -
                   1080p.mp4', message='already on disk'  -- scene A's bytes
      library[0] = file_path=<scene A's file>, title='Second Scene'
                   -- scene A's bytes, retitled to scene B
    and exactly ONE transfer had happened, so scene B was never downloaded.
    """
    runner, transport, download_dir, payloads = scene_runner

    _run(runner, download_dir, _URL_A, _TITLE_A)

    # PRECONDITION: scene A really did land, through the instrumented arm, at
    # the colliding name. Without this the collision below proves nothing.
    assert len(transport.calls) == 1, transport.calls
    scene_a_file = download_dir / _COLLIDING_NAME
    assert scene_a_file.exists()
    assert scene_a_file.read_bytes() == payloads[_URL_A]
    history, library = _rows()
    assert len(history) == 1 and history[0]["url"] == _URL_A
    assert len(library) == 1
    assert library[0]["file_path"] == str(scene_a_file)
    assert library[0]["title"] == _TITLE_A

    # PRECONDITION: scene B's template renders the very same path. This is the
    # collision, asserted rather than assumed.
    from bulk_downloader.fname import resolve_filename_template

    assert resolve_filename_template(
        _COLLIDING_TEMPLATE,
        {"site": "Example Site", "resolution": "1080p"},
    ) + ".mp4" == _COLLIDING_NAME

    _run(runner, download_dir, _URL_B, _TITLE_B)

    history, library = _rows()

    # 1. Scene B must not be reported done over scene A's bytes.
    b_rows = [r for r in history if r["url"] == _URL_B]
    assert len(b_rows) == 1, history
    assert b_rows[0]["message"] != "already on disk", (
        "scene B was marked 'already on disk' over scene A's file: the skip "
        "asked whether a file exists, not whether it is this work")
    assert b_rows[0]["filename"] != _COLLIDING_NAME, (
        f"scene B's history row names scene A's file {_COLLIDING_NAME!r}")

    # 2. Scene A's library row must keep scene A's title.
    a_lib = [r for r in library if r["file_path"] == str(scene_a_file)]
    assert len(a_lib) == 1, library
    assert a_lib[0]["title"] == _TITLE_A, (
        f"scene A's bytes are now titled {a_lib[0]['title']!r} -- "
        f"library_record's title CASE overwrote it from the skip path")

    # 3. Scene A's bytes are untouched and scene B's own bytes are on disk
    #    under a distinct name, which is exactly what safe_dest is for.
    assert scene_a_file.read_bytes() == payloads[_URL_A]
    assert len(transport.calls) == 2, (
        f"scene B was never transferred; calls={transport.calls}")
    b_path = transport.calls[1][1]
    assert b_path != str(scene_a_file)
    assert Path(b_path).read_bytes() == payloads[_URL_B]

    # 4. Scene B gets its OWN library row carrying its own title.
    b_lib = [r for r in library if r["file_path"] == b_path]
    assert len(b_lib) == 1, library
    assert b_lib[0]["title"] == _TITLE_B

    assert _media(download_dir) == sorted(
        [_COLLIDING_NAME, Path(b_path).name])


# ── Reachability: the three arms, with exact counts ─────────────────────────

def test_the_same_work_arm_skips_and_transfers_nothing(scene_runner):
    """NEGATIVE CONTROL: a genuine re-download of the SAME work still skips.

    The fix must not turn a legitimate skip into a duplicate download, so this
    runs the same url three times and pins both the transfer count and the
    file count.
    """
    runner, transport, download_dir, payloads = scene_runner

    _run(runner, download_dir, _URL_A, _TITLE_A)
    assert len(transport.calls) == 1
    assert _media(download_dir) == [_COLLIDING_NAME]

    for _ in range(2):
        _run(runner, download_dir, _URL_A, _TITLE_A)

    # The skip fired both times: no further transfer, no further file.
    assert len(transport.calls) == 1, (
        f"a re-run of the same work downloaded again: {transport.calls}")
    assert _media(download_dir) == [_COLLIDING_NAME], (
        "the same work accreted a duplicate on re-run")

    history, library = _rows()
    skips = [r for r in history
             if r["url"] == _URL_A and r["message"] == "already on disk"]
    assert len(skips) == 2, history
    for row in skips:
        assert row["status"] == "done"
        assert row["filename"] == _COLLIDING_NAME
        assert row["bytes_fetched"] == 0, (
            "a skip transferred nothing; bytes_fetched must say so")

    # Its title survives every skip, and no second library row appeared.
    assert len(library) == 1, library
    assert library[0]["title"] == _TITLE_A


def test_the_different_work_arm_downloads_instead_of_skipping(scene_runner):
    runner, transport, download_dir, payloads = scene_runner

    _run(runner, download_dir, _URL_A, _TITLE_A)
    _run(runner, download_dir, _URL_B, _TITLE_B)

    assert len(transport.calls) == 2
    # PRECONDITION for the arm: the library really does attribute the
    # colliding path to scene A, which is what makes scene B "different"
    # rather than merely "unknown".
    _, library = _rows()
    owner = [r for r in library
             if r["file_path"] == str(download_dir / _COLLIDING_NAME)]
    assert len(owner) == 1 and owner[0]["history_id"], library

    b_path = Path(transport.calls[1][1])
    assert b_path.name == "Example Site - 1080p_1.mp4", b_path
    assert b_path.read_bytes() == payloads[_URL_B]


def test_an_unattributed_file_is_unknown_and_is_not_skipped(scene_runner):
    """UNKNOWN is a failing third state, not permission.

    A file nothing attributes to anything -- copied in by hand, or left by a
    scan -- cannot prove it is this scene, so the branch may neither claim the
    job done nor overwrite the bytes.
    """
    runner, transport, download_dir, payloads = scene_runner

    stray = download_dir / _COLLIDING_NAME
    stray.write_bytes(b"stray bytes of unknown provenance")

    # PRECONDITION: nothing in either table refers to it.
    history, library = _rows()
    assert history == [] and library == []

    _run(runner, download_dir, _URL_C, _TITLE_C)

    assert len(transport.calls) == 1, transport.calls
    landed = Path(transport.calls[0][1])
    assert landed != stray, "an unprovable file was overwritten"
    assert stray.read_bytes() == b"stray bytes of unknown provenance"
    assert landed.read_bytes() == payloads[_URL_C]

    history, library = _rows()
    c_rows = [r for r in history if r["url"] == _URL_C]
    assert len(c_rows) == 1
    assert c_rows[0]["message"] != "already on disk"
    assert c_rows[0]["filename"] == landed.name

    # The stray file was never given a library row, so it was never titled.
    assert [r["file_path"] for r in library] == [str(landed)]


def test_the_unknown_arm_does_not_accrete_a_copy_on_every_rerun(scene_runner):
    """NEGATIVE CONTROL for the fix's own defect shape.

    Keying "same work" on final_path rather than on the ATTRIBUTED path would
    make every re-run of an UNKNOWN url render the original name, miss its own
    prior download, and land a fresh copy forever.
    """
    runner, transport, download_dir, payloads = scene_runner

    (download_dir / _COLLIDING_NAME).write_bytes(b"stray bytes")

    _run(runner, download_dir, _URL_C, _TITLE_C)
    assert len(transport.calls) == 1
    first = _media(download_dir)
    assert len(first) == 2, first

    for _ in range(3):
        _run(runner, download_dir, _URL_C, _TITLE_C)

    assert len(transport.calls) == 1, (
        f"re-running an url that already owns its file downloaded again: "
        f"{transport.calls}")
    assert _media(download_dir) == first, (
        f"the unknown arm accreted copies: {first} -> {_media(download_dir)}")

    history, _library = _rows()
    skips = [r for r in history
             if r["url"] == _URL_C and r["message"] == "already on disk"]
    assert len(skips) == 3, history
    # The skip reports the file the url actually owns, not the colliding name.
    for row in skips:
        assert row["filename"] == "Example Site - 1080p_1.mp4", row


# ── Row 485: losing attribution is not permission to transfer ──────────────

def test_a_dangling_library_link_refuses_a_silent_redownload(scene_runner):
    """Deleting history must not turn one proven file into a second copy."""
    from bulk_downloader.db import db_conn, db_skip_identity

    runner, transport, download_dir, payloads = scene_runner
    _run(runner, download_dir, _URL_A, _TITLE_A)

    history, library = _rows()
    owned = download_dir / _COLLIDING_NAME
    assert len(transport.calls) == 1, transport.calls
    assert _media(download_dir) == [_COLLIDING_NAME]
    assert owned.is_file() and owned.read_bytes() == payloads[_URL_A]
    assert len(library) == 1 and library[0]["file_path"] == str(owned), library
    assert len(history) == 1 and history[0]["status"] == "done", history
    dangling_id = library[0]["history_id"]
    assert dangling_id == history[0]["id"]
    assert db_skip_identity(_URL_A, str(owned)) == ("same", str(owned))

    # Delete only the attribution row. The library row and file survive, but
    # the current owner can no longer be measured through the join.
    with db_conn() as cx:
        removed = cx.execute("DELETE FROM history").rowcount
        cx.commit()
    assert removed == 1
    history, library_after_delete = _rows()
    assert history == []
    assert len(library_after_delete) == 1
    assert library_after_delete[0]["file_path"] == str(owned)
    assert library_after_delete[0]["history_id"] == dangling_id
    with db_conn() as cx:
        live_links = cx.execute(
            "SELECT COUNT(*) FROM history WHERE id = ?", (dangling_id,)
        ).fetchone()[0]
    assert live_links == 0

    _run(runner, download_dir, _URL_A, _TITLE_A)

    history_after, library_after = _rows()
    assert len(transport.calls) == 1, (
        "the dangling attribution became permission for one full-size "
        f"re-download: calls={transport.calls}, media={_media(download_dir)}")
    assert _media(download_dir) == [_COLLIDING_NAME]
    assert owned.read_bytes() == payloads[_URL_A]
    assert len(library_after) == 1 and library_after[0]["file_path"] == str(owned)
    assert len(history_after) == 1, history_after
    assert history_after[0]["status"] == "needs_review"
    assert "attribution" in (history_after[0]["message"] or "").lower()


def test_a_relative_download_dir_refuses_unbounded_duplicate_transfers(
    clean_workdir,
):
    relative_download_dir = Path("relative-downloads")
    assert not relative_download_dir.is_absolute()
    relative_download_dir.mkdir()
    runner = _runner(clean_workdir, relative_download_dir)
    transport = _Transport()
    payload = b"relative directory scene bytes"
    transport.install(runner, lambda _url: payload)

    try:
        _run(runner, relative_download_dir, _URL_A, _TITLE_A)
        history, library = _rows()
        assert len(transport.calls) == 1, transport.calls
        assert _media(relative_download_dir) == [_COLLIDING_NAME]
        assert (relative_download_dir / _COLLIDING_NAME).read_bytes() == payload
        assert len(history) == 1 and history[0]["status"] == "done", history
        assert history[0]["library_id"] is None
        assert library == [], (
            "a relative path unexpectedly entered library, so this fixture "
            "does not reach the unmeasurable-attribution arm")

        for _ in range(3):
            _run(runner, relative_download_dir, _URL_A, _TITLE_A)

        history, library = _rows()
        media = _media(relative_download_dir)
        assert len(history) == 4, history
        assert library == []
        assert all(row["library_id"] is None for row in history), history
        assert len(transport.calls) == 1, (
            "four runs under a relative download_dir performed more than the "
            f"one measurable transfer: calls={transport.calls}, media={media}")
        assert media == [_COLLIDING_NAME]
        assert len([row for row in history if row["status"] == "done"]) == 1
        review_rows = [row for row in history
                       if row["status"] == "needs_review"]
        assert len(review_rows) == 3, history
        assert all("attribution" in (row["message"] or "").lower()
                   for row in review_rows)
    finally:
        _stop(runner)


def test_the_three_arms_are_each_reached_exactly_once(scene_runner):
    """One run of the three shapes, counted at the branch itself.

    A green battery is not coverage evidence until the exercised path is
    identified, so this instruments the identity helper and asserts the exact
    verdict each scene produced.
    """
    from bulk_downloader import runner_transport as rt

    runner, transport, download_dir, payloads = scene_runner

    seen: list[str] = []
    real = rt.db_skip_identity

    def _spy(page_url, final_path):
        verdict, path = real(page_url, final_path)
        seen.append(verdict)
        return verdict, path

    other_dir = download_dir.parent / "elsewhere"
    other_dir.mkdir()

    # Scene A first, into an EMPTY directory: nothing exists, so the branch's
    # own guard is false and the identity question is never asked. That is
    # deliberate -- it keeps `seen` a record of the three arms only.
    _run(runner, download_dir, _URL_A, _TITLE_A)
    assert seen == [], seen

    rt.db_skip_identity = _spy
    try:
        # DIFFERENT: B renders A's path, and the library attributes it to A.
        _run(runner, download_dir, _URL_B, _TITLE_B)
        # SAME: A renders its own path, which A provably owns.
        _run(runner, download_dir, _URL_A, _TITLE_A)
        # UNKNOWN: a hand-placed file in a fresh directory that nothing in
        # either table refers to.
        (other_dir / _COLLIDING_NAME).write_bytes(b"stray bytes")
        _run(runner, other_dir, _URL_C, _TITLE_C)
    finally:
        rt.db_skip_identity = real

    assert seen == ["different", "same", "unknown"], seen
    # A downloaded, B downloaded beside it, A skipped, C downloaded elsewhere.
    assert len(transport.calls) == 3, transport.calls
    assert _media(download_dir) == [
        _COLLIDING_NAME, "Example Site - 1080p_1.mp4"]
    assert _media(other_dir) == [
        _COLLIDING_NAME, "Example Site - 1080p_1.mp4"]


# ── Row 479: a done row that recorded NO transfer is not ownership ──────────
#
# db_skip_identity's SAME arm asked for a done status and a library link and
# NOTHING about a transfer. db_log's own contract says bytes_fetched is the only
# column that can answer whether BD moved bytes: >0 a real transfer, 0 nothing
# transferred, None UNKNOWN and never proof of a download. The skip arm WRITES a
# bytes_fetched=0 done row itself, and library_record backfills its library_id,
# so every skip manufactured the proof the next run read -- A7's shape of
# deriving the expected set from the artifact under test.
#
# The state seeded below is the one an upgraded host actually carries, not the
# clean database every test above builds: before the v3.66.1368 skip-identity
# cut the branch skipped on final_path.exists() alone and logged done,
# bytes_fetched=0, "already on disk" over whatever file sat there.


def _seed_a_pre_cut_wrong_file_row(url, library_id):
    """The row a pre-v3.66.1368 build wrote: done, no transfer, someone else's file."""
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        cx.execute(
            "INSERT INTO history (site_id, site_name, url, status, filename, "
            "file_size, message, bytes_fetched, library_id, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))",
            (_SITE_ID, "Example Site", url, "done", _COLLIDING_NAME, 18,
             "already on disk", 0, library_id))
        cx.commit()


def test_a_done_row_recording_no_transfer_is_not_proof_of_ownership(scene_runner):
    """RED on the defective parent.

    Preconditions are asserted before any verdict, because the whole defect is a
    verdict reached over a state nobody measured.
    """
    runner, transport, download_dir, payloads = scene_runner

    # Scene A is downloaded for real: one transfer, one file, one library row.
    _run(runner, download_dir, _URL_A, _TITLE_A)
    history, library = _rows()
    assert len(transport.calls) == 1
    assert _media(download_dir) == [_COLLIDING_NAME]
    assert len(library) == 1, library
    scene_a_path = library[0]["file_path"]
    assert Path(scene_a_path).is_file()
    assert Path(scene_a_path).read_bytes() == payloads[_URL_A]

    # Now the state an upgraded host carries: scene B has a done row over scene
    # A's file, recording no transfer at all.
    _seed_a_pre_cut_wrong_file_row(_URL_B, library[0]["id"])

    history, library = _rows()
    b_rows = [r for r in history if r["url"] == _URL_B]
    assert len(b_rows) == 1, b_rows
    assert b_rows[0]["status"] == "done"
    assert b_rows[0]["bytes_fetched"] == 0
    assert b_rows[0]["library_id"] == library[0]["id"]
    assert [r for r in history
            if r["url"] == _URL_B and (r["bytes_fetched"] or 0) > 0] == [], (
        "the fixture must seed ZERO transfers for scene B, or the defect below "
        "is not the one being measured")
    assert len(_media(download_dir)) == 1

    transfers_before = len(transport.calls)

    _run(runner, download_dir, _URL_B, _TITLE_B)

    history, library = _rows()

    # GREEN: scene B is not handed scene A's file. It reaches the transfer path
    # exactly once, and scene A's row is untouched.
    assert len(transport.calls) == transfers_before + 1, (
        "scene B was skipped over a done row that recorded no transfer: the "
        f"row manufactured its own proof. transfers={transport.calls}")
    a_row = [r for r in library if r["file_path"] == scene_a_path]
    assert len(a_row) == 1, library
    assert a_row[0]["title"] == _TITLE_A, (
        "scene A's library row was retitled by scene B, which is the "
        "wrong-file-right-title shape this whole file exists for")
    assert Path(scene_a_path).read_bytes() == payloads[_URL_A]

    # A pre-existing no-transfer attribution must become OPERATOR-VISIBLE rather
    # than a silent re-download.
    review = [r for r in history if r["status"] == "needs_review"]
    assert len(review) == 1, (
        f"expected exactly 1 needs_review diagnostic for the unproven "
        f"attribution, got {len(review)}: {review}")
    assert review[0]["url"] == _URL_B
    assert "no transfer" in (review[0]["message"] or "").lower(), review[0]
    assert scene_a_path in (review[0]["message"] or ""), (
        "the diagnostic must NAME the row it is about; a refusal an operator "
        "cannot act on is barely better than a silent one")


def test_an_unmeasurable_transfer_is_unknown_and_never_proof(scene_runner):
    """A pre-v8 row carries bytes_fetched NULL. UNKNOWN is a failing third
    state, never permission (A2)."""
    runner, transport, download_dir, payloads = scene_runner
    from bulk_downloader.db import db_conn

    _run(runner, download_dir, _URL_A, _TITLE_A)
    _history, library = _rows()
    with db_conn() as cx:
        cx.execute(
            "INSERT INTO history (site_id, site_name, url, status, filename, "
            "file_size, message, bytes_fetched, library_id, ts) "
            "VALUES (?,?,?,?,?,?,?,NULL,?, datetime('now'))",
            (_SITE_ID, "Example Site", _URL_B, "done", _COLLIDING_NAME, 18,
             "pre-v8 row with no bytes_fetched column", library[0]["id"]))
        cx.commit()

    history, _library = _rows()
    assert [r for r in history
            if r["url"] == _URL_B and r["bytes_fetched"] is None], history

    before = len(transport.calls)
    _run(runner, download_dir, _URL_B, _TITLE_B)
    assert len(transport.calls) == before + 1, (
        "a NULL bytes_fetched was read as proof of a download; db_log's own "
        "contract says None is UNKNOWN and never proof")


def test_the_healthy_steady_state_still_skips(scene_runner):
    """NEGATIVE CONTROL, and the one that separates a real fix from a lazy one.

    One done row recording a REAL transfer, then two later bytes_fetched=0 skip
    rows for that same url. A fix that inspects only the newest owned row is
    caught here; a fix that scans for transfer evidence passes.
    """
    runner, transport, download_dir, payloads = scene_runner

    _run(runner, download_dir, _URL_A, _TITLE_A)
    for _ in range(2):
        _run(runner, download_dir, _URL_A, _TITLE_A)

    history, _library = _rows()
    a_rows = [r for r in history if r["url"] == _URL_A]
    assert len([r for r in a_rows if (r["bytes_fetched"] or 0) > 0]) == 1, a_rows
    assert len([r for r in a_rows if r["bytes_fetched"] == 0]) == 2, a_rows
    assert a_rows[-1]["bytes_fetched"] == 0, (
        "the NEWEST row must be a no-transfer skip, or this control does not "
        "distinguish a newest-row fix from a scanning one")

    before = len(transport.calls)
    _run(runner, download_dir, _URL_A, _TITLE_A)
    assert len(transport.calls) == before, (
        "the healthy steady state stopped skipping: the fix turned every "
        f"legitimate resume into a re-download. transfers={transport.calls}")
    assert _media(download_dir) == [_COLLIDING_NAME]
    history, library = _rows()
    assert len(library) == 1, library
    assert [r for r in history if r["status"] == "needs_review"] == [], (
        "a healthy history produced a needs_review diagnostic")


# ── Rows 503 and 519: a skip must re-prove the identity it acts on ─────────

def test_replaced_owned_file_is_unknown_and_never_rewrites_its_measurement(
    scene_runner,
):
    """A recorded size is evidence only while it still describes the file.

    RED on the defective parent: its identity query observes only ``isfile``;
    the skip then restats the replacement and records that new measurement over
    the prior one, deleting the only evidence that the file changed.
    """
    from bulk_downloader.db import db_skip_identity

    runner, transport, download_dir, payloads = scene_runner
    _run(runner, download_dir, _URL_A, _TITLE_A)

    history, library = _rows()
    owned = download_dir / _COLLIDING_NAME
    assert len(transport.calls) == 1, transport.calls
    assert owned.is_file() and owned.read_bytes() == payloads[_URL_A]
    assert len(history) == 1 and history[0]["bytes_fetched"] > 0, history
    assert len(library) == 1, library
    before = library[0]
    assert before["file_path"] == str(owned)
    assert before["file_size"] == owned.stat().st_size
    assert before["file_mtime"] > 0
    assert db_skip_identity(_URL_A, str(owned)) == ("same", str(owned))

    replacement = b"replacement bytes with a deliberately different exact size"
    owned.write_bytes(replacement)
    replacement_size = owned.stat().st_size
    assert replacement_size != before["file_size"], (
        "the replacement must create a named nonzero size delta")
    assert owned.read_bytes() == replacement

    # The defect's verdict: the old size no longer describes this path, so it
    # cannot be permission to skip.  This assertion is RED on the unfixed base.
    assert db_skip_identity(_URL_A, str(owned)) == ("unknown", None)

    transfers_before = len(transport.calls)
    _run(runner, download_dir, _URL_A, _TITLE_A)

    history, library = _rows()
    assert len(transport.calls) == transfers_before + 1, transport.calls
    assert not [r for r in history if r["message"] == "already on disk"], history
    original_rows = [r for r in library if r["file_path"] == str(owned)]
    assert len(original_rows) == 1, library
    assert original_rows[0]["file_size"] == before["file_size"]
    assert original_rows[0]["file_mtime"] == before["file_mtime"]


def test_vanished_safe_destination_after_same_proof_falls_through_to_transfer(
    scene_runner,
):
    """A later rendered collision must not hide a vanished owned path.

    The original, unattributed filename remains present while the first real
    transfer owns ``_1``.  This is the necessary negative shape: a check of
    only the freshly rendered path still passes after the owned file vanishes.
    """
    from bulk_downloader import runner_transport as rt

    runner, transport, download_dir, payloads = scene_runner
    rendered = download_dir / _COLLIDING_NAME
    rendered.write_bytes(b"unattributed collision that forces safe_dest")
    _run(runner, download_dir, _URL_A, _TITLE_A)

    history_before, library_before = _rows()
    owned = Path(transport.calls[0][1])
    assert len(transport.calls) == 1, transport.calls
    assert rendered.is_file() and rendered != owned
    assert owned.name == "Example Site - 1080p_1.mp4" and owned.is_file()
    assert owned.read_bytes() == payloads[_URL_A]
    assert len(history_before) == 1 and len(library_before) == 1
    assert library_before[0]["file_path"] == str(owned)

    seen: list[str] = []
    real = rt.db_skip_identity

    def _unlink_after_same(page_url, final_path):
        verdict, path = real(page_url, final_path)
        seen.append(verdict)
        if verdict == "same":
            assert path == str(owned)
            assert owned.is_file(), "the injection must remove the proven owned path"
            owned.unlink()
            assert not owned.exists(), "the injected disappearance did not fire"
            assert rendered.is_file(), "the rendered-path control must stay present"
        return verdict, path

    rt.db_skip_identity = _unlink_after_same
    try:
        _run(runner, download_dir, _URL_A, _TITLE_A)
    finally:
        rt.db_skip_identity = real

    # GREEN: no FileNotFoundError escapes from the skip arm; the job reaches
    # the instrumented transport and records the bytes actually written.
    assert seen == ["same"], seen
    assert len(transport.calls) == 2, transport.calls
    history, _library = _rows()
    a_rows = [r for r in history if r["url"] == _URL_A]
    assert len(a_rows) == 2, a_rows
    assert a_rows[-1]["status"] == "done"
    assert a_rows[-1]["bytes_fetched"] == len(payloads[_URL_A])
