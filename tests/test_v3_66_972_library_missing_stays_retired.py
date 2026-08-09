"""`/api/library/missing` and `library_missing()` are retired. Keep them gone.

@972, item 12's second half. The endpoint had ZERO callers anywhere in the tree
-- no SPA route, no command palette entry, no extension, no test. It was the
fourth producer answering to the word "missing", and the only one nothing read.
Its unreported `LIMIT 500` was the same saturation class 12(c) fixed for
`audit()`, but adding a saturation flag to an endpoint nobody calls is the wrong
repair; removing the producer is.

WHY A COMMENT-STRIPPED DENOMINATOR, stated because the naive version of this
test would have failed on the same commit that fixed it: `app.py` carried two
comments naming `/api/library/missing` and `api_library_missing` -- a route
index and an extraction pointer. Both were removed here. But CLAUDE.md section 0
records FOUR cases in one session where an assertion could not tell prose from
code, including one where "the comment explaining why RELEASE_WORK is unprefixed
spelled the prefixed name in order to say it had been removed", which put the
name straight back into the ledger and failed the gate the rename had just
fixed. CI caught that one, not review.

So this gate reads code only. A future comment explaining WHY this endpoint was
retired -- exactly the comment someone will want to write -- must not be able to
resurrect it.
"""

import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
_ROUTE = "/api/library/missing"
_VIEW = "api_library_missing"
_FUNC = "library_missing"


def _tracked(*globs):
    out = subprocess.run(["git", "ls-files", "-z", "--", *globs],
                         cwd=str(REPO), capture_output=True, text=True)
    return [f for f in out.stdout.split("\0") if f]


def _code_only(path):
    """Source with comments and docstrings removed, via tokenize-free AST rebuild.

    ast.unparse drops comments for free and normalises docstrings out when we
    strip them explicitly, so what survives is what the interpreter would run.
    Falls back to the raw text if the file will not parse -- a parse failure is
    a different test's problem and must not silently empty this denominator.
    """
    import io
    import tokenize
    src = (REPO / path).read_text(encoding="utf-8", errors="replace")
    try:
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and tok.line.strip().startswith(
                    (tok.string[:1], 'r"', "r'", 'f"', "f'")):
                # keep strings: a string literal naming the route WOULD be a
                # live reference (a url_for target, a fetch path), unlike a
                # comment. Only comments are prose here.
                pass
            out.append(tok.string)
        return " ".join(out)
    except Exception:
        return src


def test_the_route_and_its_view_are_gone_from_the_blueprint():
    src = (REPO / "bulk_downloader" / "app_library.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert _VIEW not in names, (
        "%s() is still defined in app_library.py -- the retired view came back"
        % _VIEW)
    # the decorator string is a LIVE reference, so it is checked on real source
    assert _ROUTE not in src, (
        "%s is still registered in app_library.py" % _ROUTE)


def test_the_query_helper_is_gone_from_the_library_module():
    tree = ast.parse((REPO / "bulk_downloader" / "library.py").read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert _FUNC not in names, (
        "library.%s() is still defined -- it was the only caller-less capped "
        "list producer and the point of the cut was to remove it, not rename it"
        % _FUNC)


def test_no_TRACKED_SOURCE_reintroduces_it():
    """The ratchet. Comment-stripped, so prose explaining the removal is safe."""
    files = _tracked("*.py", "*.sh")
    assert len(files) > 100, (
        "BD-GATE-UNRUNNABLE: git ls-files returned %d files -- the denominator "
        "collapsed and a pass would mean nothing" % len(files))
    hits = []
    for f in files:
        if f == "tests/" + pathlib.Path(__file__).name:
            continue          # this file names them in order to forbid them
        code = _code_only(f)
        if _ROUTE in code or _VIEW in code:
            hits.append(f)
    assert not hits, (
        "the retired endpoint reappeared in tracked source (comments excluded, "
        "so these are live references): %r" % hits)


def test_the_SURVIVING_missing_producers_are_untouched():
    """Over-sensitivity guard: three producers stay, one goes.

    A cut that removed the word everywhere would satisfy every test above and
    delete working features. `_collect_library_data` drives the widget,
    `missing_from_disk_scan` drives the Library audit line, `find_missing_metadata`
    drives the command palette -- all three must survive.
    """
    survivors = {
        "bulk_downloader/app_widgets_api.py": "_collect_library_data",
        "bulk_downloader/library_final.py": "missing_from_disk_scan",
    }
    for path, fn in survivors.items():
        p = REPO / path
        assert p.is_file(), "BD-GATE-UNRUNNABLE: %s is missing" % path
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert fn in names, (
            "%s() vanished from %s -- the retirement took a LIVE producer with "
            "it. Three of the four 'missing' producers are on screen." % (fn, path))
