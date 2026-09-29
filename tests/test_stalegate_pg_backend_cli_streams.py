"""Stale-gate triage (main 41a70358): pg_backend.main without print(), same bytes on the same streams.

test_v3_43_78_static_analysis_fixes F1 forbids print() in library modules and failed on main 41a70358 because
pg_backend.main printed its CLI output. The JSON result is this CLI's STDOUT contract (row127 preflight and soak
tests read it with capsys; operators pipe it), so it must not move to stderr. This pins every exit of main() to
the exact bytes and stream the print() version produced -- JSON + newline on stdout, usage + newline on stderr.
Passes before and after the rewrite; the RED for the row is the F1 sweep itself.
"""

from __future__ import annotations

import json

import pytest

from bulk_downloader import pg_backend

BD_GATE_SCOPE = "module"

RES = {"b": {"missing": []}, "a": {"missing": [], "rows": 3}}


def _run(capsys, argv):
    rc = pg_backend.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


@pytest.mark.parametrize(
    ("argv", "fn", "result", "rc"),
    [
        (["backfill", "t1"], "backfill", RES, 0),
        (["parity"], "schema_parity", RES, 0),
        (
            ["preflight", "--health", "http://127.0.0.1:9/h"],
            "preflight_cutover",
            {"ok": True},
            0,
        ),
        (
            ["soak-receipt", "--health", "http://127.0.0.1:9/h", "--log", "L.tsv"],
            "soak_receipt",
            {"ok": False},
            1,
        ),
    ],
)
def test_json_result_is_stdout_exactly(monkeypatch, capsys, argv, fn, result, rc):
    monkeypatch.setattr(pg_backend, fn, lambda *a, **k: result)
    got_rc, out, err = _run(capsys, argv)
    assert (got_rc, err) == (rc, "")
    assert out == json.dumps(result, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize(
    ("argv", "usage"),
    [
        (
            ["soak-receipt", "--health"],
            "usage: soak-receipt --health URL --log PG-SOAK-LOG.tsv [--out DIR]\n",
        ),
        (["preflight"], "usage: preflight --health URL\n"),
        (
            ["bogus"],
            (
                "usage: python -m bulk_downloader.pg_backend {backfill [table ...]|parity|preflight --health URL"
                "|soak-receipt --health URL --log PG-SOAK-LOG.tsv [--out DIR]}\n"
            ),
        ),
    ],
)
def test_usage_is_stderr_exactly(capsys, argv, usage):
    assert _run(capsys, argv) == (2, "", usage)
