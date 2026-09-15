"""Row 787: find_best_download's nothing-in-scope result must not LOOK FOUND
at any caller.

`detect.find_best_download` returns None, a candidate dict, or the row-701
nothing-in-scope sentinel (`detect._NoInScopeCandidates`).  The sentinel is
falsy on purpose, and four of the five callers decided `if not best:` and
nothing else -- so the whole guarantee that a nothing-in-scope page is not
reported as a find rested on one dunder method nobody else pinned.  A truthy
"empty-ish" sentinel (one refactor away: a plain dict, a dataclass, a
`__bool__` dropped as dead code) walks straight through those guards and the
caller then reads `locator=None` and `_learned_sel=""`: the teach verify
answers ("ok", match_text="") and deep-detect hands the sentinel back tagged
`_via_deep_detect` -- it looks found.

Acceptance (register row 787 at c9dd4176):
  1. the nothing-in-scope result is falsy, OR keys every field a caller is
     proven to read     -> test_the_sentinel_is_falsy_and_keys_every_subscripted_read
  2. a tree-derived caller census at check time
                          -> test_the_caller_census_is_derived_from_the_tree_and_exact
  3. the gate rejects a truthiness-only decision on the result
                          -> test_no_caller_decides_on_truthiness_alone (RED on c9dd4176: four)
  4. a genuine candidate is accepted everywhere (control)
                          -> the *_accepts_a_genuine_candidate arms below
The fix is one keyed predicate, `detect.no_selection`, used by the four
callers; runner.py already decides by key through `_handle_nothing_in_scope`
before its own `if not best:` guards (row 701 pins that order).

Static census: every `find_best_download(...)` call under bulk_downloader/ is
found by AST at check time, its bound name followed to the FIRST statement
that reads it, and that decision classified: keyed (reads
`_no_in_scope_candidates`, directly or through `no_selection` /
`_handle_nothing_in_scope`), truthiness-only, or read-before-decide.  Nothing
here drives a browser: the callers are exercised with find_best_download
patched to return (a) a TRUTHY copy of the real sentinel -- the hazard shape,
proven truthy before any verdict -- and (b) a genuine candidate.
"""
from __future__ import annotations

import ast
import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

BD_GATE_SCOPE = "repo-wide"
# CI-SHARD-CLAIM row-787 application-safety tests/test_row787_sentinel_must_not_look_found.py

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "bulk_downloader"
_SUBJECT = "find_best_download"
_KEY = "_no_in_scope_candidates"
_KEYED_HELPERS = frozenset({"no_selection", "_handle_nothing_in_scope"})

KEYED = "keyed"
TRUTHINESS_ONLY = "truthiness-only"
READ_BEFORE_DECIDE = "read-before-decide"
UNBOUND = "result-not-bound"

# The census this tree is expected to produce.  A sixth caller, or one that
# moved, is a finding the gate names -- it is not silently admitted.
_EXPECTED_CALLERS = {
    ("bulk_downloader/auto_detect.py", "_detect_download_live"): 1,
    ("bulk_downloader/runner.py", "SiteRunner._process_one"): 1,
    ("bulk_downloader/runner_extractors.py",
     "ExtractorsMixin._try_deep_detect_fallback"): 1,
    ("bulk_downloader/runner_manual.py", "_ManualDownloadSession._run"): 2,
}


# ══ the tree-derived census ═════════════════════════════════════════════
def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _enclosing_stmt(node, parents):
    while node is not None and not isinstance(node, ast.stmt):
        node = parents.get(node)
    return node


def _qualname(stmt, parents):
    parts = []
    node = parents.get(stmt)
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            parts.append(node.name)
        node = parents.get(node)
    return ".".join(reversed(parts)) or "<module>"


def _statements_after(stmt, parents):
    """The statements that run after `stmt`, in order, walking outwards
    through try/if/loop bodies until the enclosing function ends."""
    node = stmt
    while True:
        parent = parents.get(node)
        if parent is None or isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            if parent is not None:
                for field, value in ast.iter_fields(parent):
                    if isinstance(value, list) and node in value:
                        yield from value[value.index(node) + 1:]
            return
        for field, value in ast.iter_fields(parent):
            if isinstance(value, list) and node in value:
                yield from value[value.index(node) + 1:]
                break
        node = parent


def _reads(name, node):
    return sorted(
        (n for n in ast.walk(node)
         if isinstance(n, ast.Name) and n.id == name
         and isinstance(n.ctx, ast.Load)),
        key=lambda n: (n.lineno, n.col_offset))


def _is_keyed(test, name):
    """Does this decision read the nothing-in-scope KEY (directly or via one
    of the keyed helpers) rather than only the result's truthiness?"""
    for n in ast.walk(test):
        if isinstance(n, ast.Call):
            fn = _call_name(n)
            if fn in _KEYED_HELPERS and any(
                    isinstance(a, ast.Name) and a.id == name for a in n.args):
                return True
            if (fn == "get" and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == name and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == _KEY):
                return True
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == name
                and isinstance(n.slice, ast.Constant)
                and n.slice.value == _KEY):
            return True
        if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant)
                and n.left.value == _KEY
                and any(isinstance(c, ast.Name) and c.id == name
                        for c in n.comparators)):
            return True
    return False


def _classify(call, parents, lines):
    stmt = _enclosing_stmt(call, parents)
    bound = None
    if (isinstance(stmt, ast.Assign) and stmt.value is call
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)):
        bound = stmt.targets[0].id
    record = {"line": call.lineno, "qualname": _qualname(stmt, parents),
              "bound": bound, "decision_line": None, "decision": UNBOUND,
              "subscripted_keys": set(), "got_keys": set()}
    if bound is None:
        return record
    # Every key the caller reads off the result, anywhere in its function.
    func = stmt
    while func is not None and not isinstance(
            func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        func = parents.get(func)
    for n in ast.walk(func or stmt):
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == bound and isinstance(n.ctx, ast.Load)
                and isinstance(n.slice, ast.Constant)):
            record["subscripted_keys"].add(n.slice.value)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == bound and n.args
                and isinstance(n.args[0], ast.Constant)):
            record["got_keys"].add(n.args[0].value)
    # The FIRST statement after the call that reads the bound name decides
    # what the result is.  It must be an `if` whose test reads the key.
    for later in _statements_after(stmt, parents):
        reads = _reads(bound, later)
        if not reads:
            continue
        first = reads[0]
        record["decision_line"] = first.lineno
        holder = first
        while holder is not None and not isinstance(holder, ast.stmt):
            holder = parents.get(holder)
        in_test = (isinstance(holder, (ast.If, ast.While))
                   and any(n is first for n in ast.walk(holder.test)))
        if not in_test:
            record["decision"] = READ_BEFORE_DECIDE
        elif _is_keyed(holder.test, bound):
            record["decision"] = KEYED
        else:
            record["decision"] = TRUTHINESS_ONLY
        record["decision_src"] = lines[first.lineno - 1].strip()
        return record
    record["decision"] = READ_BEFORE_DECIDE
    record["decision_src"] = "(result is never read)"
    return record


def census_of(source: str, relpath: str):
    """Every `find_best_download(...)` call in one module, classified."""
    tree = ast.parse(source, filename=relpath)
    parents = _parents(tree)
    lines = source.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == _SUBJECT:
            rec = _classify(node, parents, lines)
            rec["path"] = relpath
            out.append(rec)
    return sorted(out, key=lambda r: (r["path"], r["line"]))


def census_of_tree(pkg: Path = _PKG):
    """The census of the whole package, derived from the filesystem at check
    time -- never from a handed list."""
    out = []
    for py in sorted(pkg.glob("*.py")):
        out.append((py, census_of(py.read_text(encoding="utf-8"),
                                  py.relative_to(_REPO).as_posix())))
    return out


def _describe(rec):
    return (f"{rec['path']}:{rec['decision_line'] or rec['line']} "
            f"[{rec['qualname']}] {rec['decision']}: "
            f"`{rec.get('decision_src', '')}`")


# ══ 2. the census is tree-derived and exact ═════════════════════════════
def test_the_caller_census_is_derived_from_the_tree_and_exact():
    per_file = census_of_tree()
    scanned = [py for py, _ in per_file]
    assert len(scanned) > 20, "the package scan found almost nothing: %r" % scanned
    callers = [r for _, recs in per_file for r in recs]
    assert len(callers) > 0, "the census found no caller: it cannot say yes"
    found = {}
    for r in callers:
        found[(r["path"], r["qualname"])] = found.get(
            (r["path"], r["qualname"]), 0) + 1
    assert found == _EXPECTED_CALLERS, (
        "find_best_download's caller census moved -- a new, moved or removed "
        "caller must be classified here, not admitted silently:\n  found    "
        f"{sorted(found.items())}\n  expected {sorted(_EXPECTED_CALLERS.items())}")
    assert len(callers) == sum(_EXPECTED_CALLERS.values()) == 5
    assert all(r["bound"] == "best" for r in callers), [
        _describe(r) for r in callers if r["bound"] != "best"]


# ══ 3. the gate rejects a truthiness-only decision ══════════════════════
def test_no_caller_decides_on_truthiness_alone():
    callers = [r for _, recs in census_of_tree() for r in recs]
    assert len(callers) == 5, "census moved; see the census test"
    offenders = [_describe(r) for r in callers if r["decision"] != KEYED]
    assert offenders == [], (
        "%d caller(s) decide what find_best_download returned on truthiness "
        "alone (or read it before deciding): a truthy nothing-in-scope "
        "result LOOKS FOUND there. Decide by the key -- detect.no_selection "
        "-- not by `if not best:`:\n  " % len(offenders)
        + "\n  ".join(offenders))


# ══ 1. the sentinel: falsy AND keys every subscripted read ══════════════
def test_the_sentinel_is_falsy_and_keys_every_subscripted_read():
    from bulk_downloader import detect
    sentinel = detect._no_in_scope_result([])
    assert sentinel.get(_KEY) is True
    assert not sentinel, "the nothing-in-scope result must be FALSY"
    callers = [r for _, recs in census_of_tree() for r in recs]
    subscripted = set().union(*(r["subscripted_keys"] for r in callers))
    got = set().union(*(r["got_keys"] for r in callers))
    assert len(subscripted) >= 2 and "locator" in subscripted, (
        "the key census found no `best[...]` read: it cannot say yes: %r"
        % subscripted)
    missing = sorted(k for k in subscripted if k not in sentinel)
    assert missing == [], (
        "a caller subscripts the result for a key the nothing-in-scope "
        "sentinel does not carry -- KeyError waiting behind a truthy "
        f"sentinel: {missing}")
    # Reads through .get() are keyed by name; record that the census saw the
    # two the row names, so the hazard it describes is the one measured.
    assert {"_learned_sel", "locator"} <= (got | subscripted)


# ══ negative control: the classifier can say no, and says why ═══════════
_CONTROL_MODULE = '''
from .detect import find_best_download, no_selection

def a(page):
    best = find_best_download(page)
    if not best:
        return None
    return best["locator"]

def b(page):
    try:
        best = find_best_download(page, custom="")
    except Exception:
        return None
    if no_selection(best):
        return None
    return best.get("_learned_sel")

def c(page):
    best = find_best_download(page)
    loc = best["locator"]
    if not best:
        return None
    return loc

def d(page):
    best = find_best_download(page)
    if best is None or not best.get("_no_in_scope_candidates"):
        return best
    return None
'''


def test_the_classifier_rejects_a_truthiness_only_decision_for_that_reason():
    recs = census_of(_CONTROL_MODULE, "control.py")
    assert len(recs) == 4, "the control fixture did not build four call sites"
    by = {r["qualname"]: r for r in recs}
    assert by["a"]["decision"] == TRUTHINESS_ONLY
    assert by["a"]["decision_src"] == "if not best:"
    assert by["b"]["decision"] == KEYED, _describe(by["b"])
    assert by["c"]["decision"] == READ_BEFORE_DECIDE, _describe(by["c"])
    assert by["d"]["decision"] == KEYED, _describe(by["d"])
    rejected = [r for r in recs if r["decision"] != KEYED]
    assert len(rejected) == 2 and {r["qualname"] for r in rejected} == {"a", "c"}
    assert by["a"]["subscripted_keys"] == {"locator"}
    assert by["b"]["got_keys"] == {"_learned_sel"}
    # And the decision is found through a try: body, as the real callers do.
    assert by["b"]["decision_line"] == by["b"]["line"] + 3


def test_a_result_that_is_never_bound_is_rejected_too():
    recs = census_of("def e(p):\n    return find_best_download(p)\n", "e.py")
    assert len(recs) == 1 and recs[0]["decision"] == UNBOUND


# ══ the keyed predicate ═════════════════════════════════════════════════
def _truthy_sentinel():
    """The hazard shape: the real sentinel's content, without its
    __bool__.  Proven truthy before any verdict rests on it."""
    from bulk_downloader import detect
    real = detect._no_in_scope_result(
        [{"reason": "foreign", "score": 1080, "text": "1080p"}])
    assert not real
    hazard = dict(real)
    assert hazard and hazard[_KEY] is True and hazard["locator"] is None
    return hazard


def _genuine():
    loc = MagicMock(name="locator")
    loc.get_attribute.return_value = None
    loc.evaluate.return_value = {
        "tag": "a", "id": "download-1080p", "classes": [],
        "url_attribute": "href", "attrs": {"href": "/dl/1080.mp4"}}
    return {"locator": loc, "text": "1080p", "score": 1080, "size": 0,
            "_via_learned": True, "_learned_sel": "a.dl", "_all_candidates": []}


def test_no_selection_decides_by_key_not_truthiness():
    from bulk_downloader.detect import no_selection, _no_in_scope_result
    assert no_selection(None) is True
    assert no_selection({}) is True
    assert no_selection(_no_in_scope_result([])) is True
    assert no_selection(_truthy_sentinel()) is True, (
        "a TRUTHY nothing-in-scope result must still be refused")
    assert no_selection(_genuine()) is False, "a genuine candidate is a find"
    assert no_selection({"score": 1080, "locator": object()}) is False
    assert no_selection({_KEY: False, "score": 240}) is False
    # A truthy non-mapping is not the sentinel: it is a find the caller will
    # read and fail loudly on, exactly as before this row.
    assert no_selection(object()) is False


# ══ every caller, driven with the hazard shape and with a real find ═════
def test_auto_detect_refuses_a_truthy_sentinel_and_accepts_a_genuine_candidate():
    from bulk_downloader import auto_detect
    page = MagicMock(name="page")
    warnings = []
    with patch("bulk_downloader.detect.find_best_download",
               return_value=_truthy_sentinel()) as fbd:
        out = auto_detect._detect_download_live(
            page, quality_pref="", warnings=warnings)
    assert fbd.call_count == 1
    assert out is None
    assert warnings == ["no download candidate found by find_best_download"], (
        "a truthy sentinel walked past the decision and was read as a "
        f"candidate: {warnings}")
    # Control: a genuine candidate is accepted and synthesised.
    warnings = []
    with patch("bulk_downloader.detect.find_best_download",
               return_value=_genuine()):
        out = auto_detect._detect_download_live(
            page, quality_pref="", warnings=warnings)
    assert out is not None and out["row_selectors"][0] == "#download-1080p", (
        out, warnings)


def _deep_detect_runner():
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "row787-site"
    r.config = {"deep_detect_fallback": True, "runner_use_live_dd": False}
    return r


def _deep_detect_module():
    # `from . import deep_detect` reads the PACKAGE ATTRIBUTE, which exists
    # only once the real module has been imported; bind it, then patch it.
    import bulk_downloader.deep_detect  # noqa: F401
    cand = {"source_type": "resolution_download_card",
            "url": "https://x.test/dl/1080.mp4",
            "click_selector": "div.dl-card a", "quality_label": "1080p",
            "score": 145}
    m = MagicMock()
    m.deep_detect = lambda html, base_url="", **_kw: {
        "download_candidates": [cand], "blockers": {}}
    return m


def test_deep_detect_retry_refuses_a_truthy_sentinel_and_accepts_a_genuine_candidate():
    page = MagicMock(name="page")
    page.url = "https://x.test/p/1"
    page.content.return_value = "<html><body>" + "x" * 500 + "</body></html>"
    with patch("bulk_downloader.deep_detect", _deep_detect_module()), \
         patch("bulk_downloader.runner_extractors.find_best_download",
               return_value=_truthy_sentinel()) as fbd:
        out = _deep_detect_runner()._try_deep_detect_fallback(
            page, page.url, {})
    assert fbd.call_count == 1, "the retry never reached find_best_download"
    assert out is None, (
        "deep-detect handed a nothing-in-scope result back as a rescue: %r"
        % out)
    with patch("bulk_downloader.deep_detect", _deep_detect_module()), \
         patch("bulk_downloader.runner_extractors.find_best_download",
               return_value=_genuine()):
        out = _deep_detect_runner()._try_deep_detect_fallback(
            page, page.url, {})
    assert out is not None and out.get("_via_deep_detect") is True


class _FakeCtx:
    def __init__(self, page):
        self.pages = [page]

    def cookies(self):
        return []

    def close(self):
        pass


def _manual_session():
    """A _ManualDownloadSession whose owner thread runs the real command
    loop over a fake context -- no Playwright, no browser."""
    from bulk_downloader.runner_manual import _ManualDownloadSession
    page = MagicMock(name="live_page")
    page.url = "https://x.test/scene/1"
    s = _ManualDownloadSession.__new__(_ManualDownloadSession)
    s._runner = MagicMock(name="runner")
    s._runner.site_id = "row787-site"
    s._runner.config = {}
    s.target_url = page.url
    s._teach_base_url = ""
    s._cmd_q = queue.Queue()
    s._error = None
    s._ready = threading.Event()
    s._closed = threading.Event()
    s._launch = lambda: (None, _FakeCtx(page), page, None)
    s._thread = threading.Thread(target=s._run, daemon=True,
                                 name="manual-dl-row787")
    s._thread.start()
    assert s._ready.wait(timeout=10) and s._error is None, s._error
    return s


def _drive(result, cmd, picks):
    s = _manual_session()
    try:
        with patch("bulk_downloader.detect.find_best_download",
                   return_value=result) as fbd:
            answer = s._send(cmd, picks, timeout=15)
        assert fbd.call_count == 1, f"{cmd} never reached find_best_download"
    finally:
        s._send("cancel", timeout=5)
        s._thread.join(timeout=10)
    return answer


_PICKS = {"row_selectors": ["a.dl"], "url_attribute": "href"}


def test_teach_verify_refuses_a_truthy_sentinel_and_accepts_a_genuine_candidate():
    answer = _drive(_truthy_sentinel(), "verify", _PICKS)
    assert answer == ("err",
                      "No element matched the picked selectors on this page"), (
        "verify LOOKED FOUND on a nothing-in-scope result: %r" % (answer,))
    answer = _drive(_genuine(), "verify", _PICKS)
    assert answer[0] == "ok" and answer[1]["match_text"] == "1080p", answer


def test_teach_test_download_refuses_a_truthy_sentinel_and_accepts_a_genuine_candidate():
    answer = _drive(_truthy_sentinel(), "test_download", _PICKS)
    assert answer == ("err", "No element matched the picked selectors"), (
        "test_download LOOKED FOUND on a nothing-in-scope result: %r"
        % (answer,))
    answer = _drive(_genuine(), "test_download", _PICKS)
    assert answer[0] == "ok" and answer[1]["kind"] == "click_and_capture", answer
    assert answer[1]["match_text"] == "1080p"


def test_the_runner_caller_still_decides_by_key_before_its_guards():
    """runner.py is the fifth caller: it is keyed through
    _handle_nothing_in_scope (row 701), and the census must see that."""
    callers = [r for _, recs in census_of_tree() for r in recs
               if r["path"] == "bulk_downloader/runner.py"]
    assert len(callers) == 1
    assert callers[0]["decision"] == KEYED, _describe(callers[0])
    assert "_handle_nothing_in_scope" in callers[0]["decision_src"]
