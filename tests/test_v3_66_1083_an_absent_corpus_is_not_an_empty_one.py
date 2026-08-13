"""v3.66.1083 -- backlog 89: an ABSENT capture corpus reported as an EMPTY one.

THE DEFECT, measured in production. Two hosts silently had zero files under
their capture roots after a rebuild that did not restore them, and the analytics
surface reported "an empty store" -- the same words it uses for a store that
exists and holds nothing. `deploy.sh` does not restore the corpus (it is
gitignored data, not code) and a host rebuild does not carry it, so the absent
case is the COMMON one, not the exotic one.

The mechanism is one line: `scan_captures` does `if not ddir.is_dir(): continue`,
so a root that is not there contributes nothing and is indistinguishable from a
root that is there and empty. `total: 0` for both.

UNKNOWN IS A THIRD STATE, and this is the shape CLAUDE.md section 0 is entirely
about: a count over a denominator that structurally excludes its subject reports
clean -- truthfully, and uselessly. "Zero captures" is a fact about the corpus;
"no corpus" is a fact about the machine, and only the second one is an incident.

WHAT THIS DOES NOT DO: restore anything. Backlog 89 asks for the corpus to
survive a rebuild, and the durable half of that is an operator backup decision,
not something a walk can invent. What is fixed here is the silence -- the state
is now reported, so the next occurrence is visible in the first place someone
looks instead of being read as an empty store.
"""
from __future__ import annotations

from pathlib import Path

# Its subject is one module's reporting contract, not an invariant over the tree.
BD_GATE_SCOPE = "module"


def _summary(tmp_path: Path, make: list[str]):
    from bulk_downloader import dom_analyzer as da
    for rel in make:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    return da.scan_captures_summary(root=tmp_path)


def _dirs():
    from bulk_downloader import dom_analyzer as da
    return list(da._CAPTURE_DIRS)


def test_the_summary_reports_the_state_of_each_root(tmp_path):
    """The reading that was missing entirely."""
    s = _summary(tmp_path, [])
    assert "roots" in s, (
        "the summary does not report its roots, so a caller cannot tell an "
        "absent corpus from an empty one -- which is the whole defect")
    assert len(s["roots"]) == len(_dirs()), (
        f"expected one entry per capture root ({len(_dirs())}), got "
        f"{len(s['roots'])}; a partial list would hide exactly the root that "
        f"went missing")
    for r in s["roots"]:
        assert {"dir", "exists"} <= set(r), r
        assert "path" not in r, (
            "an absolute filesystem path is back in the roots entry; "
            "/api/captures/scan discloses none, and test_capture_scan_routes "
            "caught this exact leak in the first version of the fix")


def test_absent_and_empty_are_different_answers(tmp_path):
    """The assertion the cut exists for.

    Both cases have total == 0. Only one of them is an incident.
    """
    absent = _summary(tmp_path, [])
    assert absent["total"] == 0
    assert absent["corpus_state"] == "absent", (
        f"a corpus whose roots do not exist reported {absent['corpus_state']!r}")

    present_but_empty = _summary(tmp_path, _dirs())
    assert present_but_empty["total"] == 0
    assert present_but_empty["corpus_state"] == "empty", (
        f"roots that EXIST and hold nothing reported "
        f"{present_but_empty['corpus_state']!r} -- reporting this as 'absent' "
        f"would raise an incident on every fresh install, which is the "
        f"over-sensitive direction and gets the signal switched off")


def test_a_partially_missing_corpus_is_named_as_such(tmp_path):
    """The state that actually occurred: some roots restored, some not.

    Collapsing this into either neighbour loses the case -- 'empty' hides it and
    'absent' overstates it.
    """
    some = _dirs()[:1]
    s = _summary(tmp_path, some)
    assert s["corpus_state"] == "partial", (
        f"with {len(some)} of {len(_dirs())} roots present the state was "
        f"{s['corpus_state']!r}")
    assert s["roots_missing"], "the missing roots are not named"
    assert all(m not in some for m in s["roots_missing"])


def test_a_populated_corpus_reports_present(tmp_path):
    """The over-sensitivity control: the healthy case must stay quiet."""
    dirs = _dirs()
    for rel in dirs:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / dirs[0] / "capture_probe.json").write_text("{}", encoding="utf-8")
    s = _summary(tmp_path, dirs)
    assert s["total"] >= 1, "the fixture built no capture, so this proves nothing"
    assert s["corpus_state"] == "present"
    assert not s["roots_missing"]


def test_the_state_survives_the_api_layer(tmp_path, monkeypatch):
    """TEST THE SEAM. The walk can be perfectly honest and the surface still
    report 'empty' if the field is dropped between them -- which is how the
    original defect reached an operator."""
    from bulk_downloader import dom_analyzer as da
    captured = {}
    real = da.scan_captures_summary

    def spy(root=None):
        out = real(root=tmp_path)
        captured.update(out)
        return out

    monkeypatch.setattr(da, "scan_captures_summary", spy)
    summ = da.scan_captures_summary()
    summ.pop("rows", None)
    assert "corpus_state" in summ and "roots_missing" in summ, (
        "the fields are dropped before a caller sees them: %r" % sorted(summ))
