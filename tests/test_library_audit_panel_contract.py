"""The library audit panel must render the figures audit() actually returns.

Measured at v3.66.822 (HEAD 2cb520c), library_final.audit() returns:

    orphans / missing / duplicate_groups / size_drift  -> ints
    duplicate_reclaimable_gb / orphan_size_gb          -> floats
    sample_orphans / sample_missing / sample_duplicates / sample_size_drift
                                                       -> lists

frontend/src/routes/Library.tsx rendered, instead:

    {audit.data.orphans?.length ?? 0}            -> .length of an INT is
                                                   undefined, so `?? 0`
                                                   printed 0 while orphans > 0
    {audit.data.missing_from_disk?.length ?? 0}  -> `missing_from_disk` is not
                                                   a key audit() returns at
                                                   all (the key is `missing`),
                                                   so this printed 0 always
    String(audit.data.total_history ?? "-")      -> ditto for total_history,
    String(audit.data.total_disk_files ?? "-")      total_disk_files,
    String(audit.data.missing_nfo ?? "-")           missing_nfo and
    String(audit.data.missing_thumbs ?? "-")        missing_thumbs: no such
                                                   keys, permanent em dash.

Both defects fail in the REASSURING direction on the main SPA, which is the
section 0 failure mode: a panel that cannot see its subject reports clean.

These tests exercise the REAL contract -- they call audit() over a real
temp download dir and an isolated DB (1 orphan on disk, 1 history row whose
file is gone), then evaluate what the SPA source would render for that exact
payload. Presence of a literal in the .tsx proves nothing and is not asserted.

UNKNOWN IS A FAILURE: if the panel uses an expression form the model below
does not understand, the ref parser under-counts and the tests fail loudly
rather than silently certifying an unread expression.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
from __future__ import annotations

import ast
import os
import re
import tempfile
from pathlib import Path

import bulk_downloader.db as db
from bulk_downloader import library_final as lf

REPO = Path(__file__).resolve().parent.parent
PANEL = REPO / "frontend" / "src" / "routes" / "Library.tsx"
API_TYPES = REPO / "frontend" / "src" / "lib" / "api-types.ts"
HANDLER = REPO / "bulk_downloader" / "app_library.py"

# Keys the 4xx/5xx body may carry that audit() itself never returns.
_ERROR_KEYS = {"error"}

# Every audit.data.<key> reference in the SPA -- the denominator.
_ANY_REF = re.compile(r"audit\.data\.\w+")

# The expression forms this model understands, most specific first. finditer
# does not overlap, so the String(...) wrapper is consumed before the bare
# coalesce alternative can match inside it.
# Boolean literals joined the set at v3.66.957, when the contract gained the
# *_saturated flags. Widening the MODEL is the right response to a new
# legitimate form -- the alternative was writing `?? 0` as a boolean fallback
# in the panel to satisfy a parser, which is the tail wagging the dog.
_LIT = r'"[^"]*"|-?\d+(?:\.\d+)?|true|false'
_FORMS = re.compile(
    r"String\(\s*audit\.data\.(?P<skey>\w+)\s*\?\?\s*(?P<sfb>" + _LIT + r")\s*\)"
    r"|audit\.data\.(?P<lkey>\w+)\?\.length\s*\?\?\s*(?P<lfb>" + _LIT + r")"
    r"|audit\.data\.(?P<ckey>\w+)\s*\?\?\s*(?P<cfb>" + _LIT + r")"
)

_IFACE = re.compile(r"export interface LibraryAuditResult \{(.*?)\n\}", re.S)
_FIELD = re.compile(r"^\s*(\w+)\?:\s*([^;]+);", re.M)

# A brace-delimited key list in the handler docstring, e.g. "{orphans, missing}".
_DOC_KEYLIST = re.compile(r"\{(.+?)\}", re.S)
# Proof that an unlisted docstring is DELEGATING rather than merely emptied.
_DOC_DELEGATES = re.compile(r"library_final|\baudit\(\)")

_MISSING = object()


def _lit(tok):
    if tok.startswith('"'):
        return tok[1:-1]
    if tok in ("true", "false"):
        return tok == "true"
    return float(tok) if "." in tok else int(tok)


def _handler_docstring():
    """api_library_audit's docstring, via AST.

    AST, not a text search for `def api_library_audit`: a grep denominator
    would also match the string inside a comment or a test fixture, and would
    miss the function if it were ever wrapped or renamed by a decorator.
    """
    tree = ast.parse(HANDLER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "api_library_audit":
            return ast.get_docstring(node) or ""
    raise AssertionError(
        "api_library_audit not found in app_library.py -- the denominator is "
        "empty, so this test would certify nothing (UNKNOWN fails)")


def _panel_refs():
    """([(key, form, fallback)], total audit.data.<key> occurrences).

    A match only counts if it fills its ENTIRE JSX expression container --
    `{<form>}` with nothing else inside. Without that, a wrapper the model
    does not know about (`{String(...).slice(0, 1)}`) would leave the inner
    form matching, the counts reconciling, and the real rendered text
    unexamined: a denominator that excludes its subject. Anything else makes
    the ref count fall short of the occurrence count, which fails.
    """
    src = PANEL.read_text(encoding="utf-8")
    refs = []
    for m in _FORMS.finditer(src):
        if m.start() == 0 or src[m.start() - 1] != "{" or src[m.end():m.end() + 1] != "}":
            continue  # not the whole expression container -> deliberately unmodelled
        if m.group("skey"):
            refs.append((m.group("skey"), "string", _lit(m.group("sfb"))))
        elif m.group("lkey"):
            refs.append((m.group("lkey"), "length", _lit(m.group("lfb"))))
        else:
            refs.append((m.group("ckey"), "coalesce", _lit(m.group("cfb"))))
    return refs, len(_ANY_REF.findall(src))


def _render(form, key, fallback, payload):
    """What the browser would print for this expression, JS semantics."""
    v = payload.get(key, _MISSING)
    if form == "length":
        # `.length` is defined for arrays and strings; undefined otherwise,
        # and `undefined ?? fallback` yields the fallback.
        if isinstance(v, (list, str)):
            return len(v)
        return fallback
    if v is _MISSING or v is None:
        return fallback
    return v


def _truth(key, payload):
    """The figure the operator is entitled to see for this key."""
    v = payload[key]
    return len(v) if isinstance(v, list) else v


def _same(rendered, truth):
    # A bool is not a number here. float(True) is 1.0, so the numeric path
    # below would call True and 1 the same value -- which is exactly the kind
    # of quiet type conflation the `.length`-on-an-int defect was.
    if isinstance(rendered, bool) or isinstance(truth, bool):
        return isinstance(rendered, bool) and isinstance(truth, bool) \
            and rendered == truth
    # Compare numerically: JS String(0.0) is "0" where Python str(0.0) is
    # "0.0", and that formatting difference is not the subject here.
    try:
        return float(rendered) == float(truth)
    except (TypeError, ValueError):
        return str(rendered) == str(truth)


def _fixture_audit():
    """A real audit() run: 2 orphans on disk, 1 history row whose file is gone.

    The counts are deliberately ASYMMETRIC (2 != 1) so that reporting one
    figure in the other's place cannot pass, and the two orphan sizes differ
    so nothing lands in the duplicate-candidate group.

    Isolated DB via db.DB_PATH set to an absolute temp path (rung 1 of
    db._resolve_db_path), so nothing touches a real downloader_history.db.
    """
    d = tempfile.mkdtemp(prefix="audit_panel_")
    (Path(d) / "orphan.mp4").write_bytes(b"\0" * 4096)
    (Path(d) / "orphan2.mp4").write_bytes(b"\0" * 8192)
    gone = os.path.join(tempfile.mkdtemp(prefix="audit_gone_"), "vanished.mp4")
    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="audit_db_"), "queue.db")
    try:
        db.db_init()
        db.db_log(site_id="s", site_name="S", url="http://x/v",
                  status="done", filename=gone, file_size=0)
        return lf.audit(download_dir=d)
    finally:
        db.DB_PATH = saved


def test_audit_reports_orphans_and_missing_as_int_counts():
    """The contract itself, re-derived by running it."""
    rep = _fixture_audit()
    assert rep["orphans"] == 2, f"two orphans on disk; audit said {rep['orphans']!r}"
    assert rep["missing"] == 1, f"one missing file; audit said {rep['missing']!r}"
    assert rep["duplicate_groups"] == 0, (
        f"the two orphans differ in size; audit said {rep['duplicate_groups']!r}")
    for k in ("orphans", "missing"):
        assert isinstance(rep[k], int) and not isinstance(rep[k], bool), (
            f"audit()[{k!r}] is {type(rep[k]).__name__}, not an int count")
    # Every scalar figure the panel can print, pinned against the fixture it
    # was built from -- otherwise an arithmetic slip in any one of them is
    # invisible to a test that only checks the panel agrees with whatever
    # audit() said.
    expected = {"orphans": 2, "missing": 1, "duplicate_groups": 0,
                "duplicate_reclaimable_gb": 0.0, "size_drift": 0,
                "orphan_size_gb": 0.0}
    actual = {k: rep[k] for k in expected}
    assert actual == expected, f"audit() figures wrong for the fixture: {actual}"
    assert len(rep["sample_orphans"]) == 2, rep["sample_orphans"]
    assert len(rep["sample_missing"]) == 1, rep["sample_missing"]


def test_panel_only_reads_keys_audit_actually_returns():
    """No audit.data.<key> in the SPA may name a key audit() never returns."""
    rep = _fixture_audit()
    refs, total = _panel_refs()
    assert refs, "no audit.data.<key> reference parsed out of Library.tsx"
    assert len(refs) == total, (
        f"parsed {len(refs)} of {total} audit.data.<key> references in "
        f"Library.tsx -- an expression form this test cannot evaluate is "
        f"present, so the panel is UNVERIFIED (which fails)")
    phantom = sorted({k for k, _f, _fb in refs} - set(rep) - _ERROR_KEYS)
    assert not phantom, (
        f"Library.tsx renders audit keys audit() never returns: {phantom}; "
        f"audit() returns {sorted(rep)}")


def test_panel_renders_the_real_figure_for_every_audit_key():
    """For the real payload, every figure the panel prints must be the truth."""
    rep = _fixture_audit()
    refs, total = _panel_refs()
    assert len(refs) == total, (
        f"parsed {len(refs)} of {total} audit.data.<key> references in "
        f"Library.tsx -- unevaluated expression form present (UNKNOWN fails)")
    wrong = []
    for key, form, fallback in refs:
        if key not in rep:
            continue  # key-set disagreement is the other test's subject
        rendered = _render(form, key, fallback, rep)
        truth = _truth(key, rep)
        if not _same(rendered, truth):
            wrong.append({"key": key, "form": form,
                          "rendered": rendered, "truth": truth})
    assert not wrong, (
        "the audit panel prints a figure that is not the audited value: "
        + repr(wrong))


def test_api_types_declares_the_real_audit_contract():
    """The TS interface is the third statement of this contract; it must agree."""
    body = _IFACE.search(API_TYPES.read_text(encoding="utf-8"))
    assert body, "export interface LibraryAuditResult not found in api-types.ts"
    fields = _FIELD.findall(body.group(1))
    assert fields, "no fields parsed out of LibraryAuditResult (empty denominator)"
    rep = _fixture_audit()
    phantom = sorted({n for n, _t in fields} - set(rep) - _ERROR_KEYS)
    assert not phantom, (
        f"api-types.ts declares audit keys audit() never returns: {phantom}; "
        f"audit() returns {sorted(rep)}")
    mistyped = [(n, t.strip()) for n, t in fields
                if n in rep
                and t.strip().endswith("[]") != isinstance(rep[n], list)]
    assert not mistyped, (
        "api-types.ts declares an array for a scalar audit key (or vice "
        "versa) -- this is what invites `.length` on an int: " + repr(mistyped))


def test_handler_docstring_is_not_a_fourth_drifting_contract():
    """The /api/library/audit handler docstring is the FOURTH statement of a
    contract that already has three (audit(), api-types.ts, Library.tsx).

    PR #99 fixed the two consumers and left this one standing, and it is the
    likeliest origin of both: it is where `missing_from_disk`, `total_history`,
    `total_disk_files`, `missing_nfo` and `missing_thumbs` are written down as
    though audit() returned them, and where `orphans` is glossed as "files"
    when it is an int count -- the exact invitation to `.length` on an int.

    It survived PR #99 because no gate's denominator contained it:
    test_api_types_declares_the_real_audit_contract reads api-types.ts and
    test_panel_only_reads_keys_audit_actually_returns reads the .tsx. Neither
    can see a Python docstring. That is CLAUDE.md section 0 exactly, so the fix
    is to widen the denominator, not merely to rewrite the prose.

    Either the handler restates the key set CHECKABLY (bare identifiers, all
    real), or it delegates to the single source of truth and names no keys.
    A docstring that lists keys in prose this test cannot evaluate is UNKNOWN,
    and unknown fails -- otherwise a future rewrite into an unparsed form would
    silently restore the vacuum this test exists to close.
    """
    rep = _fixture_audit()
    doc = _handler_docstring()
    assert doc.strip(), "api_library_audit has no docstring at all"

    m = _DOC_KEYLIST.search(doc)
    if m is None:
        # No restatement. The handler must be visibly delegating rather than a
        # docstring somebody merely emptied -- absent-and-silent is how this
        # test would come to pass vacuously.
        assert _DOC_DELEGATES.search(doc), (
            "api_library_audit's docstring names no audit keys and does not "
            "point at library_final.audit() either, so nothing states what the "
            "route returns and nothing can be checked against it")
        return

    entries = [e.strip() for e in m.group(1).split(",") if e.strip()]
    assert entries, "empty key list in api_library_audit's docstring"
    unparsed = [e for e in entries if not re.fullmatch(r"\w+", e)]
    assert not unparsed, (
        "api_library_audit's docstring restates the audit contract with prose "
        "this test cannot evaluate, so the restatement is UNVERIFIED (which "
        "fails). Use bare key names, or delegate to library_final.audit(). "
        "Offending entries: " + repr(unparsed))

    phantom = sorted(set(entries) - set(rep) - _ERROR_KEYS)
    assert not phantom, (
        f"api_library_audit's docstring documents audit keys audit() never "
        f"returns: {phantom}; audit() returns {sorted(rep)}")


# ─── 12(c): the counts are floors, and audit() must say so ─────────────

def _audit_over(n_rows, limit, file_size=4096):
    """audit() over `n_rows` done history rows, with the window set to `limit`.

    `file_size` is load-bearing: the missing window is
    `status='done' AND filename != ''` while the drift window adds
    `AND file_size > 0`, so a fixture built with 0 populates one and not the
    other. That difference is the reason saturation cannot be one flag.
    """
    d = tempfile.mkdtemp(prefix="audit_sat_")
    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="audit_sat_db_"), "queue.db")
    try:
        db.db_init()
        for i in range(n_rows):
            db.db_log(site_id="s", site_name="S", url="http://x/%d" % i,
                      status="done",
                      filename=os.path.join(d, "gone_%d.mp4" % i),
                      file_size=file_size)
        return lf.audit(download_dir=d, limit=limit)
    finally:
        db.DB_PATH = saved


def test_audit_discloses_that_a_capped_count_is_a_floor():
    """RED before 12(c): the counts saturate silently.

    A library larger than the window makes `missing` and `size_drift` FLOORS,
    and audit() reported them beside uncapped disk-walk figures as if all four
    were totals. An operator reading 1000 could not tell it from 1000-and-more.
    """
    rep = _audit_over(n_rows=5, limit=2)
    assert rep["missing_saturated"] is True, (
        "5 rows through a 2-row window: `missing` is a floor and audit() must "
        "say so")
    assert rep["audit_row_limit"] == 2, (
        "the window in force must be disclosed, or a floor cannot be read as "
        "'2 or more'")


def test_audit_does_not_cry_saturation_on_an_unsaturated_window():
    """The over-sensitive direction. A flag that is always True is not a flag.

    Section 0: a fix for 'reports clean when blind' that simply calls every
    scan inconclusive passes the first test and destroys the tool.
    """
    rep = _audit_over(n_rows=2, limit=50)
    assert rep["missing_saturated"] is False
    assert rep["size_drift_saturated"] is False


def test_the_two_windows_saturate_independently():
    """They are NOT one population, whatever audit()'s docstring used to say.

    missing  : status='done' AND filename != ''
    drift    : status='done' AND filename != '' AND file_size > 0

    So rows recorded with no size fill the missing window and are invisible to
    the drift window. One shared flag would report the drift count as a floor
    when nothing was capped, or miss that the missing count was.
    """
    rep = _audit_over(n_rows=5, limit=2, file_size=0)
    assert rep["missing_saturated"] is True, "5 sizeless rows still fill the missing window"
    assert rep["size_drift_saturated"] is False, (
        "file_size > 0 excludes every one of those rows from the drift window, "
        "so nothing was capped there")


def test_the_missing_projection_still_returns_rows_not_the_scan_dict():
    """audit() reads the SCAN now, so nothing else pinned the projection.

    A mutation proved it: `list_missing_from_disk` could return the whole scan
    dict and this band stayed green, because audit() stopped going through it.
    Its other callers index the rows, and a dict would hand them keys.
    """
    d = tempfile.mkdtemp(prefix="audit_proj_")
    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="audit_proj_db_"), "queue.db")
    try:
        db.db_init()
        db.db_log(site_id="s", site_name="S", url="http://x/v", status="done",
                  filename=os.path.join(d, "gone.mp4"), file_size=4096)
        rows = lf.list_missing_from_disk(download_dir=d, limit=50)
        scan = lf.missing_from_disk_scan(download_dir=d, limit=50)
    finally:
        db.DB_PATH = saved
    assert isinstance(rows, list), (
        f"list_missing_from_disk returned {type(rows).__name__}, not a list -- "
        f"it is a projection of the scan's `rows`, not the scan")
    assert rows == scan["rows"]
    assert len(rows) == 1 and rows[0]["filename"].endswith("gone.mp4")
