"""bd-wacz-corpus --templates: --jobs N and --progress.

@985. Measured on the box at v3.66.984: the serial run pinned ONE core --
CPU time grew 1.00s per wall second across a 76s window (628s -> 704s) while the
read rate collapsed to 2 MB per 15s. CPU-bound with independent per-capture work
is the one shape where process-level parallelism pays, and the operator's host
has 88 vCPUs against the one the tool was using.

GRANULARITY IS PER FILE, NOT PER SITE, AND THE REASON IS ARITHMETIC. A task per
site is bounded by the largest site: `app.reptyle.com` is 62 of the box's 742
captures, so site tasks cap out near 12x however many cores exist. Per-capture
tasks have a tail of one capture.

THE TRAP THAT WOULD HAVE TAKEN THE WHOLE RUN DOWN. `build_template` raises
`SystemExit` for a wacz with no `capture.json`, and the box's corpus contains
exactly that -- five archives that could not be opened. The serial loop catches
`BaseException`. Inside a process pool, an uncaught `SystemExit` kills the WORKER
and the executor raises `BrokenProcessPool`, so five bad archives out of 742
would abort a run that the serial version completes. The catch has to live inside
the worker, and the test below drives exactly that case.

DEFAULT IS SERIAL. `--jobs` defaults to 1 so an existing invocation's behaviour
is unchanged, and `--jobs 0` means auto. Parallelism you opt into cannot
surprise a caller with 88 processes and ~10 GB of peak RSS.

IDENTICAL OUTPUT IS THE WHOLE CONTRACT. A parallel run that returns a different
answer from the serial one is not an optimisation, it is a second tool. The
tests below compare the two byte for byte rather than spot-checking fields.
"""

import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

_LOGIN = ('<input id="user-email"><input id="password">'
          '<button type="submit">Go</button>')
_TRIGGER = '<a class="download-btn" href="/dl" download>Download</a>'


def _cap(path, url, html, resolutions=None):
    net = [{"url": "https://cdn.example.org/v/%dp/x.mp4" % r, "status": 200,
            "method": "GET"} for r in (resolutions or [])]
    cap = {"url": url, "captured_at": "2026-06-29T00:00:00Z",
           "dom_log": [{"type": "full_snapshot", "html": html}],
           "network_log": net}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive/capture.json", json.dumps(cap))
        z.writestr("pages/pages.jsonl",
                   json.dumps({"format": "json-pages-1.0"}) + "\n"
                   + json.dumps({"id": "page-0", "url": url}) + "\n")
    return path


def _corpus(root, sites=4, per_site=3):
    """A corpus with several sites and uneven capture counts, so a parallel run
    has something to reorder if it is going to."""
    for s in range(sites):
        for i in range(per_site + s):
            _cap(root / ("s%d_c%d.wacz" % (s, i)),
                 "https://site%d.example.org/%d" % (s, i),
                 _LOGIN + (_TRIGGER if i % 2 == 0 else ""),
                 resolutions=[1080] if i % 2 == 0 else None)
    return root


def _run(root, *extra, expect_rc=(0, 1)):
    r = subprocess.run([sys.executable, str(TOOL), "--root", str(root),
                        "--templates", "--json", *extra],
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode in expect_rc, (
        "rc=%d\nstdout=%s\nstderr=%s" % (r.returncode, r.stdout[-800:], r.stderr[-1500:]))
    return r


def _mode(r):
    return json.loads(r.stdout)["modes"]["templates"]


def _answer(r):
    """The mode's ANSWER, with the how-it-was-computed metadata removed.

    `jobs` is deliberately different between a serial and a parallel run -- it
    reports the worker count, which is the point of asking for it. Everything
    else must be identical, so it is stripped here rather than the comparison
    being loosened to a spot-check."""
    m = _mode(r)
    m.pop("jobs", None)
    return m


def test_PARALLEL_output_is_IDENTICAL_to_serial(tmp_path):
    """The whole contract. Not 'similar', not 'same verdict counts' -- the same
    object. A parallel run that answers differently is a second tool."""
    _corpus(tmp_path)
    serial = _answer(_run(tmp_path))
    par = _answer(_run(tmp_path, "--jobs", "4"))
    assert par == serial, (
        "parallel and serial disagree.\nserial=%s\nparallel=%s"
        % (json.dumps(serial, sort_keys=True)[:900],
           json.dumps(par, sort_keys=True)[:900]))


def test_SITE_ORDER_is_deterministic_under_parallelism(tmp_path):
    """Workers finish out of order by definition. If the rows are appended as
    they land, two runs of the same corpus disagree and a diff of two reports
    becomes unreadable noise."""
    _corpus(tmp_path, sites=5, per_site=2)
    a = [s["host"] for s in _mode(_run(tmp_path, "--jobs", "4"))["sites"]]
    b = [s["host"] for s in _mode(_run(tmp_path, "--jobs", "3"))["sites"]]
    assert a == b == sorted(a), (
        "site order is not stable across worker counts: %r vs %r" % (a, b))


def test_a_SYSTEM_EXITING_capture_does_not_kill_the_POOL(tmp_path):
    """THE LOAD-BEARING TEST, and it is about the box's real corpus.

    `build_template` raises SystemExit for a wacz with no capture.json. The box
    has five such archives out of 742. In a process pool an uncaught SystemExit
    kills the worker and the executor raises BrokenProcessPool, so those five
    would abort a run the serial version completes -- worse than slow, and it
    would only appear on real data.
    """
    _corpus(tmp_path, sites=2, per_site=2)
    # No capture.json: the exact shape that raises SystemExit inside the builder.
    with zipfile.ZipFile(tmp_path / "nocapture.wacz", "w") as z:
        z.writestr("pages/pages.jsonl",
                   json.dumps({"format": "json-pages-1.0"}) + "\n"
                   + json.dumps({"id": "page-0",
                                 "url": "https://lone.example.org/x"}) + "\n")
    (tmp_path / "truncated.wacz").write_bytes(b"PK\x03\x04 not a zip")

    par = _answer(_run(tmp_path, "--jobs", "4"))
    serial = _answer(_run(tmp_path))
    assert par == serial, "the pool answered differently on the bad archives"
    assert par["unbuildable_captures"] >= 2, (
        "the unbuildable archives were not counted: %r" % par["unbuildable_captures"])


def test_JOBS_defaults_to_SERIAL_so_an_existing_call_is_unchanged(tmp_path):
    """Opt-in. A default of `auto` would spawn 88 processes and ~10 GB of peak
    RSS under a caller that asked for none of it."""
    _corpus(tmp_path, sites=2, per_site=2)
    plain = _mode(_run(tmp_path))
    one = _mode(_run(tmp_path, "--jobs", "1"))
    assert plain == one, "the default is not --jobs 1"
    assert plain["jobs"] == 1, (
        "the mode does not report the worker count it used: %r" % plain.get("jobs"))


def test_JOBS_ZERO_means_AUTO_and_the_run_REPORTS_what_it_used(tmp_path):
    """A run whose parallelism is invisible cannot be compared against another
    run's wall time, which is the only reason anyone reaches for --jobs."""
    _corpus(tmp_path, sites=2, per_site=2)
    auto = _mode(_run(tmp_path, "--jobs", "0"))
    import os
    assert auto["jobs"] == max(1, os.cpu_count() or 1), (
        "--jobs 0 did not resolve to one worker per core: %r" % auto["jobs"])
    assert _answer(_run(tmp_path)) == {k: v for k, v in auto.items() if k != "jobs"}, (
        "auto-parallel disagreed with serial")


def test_PROGRESS_goes_to_STDERR_and_never_pollutes_the_JSON(tmp_path):
    """`--json` on stdout is machine-read. A progress line on stdout would make
    every consumer's parse fail, which is a worse defect than having no progress
    at all."""
    _corpus(tmp_path, sites=2, per_site=2)
    r = _run(tmp_path, "--jobs", "2", "--progress")
    json.loads(r.stdout)          # must still parse -- the real assertion
    assert r.stderr.strip(), "--progress produced no progress output at all"
    assert "/" in r.stderr, (
        "progress does not carry a denominator -- a bare count is the thing "
        "CLAUDE.md section 1 forbids: %r" % r.stderr[:300])


def test_PROGRESS_is_SILENT_unless_asked(tmp_path):
    """The other direction. A tool that always chatters on stderr breaks a
    caller that treats stderr as an error channel."""
    _corpus(tmp_path, sites=2, per_site=2)
    # BOTH lanes. An earlier version tested only --jobs 2, where the call site
    # is separately guarded, so a mutant deleting the guard inside _progress
    # itself escaped a green band. The serial path calls it unconditionally and
    # is the one that exercises the guard.
    for jobs in ("1", "2"):
        r = _run(tmp_path, "--jobs", jobs)
        assert not r.stderr.strip(), (
            "stderr was written without --progress at --jobs %s: %r"
            % (jobs, r.stderr[:300]))


def test_REASSEMBLY_restores_index_order_from_ANY_completion_order():
    """Closing a mutation escape. `ProcessPoolExecutor.map` yields in input
    order today, so indexing by completion position is currently identical and
    the mutant escaped. Driven directly with a SHUFFLED stream, which is what
    `as_completed` would hand it, the index is load-bearing again."""
    import importlib.machinery
    import importlib.util
    ld = importlib.machinery.SourceFileLoader("bd_wacz_corpus_par", str(TOOL))
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(ld.name, ld))
    ld.exec_module(m)
    shuffled = [(3, "d"), (0, "a"), (2, "c"), (1, "b")]
    assert m._reassemble(4, shuffled) == ["a", "b", "c", "d"], (
        "results were placed by completion order rather than by index")
    assert m._reassemble(2, [(1, "y"), (0, "x")]) == ["x", "y"]


def test_an_EMPTY_corpus_is_still_UNKNOWN_under_parallelism(tmp_path):
    """The @973 invariant must survive the change: nothing examined is never a
    pass, however many workers examined it."""
    r = _run(tmp_path, "--jobs", "4", expect_rc=(2,))
    assert json.loads(r.stdout)["status"] == "unknown"
