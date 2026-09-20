"""Row 805 -- the SSRF census judges EVERY transport BD leaves the process on.

SUBJECT.  ``tools/ssrf_client_census.py`` censused ``httpx.Client`` /
``httpx.AsyncClient`` only (row 703), so every ``urllib.request`` /
``requests`` / ``aiohttp`` egress in ``bulk_downloader/`` -- ``urlopen``, a
``build_opener`` opener's ``.open``, ``requests.<verb>``, a
``requests.Session`` subclass -- sat outside every census.  A new unguarded
site on those transports could be added with nothing in the tree noticing.

THIS GATE.  The population is derived from the TREE (``git ls-files`` for the
file denominator, ``ast`` for the sites -- never a line grep, never a handed
list) and reconciled against ONE in-tree declaration,
``bulk_downloader/ssrf_egress_exemptions.py``, which must account for every
site by ``<file>::<qualified owner>`` as ``guarded`` (an in-tree SSRF check the
reason names) or ``exempt`` (a reason why the site is not attacker-reachable).
A site the declaration does not name FAILS and is named; a declared entry no
site matches is STALE and FAILS; an unmeasurable population is UNKNOWN, which
FAILS here.  UNKNOWN is never permission.

RED before the declaration existed (this tree, this gate):
``FAIL: 26 of 26 egress sites are NOT accounted for in
bulk_downloader/ssrf_egress_exemptions.py`` naming each ``file:line``, the
``urllib.request`` sites among them.

Honest limits.  ``getattr(requests, verb)(url)`` IS censused (the module is
named at the call site even though the verb is not), but a static census still
cannot see a transport reached through a name rebound at runtime, and this
gate's transports are urllib.request / requests / aiohttp only -- a raw
``socket.create_connection`` egress is outside it, and ``guarded`` here means "an
in-tree check runs before the send", not "the connect address is pinned": the
two classify-then-connect sites the reasons name still leave a rebinding
window, which is row 703's seam, not this one's.  Fixture trees are
zero-entropy synthetic packages in a temporary git checkout.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ssrf_client_census as census_tool  # noqa: E402

SPEC = ROOT / "tests" / "mutants" / "row805_ssrf_census_covers_every_transport.json"


# --------------------------------------------------------------------------
# The tree gate
# --------------------------------------------------------------------------

def test_every_tracked_non_httpx_egress_site_is_accounted_for_in_the_tree():
    state, detail, result = census_tool.egress_verdict(ROOT)
    assert result is not None, f"the population could not be measured: {detail}"
    assert len(result.files) > 0, "the file denominator is empty"
    assert len(result.sites) > 0, "the egress population is empty -- it proves nothing"
    assert state == census_tool.OK, detail


def test_the_declaration_accounts_for_exactly_the_tree_derived_population():
    """One exact count, over a population proven nonzero first."""
    result = census_tool.egress_census(ROOT)
    assert len(result.sites) == len(result.declared_keys), (
        f"the tree holds {len(result.sites)} non-httpx egress sites, not {len(result.declared_keys)}:\n  "
        + "\n  ".join(f"{s.where} {s.transport} {s.dispatch}" for s in result.sites))
    assert set(result.keys) == set(result.declared_keys), (
        "declaration and tree disagree; unaccounted="
        f"{sorted(set(result.keys) - set(result.declared_keys))} stale="
        f"{sorted(set(result.declared_keys) - set(result.keys))}")
    transports = {s.transport for s in result.sites}
    assert transports == {"urllib.request", "requests"}, (
        f"the censused transports are {sorted(transports)}; a transport that "
        "appeared or vanished changes what this gate covers")


def test_every_declared_entry_carries_a_kind_and_a_reason():
    guarded = [k for k, kind, _r in census_tool.egress_census(ROOT).accounted if kind == "guarded"]
    assert len(guarded) >= 1, "no site is declared guarded; the kinds are not discriminating"
    for key, kind, reason in census_tool.egress_census(ROOT).accounted:
        assert kind in census_tool.ACCOUNTING_KINDS, f"{key}: {kind}"
        assert len(reason.split()) >= 5, f"{key}: the reason is not an explanation: {reason!r}"


# --------------------------------------------------------------------------
# Fixture trees -- the gate's own discrimination
# --------------------------------------------------------------------------

def _fixture_tree(tmp_path: Path, files: dict, *, git: bool = True) -> Path:
    root = tmp_path / "tree"
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


_DECLARATION = '''ACCOUNTED = {
    "bulk_downloader/sink.py::send": ("exempt", "fixed loopback endpoint that no caller can steer"),
}
'''

_ONE_SITE = '''from urllib.request import urlopen


def send(req):
    return urlopen(req, timeout=5)
'''


def test_an_unguarded_urllib_site_added_to_an_accounted_tree_is_named(tmp_path):
    clean = _fixture_tree(tmp_path / "clean", {
        "bulk_downloader/sink.py": _ONE_SITE,
        "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION,
    })
    state, detail, _ = census_tool.egress_verdict(clean)
    assert state == census_tool.OK, f"the accounted fixture is not OK: {detail}"

    dirty = _fixture_tree(tmp_path / "dirty", {
        "bulk_downloader/sink.py": _ONE_SITE,
        "bulk_downloader/new_sink.py": (
            "import urllib.request\n\n\n"
            "def fetch(url):\n"
            "    return urllib.request.urlopen(url, timeout=5)\n"),
        "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION,
    })
    state, detail, result = census_tool.egress_verdict(dirty)
    assert state == census_tool.FAIL, detail
    assert "bulk_downloader/new_sink.py:5" in detail, detail
    assert "bulk_downloader/new_sink.py::fetch" in detail, detail
    assert [s.where for s in result.unaccounted] == ["bulk_downloader/new_sink.py:5"]


def test_the_same_site_passes_once_the_tree_explains_it(tmp_path):
    root = _fixture_tree(tmp_path, {
        "bulk_downloader/sink.py": _ONE_SITE,
        "bulk_downloader/new_sink.py": (
            "import urllib.request\n\n\n"
            "def fetch(url):\n"
            "    return urllib.request.urlopen(url, timeout=5)\n"),
        "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION.replace(
            "}", '    "bulk_downloader/new_sink.py::fetch": '
                 '("guarded", "the caller classifies the host before every hop"),\n}'),
    })
    state, detail, _ = census_tool.egress_verdict(root)
    assert state == census_tool.OK, detail


def test_a_declared_entry_no_site_matches_is_stale_and_fails(tmp_path):
    root = _fixture_tree(tmp_path, {
        "bulk_downloader/sink.py": _ONE_SITE,
        "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION.replace(
            "}", '    "bulk_downloader/gone.py::fetch": '
                 '("exempt", "a site that this tree no longer holds at all"),\n}'),
    })
    state, detail, _ = census_tool.egress_verdict(root)
    assert state == census_tool.FAIL, detail
    assert "bulk_downloader/gone.py::fetch" in detail and "stale" in detail, detail


@pytest.mark.parametrize("shape", ["no-git", "no-tracked-files", "unparseable"])
def test_an_unmeasurable_population_is_UNKNOWN_and_never_OK(tmp_path, shape):
    files = {"bulk_downloader/sink.py": _ONE_SITE,
             "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION}
    if shape == "unparseable":
        files["bulk_downloader/broken.py"] = "def f(:\n"
    root = _fixture_tree(tmp_path, files, git=(shape != "no-git"))
    if shape == "no-tracked-files":
        subprocess.run(["git", "-C", str(root), "rm", "-q", "--cached", "-r", "."], check=True)
    state, detail, _ = census_tool.egress_verdict(root)
    assert state == census_tool.UNKNOWN, f"{shape} was judged {state}: {detail}"


def test_a_tree_with_files_but_no_egress_site_is_UNKNOWN_not_OK(tmp_path):
    """A denominator that collected nothing is not a clean bill of health."""
    root = _fixture_tree(tmp_path, {
        "bulk_downloader/quiet.py": "def send(x):\n    return x\n",
        "bulk_downloader/ssrf_egress_exemptions.py": "ACCOUNTED = {}\n",
    })
    state, detail, result = census_tool.egress_verdict(root)
    assert len(result.files) > 0, "the fixture tracked no files at all"
    assert state == census_tool.UNKNOWN, f"an empty population was judged {state}: {detail}"


def test_the_opener_and_session_shapes_are_censused_not_just_urlopen(tmp_path):
    root = _fixture_tree(tmp_path, {
        "bulk_downloader/shapes.py": (
            "import urllib.request\n"
            "import requests\n\n"
            "_OPENER = urllib.request.build_opener()\n\n\n"
            "def via_opener(req):\n"
            "    return _OPENER.open(req, timeout=5)\n\n\n"
            "class Session(requests.Session):\n"
            "    pass\n\n\n"
            "def via_requests(url):\n"
            "    return requests.get(url, timeout=5)\n\n\n"
            "def via_getattr(url, verb):\n"
            "    return getattr(requests, verb)(url, timeout=5)\n"),
        "bulk_downloader/ssrf_egress_exemptions.py": _DECLARATION,
    })
    result = census_tool.egress_census(root)
    assert {s.dispatch for s in result.sites} == {
        "_OPENER.open", "requests.Session subclass", "requests.get",
        "requests.<dynamic>"}, result.sites
