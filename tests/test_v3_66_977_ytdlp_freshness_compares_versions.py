"""The yt-dlp freshness check must compare VERSIONS, not wall-clock age.

@977. MEASURED on the box 2026-08-09, which is what opened this: the selftest
reported *"yt-dlp is 36 days old -- consider updating"*, the operator ran
`pip install -U yt-dlp`, and pip answered *"Requirement already satisfied ...
(2026.7.4)"*. Independently confirmed against the index: 2026.7.4 IS the newest
release, uploaded 2026-07-04. The box was already current and the check fired
anyway, because its predicate was `age_days > 30`.

Age cannot distinguish "you are behind" from "upstream has been quiet". The
operator did exactly what the message asked and it was necessarily a no-op --
CLAUDE.md section 0's over-sensitivity failure, which the contract counts as a
soundness bug rather than a safe default precisely because a check that cries
wolf gets switched off. It had been WARNing in every capture.

A NOTE ON THE THIRD STATE, because this cut does not fully get one. `selftest`
has exactly three statuses -- ok / warn / fail -- and no UNKNOWN. "Could not
reach the index" is therefore reported as WARN, with the distinction carried in
the MESSAGE rather than in the status. That is weaker than section 0 asks for,
and it is deliberate: adding a fourth status changes the boot summary shape, the
`07b_selftest.json` artifact and every consumer of `ok/warn/fail`, which is a
wider blast radius than this fix should carry. What the fix does guarantee is
that an unreachable index never reads as "you are stale" -- the two say
different things and recommend different actions.
"""

import importlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _mods():
    from bulk_downloader import healthcheck, ytdlp_updater
    importlib.reload(ytdlp_updater)
    return healthcheck, ytdlp_updater


def _patch(monkeypatch, *, installed, age, latest):
    hc, yt = _mods()
    monkeypatch.setattr(yt, "status_dict", lambda: {
        "installed": installed is not None, "version": installed,
        "age_days": age, "stale": age is not None and age > 30})
    monkeypatch.setattr(yt, "latest_version", lambda **kw: latest)
    return hc


def test_CURRENT_but_old_is_OK_not_a_warning(monkeypatch):
    """THE DEFECT, exactly as measured on the box.

    36 days old AND already the newest release. The old predicate warned; there
    was nothing the operator could do about it.
    """
    hc = _patch(monkeypatch, installed="2026.7.4", age=36, latest="2026.7.4")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_OK, (
        "a box running the NEWEST release was told to update, because the check "
        "measured age instead of comparing versions: %r" % r)


def test_genuinely_BEHIND_still_warns(monkeypatch):
    """The over-sensitive direction's opposite: the fix must not silence a real
    one. A check that returned OK unconditionally would pass the test above."""
    hc = _patch(monkeypatch, installed="2026.7.4", age=36, latest="2026.8.9")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN, (
        "a box genuinely behind the index was reported OK: %r" % r)
    assert "2026.8.9" in r["message"], (
        "the warning does not name the version available, so the operator "
        "cannot tell what updating would get them: %r" % r["message"])


def test_a_YOUNG_but_behind_release_still_warns(monkeypatch):
    """Age must not gate the comparison in the other direction either: a release
    5 days old can still be superseded, and the old threshold would have hidden
    it entirely."""
    hc = _patch(monkeypatch, installed="2026.8.1", age=5, latest="2026.8.9")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN, (
        "a recent-but-superseded release was passed as fresh purely because it "
        "was under the age threshold: %r" % r)


def test_an_UNREACHABLE_index_does_not_claim_staleness(monkeypatch):
    """"I could not check" and "you are stale" recommend different actions.

    The status is WARN either way (there is no UNKNOWN in this vocabulary), so
    the message is what has to carry it -- and it must not tell the operator to
    update, because nothing has established that they should.
    """
    hc = _patch(monkeypatch, installed="2026.7.4", age=36, latest=None)
    r = hc._check_ytdlp()
    m = r["message"].lower()
    assert ("could not" in m or "unknown" in m or "unable" in m), (
        "an unreachable index produced a message that does not say the check "
        "failed: %r" % r["message"])
    assert "consider updating" not in m, (
        "the check could not reach the index, yet still told the operator to "
        "update -- that is asserting staleness it never measured: %r"
        % r["message"])


def test_an_uninstalled_ytdlp_is_still_reported(monkeypatch):
    """Unchanged behaviour, asserted so the fix does not drop it."""
    hc = _patch(monkeypatch, installed=None, age=None, latest="2026.8.9")
    r = hc._check_ytdlp()
    assert r["severity"] != hc.SEV_OK, (
        "no yt-dlp at all was reported OK: %r" % r)


def test_latest_version_EXISTS_and_fails_soft():
    """The writer half. A reader-side fix whose provider does not exist is the
    declared-and-never-written shape this repo has shipped before."""
    _hc, yt = _mods()
    assert hasattr(yt, "latest_version"), (
        "ytdlp_updater.latest_version() does not exist, so the comparison has "
        "nothing to compare against")
    # Must never raise, whatever the network does: this runs at BOOT.
    got = yt.latest_version(_fetch=lambda url, timeout: (_ for _ in ()).throw(OSError("no net")))
    assert got is None, "a failed fetch must return None, not raise: %r" % got


def test_latest_version_is_CACHED_so_boot_does_not_refetch():
    """It runs in the startup selftest. An uncached network call on every boot
    is a latency regression, and on a slow link it is a stall."""
    _hc, yt = _mods()
    calls = []

    def fake(url, timeout):
        calls.append(url)
        return '{"info": {"version": "2026.8.9"}}'

    a = yt.latest_version(_fetch=fake)
    b = yt.latest_version(_fetch=fake)
    assert a == b == "2026.8.9", (a, b)
    assert len(calls) == 1, (
        "the index was queried %d times for two calls -- the result is not "
        "cached, so every boot pays the network round trip" % len(calls))


def test_a_box_AHEAD_of_the_index_is_not_told_it_is_behind(monkeypatch):
    """Ordering, not inequality -- and this was a MUTATION ESCAPE.

    A dev or pre-release build can be NEWER than the index's latest. Comparing
    with `!=` reports that as "behind" and sends the operator to downgrade onto
    an older extractor, which is worse than the false alarm this cut removes.

    The implementation got this right; nothing ASSERTED it. `bd-mutate` swapped
    the ordered comparison for `!=` and the whole suite stayed green. Recorded
    because reasoning correctly while writing the code is not the same as
    holding the behaviour in place -- only the battery could tell the two apart.
    """
    hc = _patch(monkeypatch, installed="2026.9.9", age=1, latest="2026.8.9")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_OK, (
        "a box AHEAD of the index was reported as behind, which recommends a "
        "DOWNGRADE: %r" % r)
    assert "behind" not in r["message"].lower(), r["message"]


def test_is_behind_is_ordered_in_both_directions():
    """The unit beneath it, driven directly so the property is pinned even if
    _check_ytdlp's shape changes later."""
    _hc, yt = _mods()
    assert yt.is_behind("2026.7.4", "2026.8.9") is True
    assert yt.is_behind("2026.9.9", "2026.8.9") is False, "AHEAD read as behind"
    assert yt.is_behind("2026.8.9", "2026.8.9") is False, "EQUAL read as behind"
    assert yt.is_behind(None, "2026.8.9") is False
    assert yt.is_behind("2026.8.9", None) is False
