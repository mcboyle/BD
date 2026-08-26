"""The suite rewrote the operator's real dashboard layout on every capture run.

WHAT HAPPENED. tests/test_v3_66_729_body_contract_fixtures.py reaches an endpoint
that calls widgets_config.save() with no override in force, so
_config_path() resolves to ~/.config/bulk-downloader/widgets.json -- the
operator's real file -- and reset_global() replaces it with the four
DEFAULT_WIDGETS. capture.sh runs the whole suite in two pytest lanes, so this
fired on EVERY capture. Confirmed on the deploy box: after a green capture the
operator's widgets.json held exactly DEFAULT_WIDGETS with "per_site": {}, and
its _saved_at fell inside the capture's own test window.

WHY THE EXISTING GUARD DID NOT CATCH IT, which is the actual lesson. conftest
already had _never_write_the_real_vpn_config, and its Layer 2 wraps the FUNCTION
vpn_config.save. But the subject of the property "no test writes the operator's
config" is a PATH, not a function. app_store_raw_editor._atomic_write
(:129-133) does Path.write_text into a .tmp then os.replace onto the same
path, and never calls save() -- so with the guard provably installed
(vpn_config.save.__name__ == "_guarded_save"), a POST to
/api/settings/store-raw still returned 200 and changed the real tunnels.json.

That is CLAUDE.md section 0: a guard whose denominator (calls through one
function) does not contain its subject (writes to one path). Enumerating more
stores and wrapping more save() functions would keep failing the same way,
however many you enumerate. The guard has to key on the RESOLVED PATH.

THE POPULATION IS DERIVED, NOT LISTED. An AST scan of bulk_downloader/**/*.py
(565 files, 0 unparsed; predicate: os.path.expanduser | Path.expanduser |
Path.home | os.environ HOME lookups) finds 19 $HOME-resolving expressions, of
which exactly three are $HOME-DEFAULTING PERSISTENT STORES:

    vpn_config.py:110      ~/.config/bulk-downloader/vpn/tunnels.json
    widgets_config.py:96   ~/.config/bulk-downloader/widgets.json
    macro_recorder.py:139  ~/BulkDownloader/macros/       (via BD_INSTALL_DIR)

The other 16 are expanduser() over a CALLER-supplied path, read-only probes, a
boot-seed candidate list, or download-dir defaults -- none is a store.

test_every_home_defaulting_store_is_classified below re-derives that population
at run time and fails on any store this file has not classified, so a store
added tomorrow cannot be silently unguarded. That is what makes this
denominator-derived rather than another hand-kept list.

WHY macros IS GUARDED CONDITIONALLY, and why the condition is not a fudge. On
this container ~/BulkDownloader is outside the checkout, so ~/BulkDownloader/
macros is operator state and is refused. On the deploy box the checkout IS
~/BulkDownloader, so the identical path is ordinary untracked repo litter that
git status already surfaces -- and a guard firing there would fire on correct
behaviour, which section 0 calls a soundness bug in its own right. The guard
therefore protects that root only when it resolves outside the repo.

Machine-dependent behaviour is worth stating plainly rather than hiding: BOTH
branches are pinned by test_macros_is_guarded_only_when_it_lands_outside_the_repo,
because asserting only the branch this machine happens to take would leave the
other unverified on every machine -- a denominator excluding half its subject.

Note sites_config.json is NOT in this population despite being the subject of an
earlier cut in the same family: it resolves under BD_INSTALL_DIR or cwd, never
through $HOME.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest as _ct

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]


# ── Layer 2: the guard keys on the path, so a bypassing writer is caught ─────

def _fake_home(monkeypatch, tmp_path):
    """Point HOME at a disposable dir and return its protected store root.

    The guard resolves its roots at WRITE time, not at session start, so a test
    can relocate HOME and exercise the real predicate without going anywhere
    near the operator's actual files. Nothing under the real ~ is touched by
    anything in this file.
    """
    # Build the namespace BEFORE relocating HOME. Once HOME points here the
    # guard resolves its roots to this directory, and os.mkdir into a protected
    # root is refused -- the setup would trip the very guard it is setting up.
    # A fixture that trips the guard is a fixture defect, not a finding.
    root = tmp_path / ".config" / "bulk-downloader"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return root


def test_write_text_into_the_store_root_is_refused(monkeypatch, tmp_path):
    """RED. The direct route: widgets_config.save() -> Path.write_text."""
    root = _fake_home(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as caught:
        (root / "widgets.json").write_text('{"global": []}', encoding="utf-8")
    assert "widgets.json" in str(caught.value)


def test_os_replace_onto_the_store_root_is_refused(monkeypatch, tmp_path):
    """RED, and the one the function-keyed guard could never catch.

    This is app_store_raw_editor._atomic_write's exact shape: write a .tmp, then
    os.replace it onto the real path. It never calls the store's save(), so a
    wrapper around save() is blind to it -- measured, with the VPN guard
    installed, as a 200 that changed the operator's real tunnels.json.
    """
    root = _fake_home(monkeypatch, tmp_path)
    src = tmp_path / "payload.tmp"
    src.write_text('{"tunnels": []}', encoding="utf-8")
    with pytest.raises(RuntimeError) as caught:
        os.replace(src, root / "vpn" / "tunnels.json")
    assert "tunnels.json" in str(caught.value)


def test_builtin_open_for_write_into_the_store_root_is_refused(monkeypatch, tmp_path):
    """RED. Third route to the same subject: a bare open(path, 'w').

    Three routes are asserted rather than one because the property is "no write
    reaches this path", and a guard that covers only the route we happened to
    observe would be the same too-narrow denominator one layer down.
    """
    root = _fake_home(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        with open(root / "widgets.json", "w", encoding="utf-8") as fh:
            fh.write("{}")


def test_reading_the_store_root_is_still_allowed(monkeypatch, tmp_path):
    """ANTI-CRY-WOLF. Reads must pass. The property is about WRITES; blocking
    reads would break every test that legitimately loads config."""
    root = _fake_home(monkeypatch, tmp_path)
    target = root / "widgets.json"
    _ct._home_store_guard_bypass(lambda: target.write_text("{}", encoding="utf-8"))
    assert target.read_text(encoding="utf-8") == "{}"
    with open(target, encoding="utf-8") as fh:
        assert fh.read() == "{}"


def test_writes_outside_the_store_root_are_untouched(monkeypatch, tmp_path):
    """ANTI-CRY-WOLF, and the one that matters on the box.

    On the deploy host the checkout is ~/BulkDownloader, so an over-broad guard
    keyed on $HOME would fire on ordinary repo writes and get switched off. Only
    the app's own config namespace is protected.
    """
    _fake_home(monkeypatch, tmp_path)
    for probe in (tmp_path / "scratch.json",
                  tmp_path / "BulkDownloader" / "src" / "thing.py",
                  tmp_path / ".config" / "something-else" / "c.json"):
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"


# ── Layer 1: the session points every known store somewhere disposable ───────

@pytest.mark.parametrize("var", ["BD_VPN_CONFIG_PATH", "BD_WIDGETS_CONFIG_PATH"])
def test_the_session_redirects_the_store(var):
    """RED for widgets. Layer 1 alone is not sufficient -- an env var is exactly
    what a test pops in a finally -- but its absence for widgets is why the
    operator's file was reachable at all."""
    value = os.environ.get(var)
    assert value, f"{var} is not set session-wide, so a test that does not set it writes the real store"
    assert not str(value).startswith(os.path.expanduser("~/.config/bulk-downloader")), (
        f"{var} points into the operator's real config namespace: {value}")


def test_the_real_store_paths_are_covered_by_layer_2():
    """The two ~/.config stores must resolve under a protected root when their
    override is absent. Asserted over the RESOLVED default, not over a literal,
    so a change to either resolver is caught here."""
    roots = [str(r) for r in _ct._protected_home_roots()]
    assert roots, "no protected roots -- Layer 2 is not armed"
    env = {**os.environ}
    env.pop("BD_VPN_CONFIG_PATH", None)
    env.pop("BD_WIDGETS_CONFIG_PATH", None)
    probe = (
        "import json,sys;"
        "sys.path.insert(0,%r);"
        "from bulk_downloader import vpn_config, widgets_config;"
        "print(json.dumps([str(vpn_config._config_path()),"
        "str(widgets_config._config_path())]))" % str(REPO)
    )
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    import json as _json
    for resolved in _json.loads(r.stdout.strip().splitlines()[-1]):
        assert any(resolved.startswith(root) for root in roots), (
            f"{resolved} is a $HOME-defaulting store outside every protected "
            f"root {roots}; Layer 2 cannot see it")


# ── the denominator: a store added tomorrow cannot be silently unguarded ─────

_HOME_CALLS = ("expanduser", "home")


def _home_resolving_modules():
    """Modules under bulk_downloader/ containing a $HOME-resolving expression.

    AST, not grep: the predicate is a Call node, so the string "expanduser"
    inside a comment or a docstring is structurally invisible. Unparseable files
    FAIL rather than being skipped -- a file the scan cannot read is unknown,
    and unknown is a third state.
    """
    hits, unparsed = {}, []
    for path in sorted((REPO / "bulk_downloader").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unparsed.append(f"{path.name}: {type(exc).__name__}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in _HOME_CALLS:
                hits.setdefault(path.name, []).append(node.lineno)
            elif (name in ("get", "__getitem__")
                  and any(isinstance(a, ast.Constant) and a.value == "HOME"
                          for a in node.args)):
                hits.setdefault(path.name, []).append(node.lineno)
    assert not unparsed, f"the scan could not read {unparsed}; its denominator is incomplete"
    return hits


# Every module below has been read and classified. STORE => a persistent store
# that DEFAULTS under $HOME. NOT-A-STORE => expanduser over a caller-supplied
# path, a read-only probe, a candidate list, or a returned default.
_CLASSIFIED = {
    "vpn_config.py": "STORE",
    "widgets_config.py": "STORE",
    "macro_recorder.py": "STORE",
    "app_envfile_editor.py": "NOT-A-STORE",
    "_envfile.py": "NOT-A-STORE",
    "detect.py": "NOT-A-STORE",
    "tool_bridge.py": "NOT-A-STORE",
    "runner_browser.py": "NOT-A-STORE",
    "yt_dlp_archive.py": "NOT-A-STORE",
    "app.py": "NOT-A-STORE",
    "db_tools.py": "NOT-A-STORE",
    "release_lint.py": "NOT-A-STORE",
}

# macros is covered CONDITIONALLY, and the condition is the whole point. On this
# container ~/BulkDownloader is outside the checkout, so it is operator state and
# is guarded. On the deploy box the checkout IS ~/BulkDownloader, so the same
# path is ordinary untracked repo litter that git status already surfaces --
# guarding it there would fire on correct behaviour. Both branches are pinned by
# test_macros_is_guarded_only_when_it_lands_outside_the_repo.
_LAYER2_CONDITIONAL = {
    "macro_recorder.py": (
        "guarded when ~/BulkDownloader/macros resolves outside the repo root; "
        "skipped when it resolves inside, where it is repo litter rather than "
        "operator state and a guard would cry wolf on every ordinary write."
    ),
}
_LAYER2_EXEMPT = {}


def test_every_home_defaulting_store_is_classified():
    """THE DENOMINATOR GUARD. Re-derives the $HOME-resolving population at run
    time and fails on any module this file has not classified.

    Without it, the three stores above are just another hand-kept list, and the
    fourth one somebody adds next month is unguarded and silent -- which is
    exactly how widgets came to be writable in the first place.
    """
    found = set(_home_resolving_modules())
    unclassified = sorted(found - set(_CLASSIFIED))
    assert not unclassified, (
        "these modules resolve $HOME but are not classified in this file: "
        f"{unclassified}. Read each one. If it is a persistent store that "
        "DEFAULTS under $HOME, it must be guarded (Layer 1 + Layer 2) and "
        "added as STORE. If it is not, add it as NOT-A-STORE with that "
        "decision recorded. Do not delete this test to make it pass."
    )


def test_every_store_is_either_layer_2_covered_or_explicitly_exempt():
    """A STORE may only skip Layer 2 with a recorded reason, so the safe-by-
    default answer is 'guarded' and the exception has to be argued for."""
    stores = {m for m, kind in _CLASSIFIED.items() if kind == "STORE"}
    covered = {"vpn_config.py", "widgets_config.py"}
    gap = sorted(stores - covered - set(_LAYER2_CONDITIONAL) - set(_LAYER2_EXEMPT))
    assert not gap, (
        f"{gap} default under $HOME but are neither covered by a protected "
        "root, nor conditionally covered, nor exempt with a recorded reason"
    )
    for module, reason in {**_LAYER2_CONDITIONAL, **_LAYER2_EXEMPT}.items():
        assert len(reason) > 40, f"{module}'s reason is too thin to review: {reason!r}"


def test_macros_is_guarded_only_when_it_lands_outside_the_repo(monkeypatch, tmp_path):
    """Both branches of the conditional, pinned.

    OUTSIDE the repo (this container): ~/BulkDownloader/macros is operator state
    and must be refused. INSIDE the repo (the deploy box, where the checkout IS
    ~/BulkDownloader): it is untracked repo litter that git status already
    surfaces, and refusing it would fire on correct behaviour.

    Asserting only the branch this machine happens to take would leave the other
    one unverified on every machine -- the denominator excluding half its own
    subject.
    """
    # Same ordering rule as _fake_home: both trees exist before HOME moves.
    (tmp_path / "BulkDownloader" / "macros").mkdir(parents=True, exist_ok=True)
    outside = _fake_home(monkeypatch, tmp_path).parent.parent / "BulkDownloader" / "macros"
    with pytest.raises(RuntimeError):
        (outside / "m.json").write_text("{}", encoding="utf-8")

    # now make the SAME logical path resolve inside the repo root
    monkeypatch.setattr(_ct, "_REPO_ROOT_FOR_GUARD", tmp_path / "BulkDownloader")
    inside = tmp_path / "BulkDownloader" / "macros" / "m2.json"
    inside.write_text("{}", encoding="utf-8")
    assert inside.read_text(encoding="utf-8") == "{}"


# ── The gap: a DECLARED protected root whose write primitive was not hooked ──
#
# At 5e5e9c5 ~/BulkDownloader/macros was already listed by
# _protected_home_roots() and _violates_home_store_guard() already returned True
# for it -- and the directory was created anyway on every run, because
# macro_recorder._macro_dir() reaches it with Path.mkdir(parents=True), which
# lands on os.mkdir, and none of the six hooked primitives was os.mkdir.
# Measured in-process with the guard active:
#     guard predicate says violation: True
#     exists before: False
#     _macro_dir() returned: /root/BulkDownloader/macros
#     exists after: True
# The gate above reported OK throughout, because it asserts the PREDICATE while
# the subject is the WRITE. Declaring a root is not protecting it.

def test_mkdir_into_the_store_root_is_refused(monkeypatch, tmp_path):
    """RED. os.mkdir was the one write primitive nothing hooked."""
    root = _fake_home(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        (root / "vpn").mkdir()
    assert not (root / "vpn").exists()


def test_mkdir_parents_and_makedirs_into_the_store_root_are_refused(
        monkeypatch, tmp_path):
    """Path.mkdir(parents=True) recurses through os.mkdir, and os.makedirs is
    implemented on it too -- measured on CPython 3.12.3, so one hook covers all
    three. Pinned by behaviour so a future edit cannot hook only the shallow
    case."""
    root = _fake_home(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        (root / "a" / "b").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        os.makedirs(str(root / "c" / "d"))
    assert not (root / "a").exists() and not (root / "c").exists()


def test_macro_dir_does_not_create_the_operators_macro_directory(
        monkeypatch, tmp_path):
    """RED, end to end. The store's own resolver, with nothing steering it."""
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    from bulk_downloader import macro_recorder
    resolved = Path(macro_recorder._macro_dir())
    operator_macros = tmp_path / "BulkDownloader" / "macros"
    assert not operator_macros.exists(), (
        f"macro_recorder._macro_dir() created {operator_macros} -- the path the "
        f"guard already declared protected")
    assert not str(resolved).startswith(str(operator_macros)), (
        f"_macro_dir() resolved to {resolved}, inside the protected root")


def test_the_macro_store_lever_is_diverted():
    """Layer 1 for macros. Not optional: layer 2 alone makes /api/macros/* 5xx,
    which fails test_v3_66_729_body_contract_fixtures.py::
    test_the_app_never_5xxs_on_a_well_formed_request -- measured. A guard that
    turns a silent write into a red unrelated test is a guard that gets
    switched off."""
    from bulk_downloader import macro_recorder
    assert macro_recorder._macro_dir.__name__ == "_guarded_macro_dir", (
        f"macro_recorder._macro_dir is {macro_recorder._macro_dir!r}; the "
        f"session redirect is not installed")


def test_a_test_that_steers_bd_install_dir_keeps_steering_it(
        monkeypatch, tmp_path):
    """CRY-WOLF. The shim must divert only the UNSTEERED case, exactly as
    _never_write_the_repo_plugins_dir does for _plugin_dir."""
    steered = tmp_path / "install"
    (steered / "macros").mkdir(parents=True)
    monkeypatch.setenv("BD_INSTALL_DIR", str(steered))
    from bulk_downloader import macro_recorder
    assert Path(macro_recorder._macro_dir()) == steered / "macros"


def test_mkdir_outside_the_store_root_is_untouched(monkeypatch, tmp_path):
    """CRY-WOLF. Hooking os.mkdir is the broadest hook in the guard -- every
    tmp dir, every fixture, every artifact directory in the suite goes through
    it. If it fires on any of these the guard gets switched off."""
    _fake_home(monkeypatch, tmp_path)
    for probe in (tmp_path / "scratch",
                  tmp_path / "Downloads" / "bulk_downloader",
                  tmp_path / "BulkDownloader" / "src",
                  tmp_path / ".config" / "something-else",
                  tmp_path / ".config" / "bulk-downloader-backup"):
        probe.mkdir(parents=True, exist_ok=True)
        assert probe.is_dir()


# ── The relocation window: the operator's FIXED path must not lose cover ──────
#
# _protected_home_roots() resolves from expanduser("~") at CALL time. That is
# right for FOLLOWING a relocated HOME -- it is what lets every RED above
# exercise the real predicate against a tmp directory instead of the operator's
# files. But resolution alone also means the roots MOVE: while a test holds HOME
# at a tmp path, the operator's real ~/.config/bulk-downloader is under NO
# protected root, and a write landing there during that window is waved through.
#
# Measured on pristine source at 2cb520c, one absolute path, two calls:
#     roots at HOME=A: ['A/.config/bulk-downloader', 'A/BulkDownloader/macros']
#     predicate(A/.config/bulk-downloader/widgets.json) with HOME=A: True
#     roots at HOME=B: ['B/.config/bulk-downloader', 'B/BulkDownloader/macros']
#     predicate(SAME ABSOLUTE PATH)                    with HOME=B: False
#
# The fix is a UNION, not a swap. Freezing the roots at session start ALONE
# would protect the operator and break every call-time RED above -- a prior
# candidate did exactly that and turned six named tests red, including both
# mkdir REDs. So both halves are asserted, and the cry-wolf direction is
# asserted too: conftest is the shared denominator of every band in the suite,
# and a guard that defends MORE paths has more room to refuse a legitimate
# write.


def _operator_config_root():
    """~/.config/bulk-downloader as it stood when conftest was IMPORTED.

    Derived from _REAL_VPN_CONFIG, which conftest freezes at import time, so the
    answer does not move when a test relocates HOME -- including the relocation
    performed by the test asking the question.
    """
    return _ct._REAL_VPN_CONFIG.parent.parent


def test_the_operators_config_root_keeps_its_cover_when_home_moves(
        monkeypatch, tmp_path):
    """RED. One fixed absolute path, asked either side of the relocation."""
    fixed = _operator_config_root() / "widgets.json"
    assert _ct._violates_home_store_guard(fixed), (
        "precondition: the operator's own config is protected before any test "
        "has relocated HOME")
    _fake_home(monkeypatch, tmp_path)
    assert _ct._violates_home_store_guard(fixed), (
        f"{fixed} lost its protection the moment HOME moved to {tmp_path}. The "
        "roots are resolved at call time and nothing remembers where the "
        "session started, so for the whole of any relocation window a write to "
        "the operator's real config is waved through.")


def test_both_halves_of_the_union_are_live_at_once(monkeypatch, tmp_path):
    """RED for the frozen half, GREEN for the call-time half -- in one body.

    A snapshot that REPLACED call-time resolution would satisfy the test above
    while breaking the relocated half, so asserting both here makes that swap
    impossible to pass.
    """
    fixed = _operator_config_root() / "widgets.json"
    relocated = _fake_home(monkeypatch, tmp_path) / "widgets.json"
    assert _ct._violates_home_store_guard(fixed), (
        "the session-start half is not armed: the operator's real config is "
        "unprotected while HOME points elsewhere")
    assert _ct._violates_home_store_guard(relocated), (
        "the call-time half is not armed: a relocated HOME is no longer "
        "followed, which is what the mkdir REDs above depend on")


def test_the_write_primitives_refuse_the_operators_config_while_home_is_moved(
        monkeypatch, tmp_path):
    """RED at the WRITE, not at the predicate.

    This file already paid for that distinction once: ~/BulkDownloader/macros
    was a DECLARED root whose predicate returned True while os.mkdir created the
    directory anyway, and the gate asserting the predicate reported OK. So the
    relocation window is asserted through the primitives as well.

    Both probes name a directory that does NOT exist under the operator's real
    config, so an UNGUARDED run raises FileNotFoundError from the filesystem and
    creates nothing. The test reproduces the defect without being able to write
    into the operator's config on any machine -- the refusal it demands is a
    RuntimeError, and the filesystem's error is reported as the failure.
    """
    missing = _operator_config_root() / "__bd_guard_probe_absent__"
    assert not missing.exists(), (
        f"{missing} exists; this probe requires an absent directory so that an "
        "unguarded run cannot create anything")
    _fake_home(monkeypatch, tmp_path)

    attempts = (
        ("Path.write_text", lambda: (missing / "x.json").write_text(
            "{}", encoding="utf-8")),
        ("os.mkdir", lambda: os.mkdir(str(missing / "deeper"))),
    )
    for label, attempt in attempts:
        with pytest.raises(Exception) as caught:
            attempt()
        assert isinstance(caught.value, RuntimeError), (
            f"{label} was not refused while HOME pointed at {tmp_path}: it "
            f"failed with {type(caught.value).__name__} from the filesystem, "
            "which is the only reason nothing was written. On a box where that "
            "directory exists the write would have landed in the operator's "
            "real config.")
    assert not missing.exists()


def test_the_union_still_ignores_paths_outside_both_namespaces(
        monkeypatch, tmp_path):
    """CRY-WOLF, and it is the dominant risk of this cut.

    The union protects strictly MORE paths than call-time resolution alone, and
    conftest is the shared denominator of every band in the suite. Writes that
    were legal must stay legal, and the frozen half must not sweep in the rest
    of the session-start HOME along with the app's own namespace.
    """
    home = _operator_config_root().parents[1]
    _fake_home(monkeypatch, tmp_path)
    for probe in (tmp_path / "scratch.json",
                  tmp_path / "BulkDownloader" / "src" / "thing.py",
                  tmp_path / ".config" / "something-else" / "c.json",
                  tmp_path / ".config" / "bulk-downloader-backup" / "c.json"):
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"
    for sibling in (home / ".config" / "something-else" / "c.json",
                    home / ".cache" / _ct._BD_CONFIG_DIRNAME / "c.json",
                    home / ".config" / "bulk-downloader-backup" / "c.json",
                    home / "Downloads" / "bulk_downloader" / "c.json"):
        assert not _ct._violates_home_store_guard(sibling), (
            f"the frozen half over-reached to {sibling}, which is not the "
            "app's own config namespace")


def test_the_frozen_half_applies_the_same_repo_exclusion(monkeypatch, tmp_path):
    """Both branches of the macros conditional, on the shared root builder.

    STRUCTURAL PIN, not a behavioural RED: on pristine source the helper does
    not exist. It earns its place by covering the branch no machine can
    exercise -- the frozen half is computed once at import and cannot follow a
    monkeypatched _REPO_ROOT_FOR_GUARD, so without this the deploy box's branch
    (checkout IS ~/BulkDownloader, macros must NOT be guarded) is unverified
    everywhere, and a frozen half that dropped the exclusion would refuse
    ordinary repo writes forever.
    """
    home = tmp_path / "home"
    config = home / ".config" / _ct._BD_CONFIG_DIRNAME
    macros = home / "BulkDownloader" / _ct._MACRO_DIRNAME

    monkeypatch.setattr(_ct, "_REPO_ROOT_FOR_GUARD", tmp_path / "somewhere-else")
    outside = _ct._home_roots_for(home)
    assert config in outside and macros in outside

    monkeypatch.setattr(_ct, "_REPO_ROOT_FOR_GUARD", home / "BulkDownloader")
    inside = _ct._home_roots_for(home)
    assert config in inside and macros not in inside
