"""Row 667, second clause: the cloakbrowser log must match the EFFECTIVE
``use_persistent_profile``.

Every browser flow in the tree names its persistence on the one line
``cloak.log_choice`` emits: ``runner_browser`` logs ``"persistent profile"``
or ``"non-persistent"``, the manual flow logs ``"persistent manual profile"``,
and the login flow logs ``"non-persistent"`` even when the site config asks
for a persistent profile (``test_row667_login_attempt_accounting.py::
test_login_cloak_choice_is_nonpersistent_by_design`` pins that on purpose).

The session keeper is the exception. ``SessionKeeper._launch_browser`` opens a
persistent context unconditionally -- that is the keeper's whole point, see
v3.64.2 -- but it calls ``log_choice`` with the backend alone and no detail. So
a keepalive line in a campaign log is indistinguishable from any other launch,
and on a site configured ``use_persistent_profile: False`` the log records
nothing about the fact that the keeper kept a profile anyway. A reader auditing
the log against the site config cannot tell what actually happened, which is
the misreport row 667 names.

The fix reports the effective persistence, and names the divergence when the
config asked for something else. It does NOT change what the keeper launches:
a keeper without a persistent profile cannot keep a session alive.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

BD_GATE_SCOPE = "repo-wide"

_PRODUCT = pathlib.Path(__file__).resolve().parent.parent / "bulk_downloader"

# Every browser launch in the tree logs through cloak.log_choice(flow, backend,
# detail). The detail is the only place the line says which profile the launch
# actually used, so a call that omits it produces a log that cannot be audited
# against the site config at all -- the misreport row 667 names. Measured at
# base 367f0f42: nine call sites, eight already passing a detail, the keeper the
# single exception.
_EXPECTED_LOG_CHOICE_CALL_SITES = 9


class _FakeContext:
    """Minimal stand-in for a persistent BrowserContext."""

    def __init__(self):
        self.pages = []
        self.added_cookies = []

    def new_page(self):
        return _FakePage()

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def close(self):
        pass


class _FakePage:
    def route(self, *_args, **_kwargs):
        pass

    def is_closed(self):
        return False


def _keeper_with_fake_browser(monkeypatch, tmp_path, config):
    """Build a SessionKeeper whose browser launch is a fixture, and record
    every ``open_persistent_context`` and ``log_choice`` call.

    Returns ``(keeper, launches, choices)``.
    """
    sk = importlib.import_module("bulk_downloader.session_keeper")
    cloak = importlib.import_module("bulk_downloader.cloak")

    launches: list[dict] = []
    choices: list[tuple] = []

    def _fake_open(**kwargs):
        launches.append(kwargs)
        return _FakeContext(), None, "fixture-cloak"

    monkeypatch.setattr(cloak, "open_persistent_context", _fake_open)
    monkeypatch.setattr(
        cloak, "log_choice",
        lambda *args, **kwargs: choices.append(args))
    # The profile dir is created relative to cwd; keep it in tmp_path.
    monkeypatch.chdir(tmp_path)

    keeper = sk.SessionKeeper("row667fixture", 0, config, lambda *a: (False, ""))
    return keeper, launches, choices


def test_keeper_log_names_the_persistence_it_actually_used(monkeypatch, tmp_path):
    """A site that asks for a persistent profile: the keeper launches one and
    the log line says so."""
    keeper, launches, choices = _keeper_with_fake_browser(
        monkeypatch, tmp_path, {"use_persistent_profile": True})

    assert keeper._launch_browser() is True
    # Prove the fixture built the shape before judging the log: a launch
    # really happened, and it really was the persistent entry point.
    assert len(launches) == 1
    assert "user_data_dir" in launches[0]

    assert len(choices) == 1
    flow, backend, *detail = choices[0]
    assert flow == "keepalive[row667fixture/0]"
    assert backend == "fixture-cloak"
    assert detail, (
        "the keepalive launch logged no persistence detail, so its line cannot "
        "be matched against the site's use_persistent_profile: "
        f"{choices[0]!r}")
    assert "persistent" in detail[0]
    assert "non-persistent" not in detail[0]


def test_keeper_log_names_the_divergence_when_config_asked_for_no_profile(
        monkeypatch, tmp_path):
    """A site configured ``use_persistent_profile: False`` still gets a
    persistent keeper profile. The log must record that divergence rather than
    omit it -- otherwise the log misreports the effective value."""
    keeper, launches, choices = _keeper_with_fake_browser(
        monkeypatch, tmp_path, {"use_persistent_profile": False})

    assert keeper._launch_browser() is True
    assert len(launches) == 1

    assert len(choices) == 1
    _flow, _backend, *detail = choices[0]
    assert detail, (
        "a site configured use_persistent_profile=False got a persistent "
        "keeper profile and the log said nothing about it: "
        f"{choices[0]!r}")
    assert "persistent" in detail[0]
    assert "use_persistent_profile=False" in detail[0], (
        "the log names the effective persistence but not the configured value "
        f"it diverges from: {detail[0]!r}")


def test_the_fixture_really_observes_a_missing_detail_and_a_present_one():
    """Negative control for the assertion above: the same predicate the two
    tests use must say NO to a detail-less call and YES to a detailed one.

    Without this, a log_choice signature change could make both tests pass by
    making the predicate unreachable rather than satisfied.
    """
    def _has_detail(call):
        _flow, _backend, *detail = call
        return bool(detail) and "persistent" in detail[0]

    detail_less = ("keepalive[row667fixture/0]", "fixture-cloak")
    detailed = ("keepalive[row667fixture/0]", "fixture-cloak",
                "persistent keepalive profile")

    assert _has_detail(detail_less) is False
    assert _has_detail(detailed) is True


def _log_choice_call_sites():
    """Derive every ``cloak.log_choice`` call in the product FROM THE
    FILESYSTEM -- never from a handed list -- as ``(path, lineno, n_args)``."""
    sites = []
    for path in sorted(_PRODUCT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None))
            if name == "log_choice":
                sites.append((path, node.lineno,
                              len(node.args) + len(node.keywords)))
    return sites


def test_every_browser_flow_names_its_persistence_on_its_log_line():
    """Repo-wide: no browser flow may log a launch without saying what profile
    it used. ``log_choice(flow, backend)`` with no detail is the shape that
    makes a campaign log unauditable against ``use_persistent_profile``."""
    sites = _log_choice_call_sites()

    # Denominator first: if the census found nothing, the walk is broken and
    # every assertion below would pass vacuously.
    assert len(sites) == _EXPECTED_LOG_CHOICE_CALL_SITES, (
        "the log_choice census changed; a new browser flow (or a removed one) "
        "must be judged by this gate deliberately, not silently: "
        f"{[(str(p), n) for p, n, _ in sites]}")

    detail_less = [f"{path.relative_to(_PRODUCT.parent)}:{lineno}"
                   for path, lineno, nargs in sites if nargs < 3]
    assert detail_less == [], (
        "these browser launches log a backend but no persistence detail, so "
        "their lines cannot be matched against the site's "
        f"use_persistent_profile: {detail_less}")


def test_the_census_really_separates_a_detailed_call_from_a_bare_one():
    """Positive control for the gate above: the same predicate must say YES to
    a two-argument call and NO to a three-argument one, on source it parses
    itself -- otherwise an empty ``detail_less`` proves nothing."""
    source = (
        "def f():\n"
        "    _cloak.log_choice('flow', 'backend')\n"
        "    _cloak.log_choice('flow', 'backend', 'persistent')\n")
    tree = ast.parse(source)
    counts = [len(n.args) + len(n.keywords) for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "log_choice"]

    assert counts == [2, 3]
    assert [c for c in counts if c < 3] == [2]


def _heartbeat_with_recorded_launch(monkeypatch, tmp_path, launch_result):
    """A keeper with no live browser, whose launch, in-page fetch and httpx
    fallback are all fixtures. Returns ``(verdict, launched)``."""
    keeper, _launches, _choices = _keeper_with_fake_browser(
        monkeypatch, tmp_path, {"use_persistent_profile": True})
    # No context, no page -> _browser_alive() is False, so the heartbeat has
    # to launch before it can decide anything.
    keeper._ctx = None
    keeper._page = None
    # A navigate is due whenever the timestamp is 0, which would send the
    # heartbeat down the navigate branch; pin it to now so the cheap in-page
    # fetch is the branch under test.
    keeper._last_navigate_at = importlib.import_module(
        "bulk_downloader.session_keeper")._now()

    launched = []
    monkeypatch.setattr(
        keeper, "_launch_browser",
        lambda: (launched.append("launch"), launch_result)[1])
    monkeypatch.setattr(
        keeper, "_heartbeat_in_page_fetch", lambda: (True, "IN-PAGE"))
    monkeypatch.setattr(
        keeper, "_heartbeat_navigate", lambda: (True, "NAVIGATE"))
    monkeypatch.setattr(
        keeper, "_heartbeat_httpx_fallback", lambda: (False, "HTTPX-FALLBACK"))

    return keeper._heartbeat(), launched


def test_a_heartbeat_without_a_live_browser_launches_one_and_uses_it(
        monkeypatch, tmp_path):
    """The keeper's whole persistence claim rests on this call: with no live
    browser the heartbeat must launch one and then measure through it. If the
    launch result is not consulted, a dead browser silently degrades every
    heartbeat to the httpx fallback -- the browser log then describes a
    session the keeper never actually kept."""
    (verdict, detail), launched = _heartbeat_with_recorded_launch(
        monkeypatch, tmp_path, True)

    assert launched == ["launch"], (
        "the heartbeat did not launch a browser although none was alive")
    assert (verdict, detail) == (True, "IN-PAGE"), (
        "a successful launch must be used for the heartbeat, not discarded "
        f"in favour of the httpx fallback: {(verdict, detail)!r}")


def test_a_failed_launch_still_falls_back(monkeypatch, tmp_path):
    """Positive control for the assertion above: the same fixture must show
    the fallback when the launch really fails, or 'not the fallback' would be
    a claim the probe could never contradict."""
    (verdict, detail), launched = _heartbeat_with_recorded_launch(
        monkeypatch, tmp_path, False)

    assert launched == ["launch"]
    assert (verdict, detail) == (False, "HTTPX-FALLBACK")
