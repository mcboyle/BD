"""L12 reported N/A on a host where a segmented download had just succeeded.

THE DEFECT, measured on the deploy host 2026-07-30. The seeded HLS URL
completed for the first time:

    /hlspage/2                  'done'  'Saved: 2_2.mp4'
    journal   download: streaming manifest -> segmented downloader
                        (http://127.0.0.1:8899/hls/scene/2.m3u8)
    history   ('2026-07-30T01:03:50', 'done', '2_2.mp4', 3498, 3498, '')

and L12 still said:

    [N/A] L12  17 completed download(s), none segmented. ... nothing in this
               history went through it, which means no stream was queued

The last clause is false, and it was false at the moment it was printed.

WHY IT CANNOT SEE IT. L12 asked for `status='done'` AND one of

    url LIKE '%.m3u8%'   url LIKE '%.mpd%'
    message LIKE '%hls%' message LIKE '%segment%' message LIKE '%ffmpeg%'

Every clause is unsatisfiable for the path #75/#77 built:

  * history records the PAGE url. `runner_transport.py:1193` passes `page_url`.
    The manifest -- the only string that would match `%.m3u8%` -- appears in the
    journal and never in the table.
  * the success db_log on that same line writes `message=""`, a literal. So no
    message clause can fire on a completed download. Where those words DO
    appear is the failure note ("segmented download failed: ..."), which the
    `status='done'` clause then excludes.

So the check's denominator structurally excludes its subject: CLAUDE.md section
0, and the shape it warns about most -- it reported a clean-sounding N/A,
truthfully as to the query and uselessly as to the question.

MY OWN ERROR, NAMED. #75 corrected L12's PROSE -- the comment still standing at
checks.py:1662-1669 explains at length why the old sentence about the generic
path having no HLS handling had become false -- and left the QUERY keyed on URL
spelling. Fixing the sentence and not the denominator is the same class of
mistake as the sentence itself.

THE FIX IS TO MAKE THE PRODUCER STATE THE FACT, which is #63's precedent
verbatim (see test_history_records_whether_bytes_were_fetched.py). Broadening
the LIKE to `%hlspage%` would key the gate on how the fixture spells a URL --
presence-not-behaviour, the pattern that survived mutation in five of this
programme's cuts. `bytes_fetched` cannot stand in either: measured on the box,
file_size == bytes_fetched == 3498, so it does not discriminate.

BY RETURN/ARGUMENT, NOT BY `self` -- also #63's reasoning, and for its reason:
runner.py:1120 starts one worker thread per slot against a SHARED runner
instance, so a `self._transfer_mode` handoff would attribute one concurrent
job's transport to another.

THE DENOMINATOR IS DERIVED, NOT LISTED. Six call sites reach hls_downloader
(AST, not grep): _do_download plus four extractors that log their own `done`
row. Row 375 closes the former _try_plugin_extractor hole by recording its own
completed HLS/HTTP rows before its bool success return. The test below derives
the paths from the AST and now pins the unrecordable set to empty, so a future
segmented path added without a marker fails rather than becoming a silent hole.

RED-first: every assertion below fails on pristine source.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SEGMENTED = "segmented"


# ── harness ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def harness():
    from live_tests import checks  # noqa: F401  (registration)
    from live_tests import harness as h
    return h


@pytest.fixture(scope="module")
def checks_mod():
    from live_tests import checks
    return checks


class _Ctx:
    """Minimal live-test context: what L12 actually touches.

    A duck type, not a Context subclass, deliberately -- the L34 fan-out found
    seven of these across the suite, and a check that reaches ctx attributes
    without getattr breaks all of them at once.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.base_url = "http://localhost:5555"
        self._log = []

    def log(self, msg):
        self._log.append(str(msg))

    def ro_db(self):
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)


def _history_db(tmp_path, rows, *, with_transfer_mode=True, name="h.db"):
    """A history table, with or without the column under test.

    `rows` are (url, status, message, transfer_mode) or (url, status, message).
    `with_transfer_mode=False` builds the pre-migration shape -- the operator's
    database before v9, and the case L12 must report as UNKNOWN rather than as
    an absence.
    """
    db = tmp_path / name
    cx = sqlite3.connect(db)
    cols = ("id INTEGER PRIMARY KEY, site_id TEXT, site_name TEXT, url TEXT, "
            "status TEXT, filename TEXT, file_size INTEGER, message TEXT")
    if with_transfer_mode:
        cols += ", transfer_mode TEXT DEFAULT NULL"
    cx.execute(f"CREATE TABLE history({cols})")
    for r in rows:
        url, status, message = r[0], r[1], r[2]
        mode = r[3] if len(r) > 3 else None
        if with_transfer_mode:
            cx.execute("INSERT INTO history(url,status,message,transfer_mode) "
                       "VALUES (?,?,?,?)", (url, status, message, mode))
        else:
            cx.execute("INSERT INTO history(url,status,message) VALUES (?,?,?)",
                       (url, status, message))
    cx.commit()
    cx.close()
    return db


@pytest.fixture()
def with_ffmpeg(checks_mod, monkeypatch):
    """L12 returns N/A without ffmpeg, which would mask every verdict below."""
    monkeypatch.setattr(checks_mod, "_ffmpeg_present", lambda: "/usr/bin/ffmpeg")


# ── the defect, at its narrowest ─────────────────────────────────────────────

def test_the_row_the_box_actually_wrote_makes_l12_pass(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """THE OBSERVED ROW. Not a constructed one -- the url, status and empty
    message are copied from the deploy host's history at 01:03:50 on
    2026-07-30, the run in which the segmented path first worked.
    """
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://127.0.0.1:8899/scene/2?bdseed=1", "done", "", "http"),
        ("http://127.0.0.1:8899/hlspage/2?bdseed=1&run=b478bf19", "done", "",
         _SEGMENTED),
    ]))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.PASS, (
        f"L12 returned {level} on the exact history row a successful segmented "
        f"download writes. Got: {detail}"
    )


def test_url_spelling_alone_does_not_make_l12_pass(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """THE FIX THIS FORBIDS.

    A done row whose URL is spelled like a manifest, and whose message says
    'hls via ffmpeg', but which records a plain HTTP transfer. The old query
    PASSed on this twice over -- once on the url, once on the message -- and
    neither string is evidence about which code path ran. Widening the LIKE to
    reach `hlspage` would double down on exactly this.
    """
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://x/stream.m3u8", "done", "hls via ffmpeg", "http"),
        ("http://x/other.mpd", "done", "segment muxing done", None),
    ]))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level != harness.PASS, (
        f"L12 PASSed on URL spelling and message prose while the recorded "
        f"transport says otherwise ({level}). The recorded fact must win over "
        f"the string. Got: {detail}"
    )


def test_a_recorded_segmented_transfer_beats_an_unrelated_url(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """The converse, and the real-world case: nothing about the URL or the
    message suggests a stream, and the transport says it was one."""
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://x/page/7", "done", "", _SEGMENTED),
    ]))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.PASS, (
        f"L12 returned {level} for a recorded segmented transfer whose URL "
        f"looks like an ordinary page -- which is what the generic scrape path "
        f"produces every time. Got: {detail}"
    )


# ── unknown is a third state ─────────────────────────────────────────────────

def test_a_database_without_the_column_is_unknown_not_absent(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """CLAUDE.md section 0's third state.

    Before migration v9 no row records its transport. That is not "no segmented
    download happened" -- it is "this database never recorded the answer", and
    a check that cannot verify must say so. Both are N/A (neither is a fault in
    the deployment), so the VERDICT cannot carry the distinction and the
    message must.
    """
    unknown = _Ctx(_history_db(tmp_path, [
        ("http://x/a.mp4", "done", ""),
    ], with_transfer_mode=False, name="pre.db"))
    absent = _Ctx(_history_db(tmp_path, [
        ("http://x/a.mp4", "done", "", "http"),
    ], name="post.db"))

    u_level, u_detail = checks_mod.l12_hls_dash_segmented_download(unknown)
    a_level, a_detail = checks_mod.l12_hls_dash_segmented_download(absent)

    assert u_level == harness.NA, (
        f"a history table with no transfer_mode column returned {u_level}")
    assert a_level == harness.NA, (
        f"a history with recorded non-segmented transfers returned {a_level}")
    assert u_detail != a_detail, (
        "L12 says the same thing about a database that never recorded the "
        "answer and one that recorded 'not segmented'. Those are different "
        "claims -- the first is unknown, the second is an observation -- and "
        "collapsing them is how the N/A came to assert something false.\n"
        f"  both: {u_detail!r}"
    )


def test_the_na_message_no_longer_asserts_that_no_stream_was_fetched(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """The specific false sentence, pinned by name.

    It printed on the box at 01:03 against a history that contained a segmented
    download. The count is a lower bound -- the plugin-extractor path logs no
    row at all -- so the absence of a marked row is not evidence that no stream
    was fetched, and the message must not say it is.
    """
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://x/a.mp4", "done", "", "http"),
    ]))
    _, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert "which means no stream was queued" not in detail, (
        f"the sentence that was false on the box is still in the message: "
        f"{detail!r}"
    )
    assert "none segmented" not in detail, (
        f"'none segmented' states a property of the downloads; what is known "
        f"is a property of the RECORDS. Got: {detail!r}"
    )


def test_l12_still_reports_nothing_to_judge_on_an_empty_history(
        checks_mod, harness, tmp_path, with_ffmpeg):
    """Regression guard: the no-downloads-at-all case must stay N/A and must
    stay distinguishable from the two above."""
    ctx = _Ctx(_history_db(tmp_path, []))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.NA, f"empty history returned {level}: {detail}"


# ── the producer: db_log, the schema, the migration ──────────────────────────

def test_db_log_accepts_and_stores_the_transport(tmp_path, monkeypatch):
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "h.db"))
    _db.db_init()
    _db.db_log("s1", "S", "u1", "done", "a.mp4", 100, "",
               transfer_mode=_SEGMENTED)
    _db.db_log("s1", "S", "u2", "done", "a.mp4", 100, "", transfer_mode="http")
    _db.db_log("s1", "S", "u3", "done", "a.mp4", 100, "")   # omitted
    with _db.db_conn() as cx:
        rows = dict(cx.execute(
            "SELECT url, transfer_mode FROM history").fetchall())
    assert rows["u1"] == _SEGMENTED
    assert rows["u2"] == "http"
    assert rows["u3"] is None, (
        "an unrecorded path must be NULL -- unknown is a third state, and a "
        "consumer must be able to tell it from 'not segmented'"
    )


def test_a_fresh_database_is_born_with_the_column(tmp_path, monkeypatch):
    """db.py's CREATE TABLE carries it too, the way honeypot_score (v7) and
    bytes_fetched (v8) do -- otherwise a brand-new install has the column only
    after apply_pending() happens to run."""
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "fresh.db"))
    _db.db_init()
    with _db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "transfer_mode" in cols, (
        f"a fresh history table has no transfer_mode. Columns: {sorted(cols)}")


def test_an_existing_history_table_gains_the_column(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` never adds a column to a live table, so the
    operator's database needs the migration or db_log's insert fails on the box
    while passing in a fresh sandbox."""
    from bulk_downloader import db as _db, migrations as _m
    p = tmp_path / "old.db"
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "site_id TEXT, site_name TEXT, url TEXT, status TEXT, "
               "filename TEXT, file_size INTEGER, message TEXT, "
               "screenshot TEXT, ts TEXT)")
    cx.commit()
    cx.close()
    monkeypatch.setattr(_db, "DB_PATH", str(p))
    _db.db_init()
    _m.apply_pending(backup_first=False)
    _m.apply_pending(backup_first=False)          # idempotent
    with _db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "transfer_mode" in cols, (
        "an existing history table did not gain transfer_mode")


def test_the_migration_does_not_guess_from_the_url(tmp_path, monkeypatch):
    """NO BACKFILL. A pre-migration done row whose URL is spelled like a
    manifest must come out NULL, not 'segmented'.

    Backfilling from URL spelling would write the very inference this cut
    exists to remove -- and would write it as though it were a recorded fact,
    which is strictly worse than leaving it unknown.
    """
    from bulk_downloader import db as _db, migrations as _m
    p = tmp_path / "old2.db"
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "site_id TEXT, site_name TEXT, url TEXT, status TEXT, "
               "filename TEXT, file_size INTEGER, message TEXT, "
               "screenshot TEXT, ts TEXT)")
    cx.execute("INSERT INTO history(url,status,message) VALUES "
               "('http://x/a.m3u8','done','hls via ffmpeg')")
    cx.commit()
    cx.close()
    monkeypatch.setattr(_db, "DB_PATH", str(p))
    _db.db_init()
    _m.apply_pending(backup_first=False)
    with _db.db_conn() as cx:
        got = cx.execute("SELECT transfer_mode FROM history").fetchone()[0]
    assert got is None, (
        f"the migration guessed transfer_mode={got!r} from the URL. A guess "
        f"recorded as a fact is worse than a NULL.")


def test_the_postgres_mirror_schema_carries_the_column():
    """pg_backend._PG_SCHEMA's own comment: "Column sets track db.py; a
    divergence here surfaces as a mirror failure"."""
    src = (ROOT / "bulk_downloader" / "pg_backend.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    schema = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_PG_SCHEMA" for t in node.targets):
            schema = ast.unparse(node)
    assert schema is not None, "_PG_SCHEMA not found"
    hist = [s for s in schema.split("CREATE TABLE") if "history(" in s]
    assert hist, "no history table in _PG_SCHEMA"
    assert "transfer_mode" in hist[0], (
        "the PG mirror's history table has no transfer_mode, so a dual-write "
        "replaying db.py's INSERT would fail against it")


# ── the writers: derived from the AST, not listed ────────────────────────────

def _module_trees():
    out = {}
    for p in sorted((ROOT / "bulk_downloader").glob("*.py")):
        out[p.name] = ast.parse(p.read_text(encoding="utf-8"))
    return out


def _functions(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _innermost(funcs, lineno):
    best = None
    for f in funcs:
        if f.lineno <= lineno <= (f.end_lineno or f.lineno):
            if best is None or f.lineno > best.lineno:
                best = f
    return best


# Row 439 (v3.66.1362): the six segmented arms no longer call
# `_hls.download(...)` themselves. They call `self._hls_download(_hls, ...)`,
# the fail-closed egress seam on TransportMixin, which resolves the VPN/proxy
# posture and only then delegates. THIS SCAN HAD TO LEARN THE NEW SEAM OR GO
# BLIND: the moment the arms moved behind the wrapper the denominator fell from
# six functions to one, and every ratchet below would have certified a
# population it could no longer see. The wrapper's own delegating call is
# excluded so the producer set stays the six arms rather than the seam.
_SEG_SEAM = "_hls_download"
_SEG_SEAM_OWNER = ("runner_transport.py", _SEG_SEAM)


def _is_segmented_call(n):
    """A Call that performs (or commissions) a segmented transfer.

    Two accepted shapes, both structural rather than textual:
      * `<name containing 'hls'>.download(...)` -- the owner module's entry
        point, still how the seam itself and any direct caller reaches ffmpeg;
      * `self._hls_download(...)` -- the fail-closed seam every arm now uses.
    """
    if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
        return False
    if (n.func.attr == "download" and isinstance(n.func.value, ast.Name)
            and "hls" in n.func.value.id):
        return True
    return (n.func.attr == _SEG_SEAM and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "self")


def _hls_download_functions():
    """Every function that performs a segmented transfer, by AST.

    AST fixes the denominator; matching call structure rather than the string
    'hls' anywhere fixes the subject -- CLAUDE.md section 1.
    """
    found = {}
    for fname, tree in _module_trees().items():
        if fname == "hls_downloader.py":
            continue
        funcs = _functions(tree)
        for n in ast.walk(tree):
            if not _is_segmented_call(n):
                continue
            owner = _innermost(funcs, n.lineno)
            if owner is None:
                continue
            if (fname, owner.name) == _SEG_SEAM_OWNER:
                continue    # the seam delegates; it is not a producer
            found.setdefault((fname, owner.name), owner)
    return found


def _done_db_logs(fn):
    out = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "db_log"):
            continue
        if len(n.args) >= 4 and isinstance(n.args[3], ast.Constant) \
                and n.args[3].value == "done":
            out.append(n)
    return out


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# Every segmented producer now records its own completion row. Keep this exact
# empty-set ratchet so a future silent path cannot join a permanent exemption.
_UNRECORDABLE = set()


def test_the_scan_finds_the_segmented_paths_at_all():
    """A denominator that cannot see its subject certifies anything. If this
    scan returns nothing, every assertion below is vacuous."""
    found = _hls_download_functions()
    assert len(found) >= 5, (
        f"the AST scan found only {len(found)} function(s) calling "
        f"hls_downloader.download: {sorted(found)}. It found six on "
        f"2026-07-30; a scan that lost them certifies nothing.")


def _other_downloaders(fn):
    """Non-segmented transfer calls in the same function."""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if nm in ("_do_direct_http_download", "_http_download", "_pw_save"):
                out.append((nm, n.lineno))
    return out


def _marks_segmented(call):
    """Does this db_log state a segmented transport?

    Either form counts, and the distinction is the whole subtlety of this cut:

      transfer_mode="segmented"   the branch has its own db_log and returns, so
                                  a literal cannot leak onto another row
      transfer_mode=transfer_mode  the function's hls and http arms CONVERGE on
                                  one db_log, so the value has to travel

    The first draft of this predicate accepted only the literal. It failed
    against correct code -- and the failure was the useful kind, because a
    literal at a converging call site is precisely the false positive that
    would make L12 PASS on a plain MP4 download. Constant-only was the wrong
    subject, not the wrong instrument.
    """
    v = _kwarg(call, "transfer_mode")
    if isinstance(v, ast.Constant):
        return v.value == _SEGMENTED
    return isinstance(v, ast.Name)


def test_every_recordable_segmented_path_records_it():
    """DERIVED, not listed. Each function that drives a segmented transfer and
    writes its own `done` row must state that on the row."""
    found = _hls_download_functions()
    unmarked = []
    for key, fn in sorted(found.items()):
        dones = _done_db_logs(fn)
        if not dones:
            continue
        if not any(_marks_segmented(c) for c in dones):
            unmarked.append(f"{key[0]}::{key[1]} (done at "
                            f"{[c.lineno for c in dones]})")
    assert not unmarked, (
        "these functions drive a segmented transfer and write a 'done' history "
        "row that does not say so, so L12 cannot count them:\n  "
        + "\n  ".join(unmarked))


def test_a_converging_path_forwards_a_variable_rather_than_a_literal():
    """THE FALSE POSITIVE THIS CUT COULD EASILY HAVE SHIPPED.

    _try_jsonapi_extractor, _try_vixen_extractor and _try_aylo_extractor each
    have an HLS arm AND a direct-MP4 arm that converge on a SINGLE `done`
    db_log. A hardcoded transfer_mode="segmented" there would stamp every
    direct-MP4 download as a stream, and L12 would then PASS on a transfer that
    never touched ffmpeg -- a worse outcome than the N/A this cut removes,
    because it reports a capability as proven on evidence that is not evidence.
    """
    offenders = []
    for key, fn in sorted(_hls_download_functions().items()):
        dones = _done_db_logs(fn)
        others = _other_downloaders(fn)
        if not dones or not others:
            continue
        if len(dones) > 1:
            # Separate rows per arm; a literal is safe and clearer. The library
            # extractor is this shape (hls=1 row returns before the hls=0 row).
            continue
        v = _kwarg(dones[0], "transfer_mode")
        if isinstance(v, ast.Constant):
            offenders.append(
                f"{key[0]}::{key[1]}:{dones[0].lineno} hardcodes "
                f"{v.value!r} while also calling "
                f"{[n for n, _ in others]}")
    assert not offenders, (
        "a single `done` row is reached by both a segmented and a "
        "non-segmented transfer, and it names one of them unconditionally:\n  "
        + "\n  ".join(offenders))


def test_the_unrecordable_path_is_exactly_the_one_we_know_about():
    """A RATCHET, not an exemption. Every segmented path must write a `done`
    row of its own; a new silent path must fail here rather than become a hole.
    """
    found = _hls_download_functions()
    silent = {k for k, fn in found.items() if not _done_db_logs(fn)}
    assert silent == _UNRECORDABLE, (
        f"the set of segmented paths that log no history row changed.\n"
        f"  now:      {sorted(silent)}\n"
        f"  expected: {sorted(_UNRECORDABLE)}\n"
        f"Each one is a segmented download L12 can never count, so the N/A "
        f"message's 'lower bound' caveat has to keep matching reality.")


def _literal_segmented_db_logs():
    """Every db_log that hardcodes transfer_mode="segmented", with its tree."""
    out = []
    for fname, tree in _module_trees().items():
        funcs = _functions(tree)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call)
                    and getattr(n.func, "id", "") == "db_log"):
                continue
            v = _kwarg(n, "transfer_mode")
            if isinstance(v, ast.Constant) and v.value == _SEGMENTED:
                out.append((fname, _innermost(funcs, n.lineno), n))
    return out


def test_nothing_claims_segmented_without_a_segmented_transfer():
    """The inverse: no db_log may label a row 'segmented' from a function that
    never reaches hls_downloader. A false positive here would make L12 PASS on
    a plain HTTP download, which is worse than the N/A it replaces."""
    seg_funcs = set(_hls_download_functions())
    liars = []
    for fname, owner, call in _literal_segmented_db_logs():
        key = (fname, owner.name if owner else "<module>")
        if key not in seg_funcs:
            liars.append(f"{key[0]}::{key[1]}:{call.lineno}")
    assert not liars, (
        "these call sites record a segmented transfer from a function that "
        f"never calls hls_downloader.download: {liars}")


def _hls_calls_in(node):
    return [n for n in ast.walk(node) if _is_segmented_call(n)]


def _innermost_branch(fn, target):
    """The innermost `if`/`else` BLOCK containing `target`, or None.

    None when the statement sits at the function's top level, and that is the
    load-bearing part. The first version fell back to `fn.body`, which made the
    assertion vacuous: `fn.body` contains the `if result.is_hls:` statement, so
    ast.walk finds the hls call from the top level and EVERY row in the function
    looks segmented-adjacent. A mutation stamping 'segmented' onto the
    direct-MP4 row of _try_library_extractor survived exactly there.

    A conditional transport can only be claimed by a literal from inside the
    conditional. At the top level it has to travel in a variable.
    """
    best = None
    best_line = -1
    for n in ast.walk(fn):
        if not isinstance(n, ast.If):
            continue
        for block in (n.body, n.orelse):
            if not block:
                continue
            lo = block[0].lineno
            hi = max((s.end_lineno or s.lineno) for s in block)
            if lo <= target.lineno <= hi and lo > best_line:
                best, best_line = block, lo
    return best


def test_a_literal_segmented_label_sits_in_the_branch_that_segments():
    """PER-BRANCH, NOT PER-FUNCTION -- the granularity the first version of the
    test above did not have.

    A mutation that stamped transfer_mode="segmented" onto an unrelated row
    inside _do_download SURVIVED, because _do_download does call
    hls_downloader and the assertion only asked about the enclosing function.
    The property is narrower: a row that hardcodes 'segmented' must sit in the
    same branch as the call that segments. Where the arms converge the value
    travels in a variable instead, which
    test_a_converging_path_forwards_a_variable_rather_than_a_literal covers.

    SCOPED TO `done` ROWS, deliberately and not silently: a needs_review row on
    a failed stream may reasonably say the transport it attempted, and L12
    counts only completions, so that is not a claim this cut needs to police.
    """
    offenders = []
    for fname, owner, call in _literal_segmented_db_logs():
        if not (len(call.args) >= 4 and isinstance(call.args[3], ast.Constant)
                and call.args[3].value == "done"):
            continue
        if owner is None:
            continue
        block = _innermost_branch(owner, call)
        if block is None:
            offenders.append(
                f"{fname}::{owner.name}:{call.lineno} hardcodes 'segmented' at "
                f"the function's top level, where it is reached whether or not "
                f"the segmented branch ran. Forward a variable instead.")
        elif not any(_hls_calls_in(s) for s in block):
            offenders.append(
                f"{fname}::{owner.name}:{call.lineno} claims 'segmented' but "
                f"nothing in its branch (from line {block[0].lineno}) calls "
                f"hls_downloader.download")
    assert not offenders, "\n  ".join(["falsely-labelled done rows:"] + offenders)


# ── the three-way chain in _do_download ──────────────────────────────────────

def _do_download_fn():
    tree = ast.parse((ROOT / "bulk_downloader" / "runner_transport.py")
                     .read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_do_download":
            return n
    return None


def _transfer_chain():
    """The `if is_stream: ... elif use_http: ... else: ...` chain."""
    fn = _do_download_fn()
    assert fn is not None, "_do_download not found"
    for n in ast.walk(fn):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "is_stream"):
            return n
    return None


def _assigned_modes(stmts):
    """transfer_mode assignments in THESE statements only.

    `.body`, not ast.walk of the whole If -- #77's lesson: after the two-ifs
    bug was fixed to an elif, the other arms became nested nodes of the first,
    so walking the outer If sees all three and the assertion stops
    distinguishing them.
    """
    out = []
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
                if any(getattr(t, "id", "") == "transfer_mode"
                       for t in n.targets):
                    out.append(n.value.value)
    return out


def test_each_arm_of_the_transfer_chain_names_its_own_transport():
    chain = _transfer_chain()
    assert chain is not None, (
        "the `if is_stream:` chain was not found in _do_download")
    stream = _assigned_modes(chain.body)
    assert _SEGMENTED in stream, (
        f"the is_stream arm does not set transfer_mode={_SEGMENTED!r}; "
        f"it sets {stream}")

    assert len(chain.orelse) == 1 and isinstance(chain.orelse[0], ast.If), (
        "the chain is no longer `if/elif/else`. v3.66.819 shipped these as two "
        "independent ifs and the stream branch's `use_http = False` selected "
        "the else, crashing on _pw_save; exactly one arm may run.")
    http_if = chain.orelse[0]
    http = _assigned_modes(http_if.body)
    browser = _assigned_modes(http_if.orelse)
    assert http and browser, (
        f"the http/browser arms do not name their transport "
        f"(http={http}, browser={browser}). An arm that records NULL is "
        f"indistinguishable from a path that never recorded anything, which "
        f"is the ambiguity this column exists to remove.")
    assert len({_SEGMENTED, http[0], browser[0]}) == 3, (
        f"the three arms must be distinguishable: "
        f"{_SEGMENTED!r}, {http[0]!r}, {browser[0]!r}")


def test_the_done_row_passes_the_chain_variable_not_a_constant():
    """The db_log at the end of the success path must forward whatever the
    chain chose. A literal there would report one transport for all three."""
    fn = _do_download_fn()
    chain = _transfer_chain()
    passed = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "db_log"):
            continue
        if not (len(n.args) >= 4 and isinstance(n.args[3], ast.Constant)
                and n.args[3].value == "done"):
            continue
        if n.lineno <= chain.lineno:      # the skip_if_exists row, above the chain
            continue
        v = _kwarg(n, "transfer_mode")
        passed.append((n.lineno, None if v is None else ast.unparse(v)))
    assert passed, "no `done` db_log below the transfer chain"
    for lineno, expr in passed:
        assert expr == "transfer_mode", (
            f"the done row at runner_transport.py:{lineno} passes "
            f"transfer_mode={expr!r}. It must forward the chain's own variable, "
            f"or one transport gets reported for all three arms.")


def test_the_skip_path_records_no_transport():
    """`skip_if_exists` writes a `done` row with bytes_fetched=0 -- nothing was
    transferred, so there is no transport to name. It must stay NULL rather
    than borrow a label, for the same reason bytes_fetched=0 and NULL are
    different states."""
    fn = _do_download_fn()
    chain = _transfer_chain()
    above = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "db_log"
             and len(n.args) >= 4 and isinstance(n.args[3], ast.Constant)
             and n.args[3].value == "done"
             and n.lineno < chain.lineno]
    assert above, (
        "the skip_if_exists `done` row above the transfer chain was not found; "
        "if it moved, this assertion is no longer pinning it")
    for c in above:
        assert _kwarg(c, "transfer_mode") is None, (
            f"the skip row at runner_transport.py:{c.lineno} names a transport "
            f"it did not perform")
