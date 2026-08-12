"""A session-scoped guard must not be defeatable by a `sys.modules` wipe.

ITEM 48. Measured at v3.66.1033: 14 tracked test files delete
`bulk_downloader.*` from `sys.modules` without restoring it. conftest's three
session-scoped guards each patch an attribute on a module object they imported
ONCE, at session start -- so after any such wipe the next import builds a fresh
module and the guard's patch is orphaned. The guard is then dead for the rest of
that worker process, and plugin tests write into the repository's own
`plugins/` directory.

That is what produced item 48's rotating failure set. `--dist loadfile` assigns
files to workers dynamically, so WHICH victims land downstream of a leaker
changes every run -- and fewer workers made it WORSE, because fewer workers
means longer per-worker chains and therefore more victims downstream. Measured
on .164 at the same commit: -n 4 gave 35 failures, -n 16 gave 1-8, and .249's
capture at -n 64 gave 0.

THE FIX IS AT THE GUARD, NOT AT THE 14 LEAKERS, and the reason is section 0's:
patching the leakers enumerates the ways a guard can be blinded, which is a list
that grows every time someone writes a new test. Re-asserting at the guard makes
it survive a wipe that has not been invented yet.

THE DISCRIMINATOR THAT KEEPS THIS FROM BECOMING THE v3.66.1024 MISTAKE. That
cut's conftest guard was deleted because it fought a shipped decision. This one
re-applies ONLY when the module OBJECT IDENTITY changed -- i.e. a re-import
happened. A test that deliberately reassigns the attribute on the SAME module
object is left alone, because that is a decision, not a wipe.
`test_a_deliberate_patch_on_the_same_module_is_left_alone` is that control, and
it is the assertion that stops the fix from being over-sensitive.

WHAT THIS DOES NOT COVER, stated because a guard reporting OK must say what it
cannot see: a test that wipes and then re-imports WITHIN ITS OWN BODY runs
unguarded for the remainder of that test. Re-assertion happens at the next
test's setup. Closing that would mean force-importing at setup, which would
defeat `bd_module_wipe`'s entire purpose -- a marked test wipes precisely so it
can re-import fresh and re-read env vars at module load.
"""
import pathlib
import sys

import pytest


GUARDED = (
    ("bulk_downloader.plugins", "_plugin_dir", "_guarded_plugin_dir"),
    ("bulk_downloader.plugins", "_quarantine_state_path", "_guarded_state_path"),
    ("bulk_downloader.vpn_config", "save", "_guarded_save"),
    ("bulk_downloader.macro_recorder", "_macro_dir", "_guarded_macro_dir"),
)


def _wipe_bd_modules():
    """Exactly what the 14 leakers do -- delete, never restore."""
    for m in [m for m in sys.modules
              if m == "bulk_downloader" or m.startswith("bulk_downloader.")]:
        del sys.modules[m]


def _import(name):
    __import__(name)
    return sys.modules[name]


def test_zzz_a_wipe_happens():
    """The leaker. Named to sort BEFORE the assertions below under -p no:randomly.

    It asserts nothing: its whole job is to corrupt sys.modules the way
    test_v3_66_1021_log_reinit_replaces.py:66 does, so the tests after it
    inherit the damage.
    """
    for name, _attr, _expected in GUARDED:
        _import(name)
    _wipe_bd_modules()
    assert not [m for m in sys.modules if m.startswith("bulk_downloader")]


@pytest.mark.parametrize("name,attr,expected", GUARDED)
def test_zzz_b_the_guard_survived(name, attr, expected):
    """After a wipe, a freshly imported module must still carry the guard."""
    mod = _import(name)
    fn = getattr(mod, attr, None)
    assert fn is not None, "%s.%s is gone entirely" % (name, attr)
    assert getattr(fn, "__name__", None) == expected, (
        "%s.%s is %r, not the guarded %r -- a sys.modules wipe orphaned the "
        "session guard, and every later test on this worker runs unprotected."
        % (name, attr, getattr(fn, "__name__", None), expected))


def test_zzz_c_the_plugins_guard_still_diverts_after_a_wipe(monkeypatch):
    """The behaviour, not just the name -- a guard can be present and inert.

    INSTALL_DIR is PINNED to the repo first, and the first draft of this test
    did not do that: it passed on pristine source, in the same run where the
    four name checks above went red. Whether `_plugin_dir()` would resolve to
    the repo at all depends on whether `constants.py` was imported before
    conftest's chdir, so without the pin the test cannot fail and certifies
    nothing. `test_no_test_writes_the_repo_plugins_dir` had already found and
    documented this exact trap; this file reproduced it anyway, which is
    CLAUDE.md section 0 landing inside the fix for a section 0 defect.
    """
    # NOTE: no wipe here. `test_zzz_a` already wiped, earlier in this file, and
    # the contract under test is CROSS-TEST protection -- the guard is restored
    # at the next test's setup. An in-body wipe tests the one case the fixture
    # documents as uncovered, and the first draft of this test did exactly that,
    # so it stayed red against a working fix.
    repo = pathlib.Path(__file__).resolve().parent.parent
    pl = _import("bulk_downloader.plugins")
    constants = _import("bulk_downloader.constants")
    monkeypatch.setattr(constants, "INSTALL_DIR", repo)

    resolved = pathlib.Path(pl._plugin_dir()).resolve()
    assert resolved != (repo / "plugins").resolve(), (
        "with INSTALL_DIR pinned at the repo, _plugin_dir() resolves back into "
        "the source tree at %s -- the guard is present in name but inert, and "
        "any test installing a plugin now writes into the checkout." % resolved)


_SENTINEL_HOLDER = {}


def test_zzz_d_a_deliberate_patch_is_set():
    """Half one of the over-sensitivity control. Sets a patch, asserts nothing.

    It MUST be split across two tests. The first version of this control set the
    sentinel and asserted it in the same body -- but the re-assert fixture runs
    at SETUP, so nothing it could do had happened yet. The control passed
    against a mutant that re-patches unconditionally, and `bd-mutate` caught
    that it was vacuous. The fixture only gets a chance to interfere BETWEEN
    tests, so the check has to live in the next one.
    """
    pl = _import("bulk_downloader.plugins")
    sentinel = lambda: "/deliberately/somewhere/else"      # noqa: E731
    _SENTINEL_HOLDER["fn"] = sentinel
    _SENTINEL_HOLDER["orig"] = pl._plugin_dir
    pl._plugin_dir = sentinel


def test_zzz_e_the_deliberate_patch_survived_the_next_setup():
    """Half two: the fixture must NOT have stamped over a deliberate patch.

    Re-assertion keys on the module OBJECT changing, never on the attribute
    differing. Steering `_plugin_dir` on the live module object is a decision;
    a fix that overwrote it would fight shipped tests exactly as the deleted
    v3.66.1024 guard did.
    """
    pl = _import("bulk_downloader.plugins")
    try:
        assert pl._plugin_dir is _SENTINEL_HOLDER["fn"], (
            "the re-assert fixture overwrote a deliberate patch on an unchanged "
            "module object -- it is keying on the attribute, not on module "
            "identity, and will fight every test that steers a guarded name.")
    finally:
        pl._plugin_dir = _SENTINEL_HOLDER["orig"]


def test_the_registry_adopts_the_module_it_repatched():
    """Adoption: after re-patching, the entry must point at the NEW object.

    Without it the fixture re-applies on every one of ~15,500 tests instead of
    once. Functionally idempotent, which is why a mutant removing it escaped --
    so the assertion is about the registry, not about observable behaviour.
    """
    import conftest as _cf

    entries = [e for e in _cf._GUARD_REPATCH if e[0] == "bulk_downloader.plugins"]
    assert entries, "the plugins guard is not registered at all"
    name, patched_obj, _attrs = entries[0]
    assert sys.modules.get(name) is patched_obj, (
        "the registry still points at a stale module object, so the fixture "
        "will re-patch on every test rather than adopting once")


# --- THE RATCHET -------------------------------------------------------------

# 14 -> 13 at v3.66.1055. v3.66.1049 restored the module table in
# test_v3_66_1021's fixture, which dropped it out of this detector, and that cut
# did NOT lower the pin -- exactly what the docstring below tells you to do "in
# the same cut". The ratchet cannot catch a budget left too HIGH: it is
# one-directional by design, so a stale pin is silent and simply stops gating
# the next regression. Measured at d8c9e4c: 13.
# 13 -> 14 at v3.66.1067, AND THE +1 IS NOT A NEW LEAK. It is THIS FILE
# becoming visible to its own census: the predicate used to read raw text, so
# 1034's regex source literal and an assertion message made it score as
# restoring while it deletes and never restores. The one leaker causing live
# CSRF failures was absent from its own list. Making the predicate honest cost
# exactly one row and it is this one.
#
# The leak itself is DELIBERATE (see test_zzz_a_wipe_happens) and is ledger
# item 48's subject, not this cut's. Do not "fix" it by making 1034 restore
# without reading that item first -- the wipe is what the guards are tested
# against. The honest budget is 14 until item 48 decides what replaces it.
_LEAK_BUDGET = 14


def _module_wipe_leakers():
    """Tracked test files that drop `bulk_downloader.*` and never put it back.

    THE PREDICATE IS A HEURISTIC AND SAYS SO. It is text matching, not dataflow:
    a file that restores by an idiom not listed here reads as a leaker, and a
    file whose restore is unreachable reads as safe. It is deliberately biased
    toward over-reporting -- a false leaker costs one line in a ratchet message
    a human reads, while a false clean silently re-opens the class this whole
    file exists to close.
    """
    import re
    import subprocess

    from python_source import python_code_only

    root = pathlib.Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files", "--", "tests/*.py"],
        cwd=root, capture_output=True, text=True, check=True).stdout.split()

    deletes = re.compile(r"del\s+sys\.modules\[|sys\.modules\.pop\s*\(")
    restores = re.compile(r"saved_modules|sys\.modules\.update\(|_restore\w*modules")
    out = []
    for rel in tracked:
        try:
            # CODE ONLY -- COMMENTS AND STRING LITERALS STRIPPED (@1067).
            #
            # This read the RAW text until v3.66.1067, and the file you are
            # reading was the casualty: it deletes sys.modules entries and
            # never restores them, which is its declared job, yet it scored
            # SAFE because the restore pattern appears twice in its own text --
            # once as the regex source literal above, once inside an assertion
            # message. Measured at v3.66.1066: census 13, budget 13, and the
            # one leaker causing live CSRF failures ABSENT from this list.
            #
            # A gate that cannot see the instance sitting inside it reports OK,
            # truthfully and uselessly. Section 0.
            raw = (root / rel).read_text(encoding="utf-8", errors="replace")
            src = python_code_only(root / rel)
        except OSError:
            continue
        # TWO DENOMINATORS, ON PURPOSE. "Does this file concern
        # bulk_downloader?" is answered from the RAW text, because a module
        # NAME is a string literal by nature -- `m.startswith("bulk_downloader.")`
        # is the normal way to write it, and stripping strings makes every
        # wiper in the tree invisible. Over-stripping was the first version of
        # this fix and it emptied the census silently.
        #
        # "Does it delete / restore?" is answered from CODE, because those are
        # CALLS, and a pattern that describes a call must not be mistaken for
        # one.
        if "bulk_downloader" not in raw or not deletes.search(src):
            continue
        if restores.search(src):
            continue
        out.append(rel)
    return sorted(out)


def test_no_new_sys_modules_leakers():
    """A one-directional ratchet on the class that produced item 48.

    conftest's guards now self-heal, so an existing leaker breaks nothing we
    know of. This exists for the thing we do NOT know about: the fourth piece of
    code holding a module reference across tests, which would fail with the same
    rotating signature and nothing pointing at the cause.

    MEASURED at v3.66.1034: 14 leakers, 21 files that delete-and-restore
    correctly. The 14 are deliberately NOT fixed -- they break nothing
    measurable now, and editing 14 passing files is churn with its own risk.

    Going UP is the failure. Going DOWN means someone fixed one: lower the pin
    in the same cut, exactly as test_source_windows_do_not_shift works.
    """
    leakers = _module_wipe_leakers()
    assert len(leakers) <= _LEAK_BUDGET, (
        "%d files delete bulk_downloader.* from sys.modules without restoring "
        "it, over a budget of %d. A new one landed:\n  %s\n\nRestore what you "
        "delete -- save the entries first and `sys.modules.update(saved)` in a "
        "finally, the way the 21 correct files already do. conftest's guards "
        "self-heal, but only the three that are registered; anything else "
        "holding a module reference across tests will break with item 48's "
        "rotating signature and no gate pointing at the cause."
        % (len(leakers), _LEAK_BUDGET, "\n  ".join(leakers)))
    assert leakers, (
        "the detector found ZERO leakers, which contradicts the v3.66.1034 "
        "measurement of 14 -- the predicate has gone blind rather than the "
        "tree having been cleaned. Check it before lowering the budget.")
