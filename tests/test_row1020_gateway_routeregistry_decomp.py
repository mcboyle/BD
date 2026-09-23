"""Row 1020 -- Monolithic Gateway Decomposition & Dynamic Blueprint Registration (RouteRegistry).

MEASURED ON BASE bc1544b7, not assumed:

    grep -c 'register_routes(app)' bulk_downloader/app.py            -> 161
    blocks whose first three lines are try:/from . import app_X/app_X.register_routes(app) -> 151
    of those, blocks matching the full uniform six-line shape                    -> 143
    bulk_downloader/app_*.py exposing a top-level def register_routes(           -> 161
    grep -rniE 'RouteRegistry|route_registry' bulk_downloader/ tools/ -> 0 hits
    positive control, same probe shape: 'turnstile' -> 32 files

So the gateway wires every blueprint by hand, in 143 identical six-line try/except blocks (plus
eight near-identical ones) spread across 1,200 lines of an 8,217-line module. Two consequences, and they are the row's reason:

  1. There is no object that knows the route-module set. Nothing can be asked "which blueprint
     modules exist, which registered, which failed" -- the answer only exists as source text.
  2. Every block swallows its failure into a stderr line. A module that stops registering leaves a
     running app with missing routes and a green process: the failure is not data anywhere.

The first test below is importable ON BASE and reads app.py as text, so it fails with an
AssertionError naming the hand-wiring count -- not an ImportError about a module this row adds.
"""
import importlib
import os
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(os.environ.get('BD_ROW1020_TREE') or Path(__file__).resolve().parent.parent)
APP_PY = ROOT / 'bulk_downloader/app.py'

# The block shape the registry owns: try / import one module / register it / except -> stderr.
# MEASURED at base: 143 blocks match it exactly. Eight further registrations share the first three
# lines but not the rest -- they register a SECOND module inside the same try (app_auth+app_oidc,
# app_tags+app_tool_bridge) or write different stderr text. They are deliberately NOT part of this
# row: collapsing a compound block would change what happens when its first module fails.
_UNIFORM_BLOCK = re.compile(
    r'^try:\n    from \. import (app_\w+)\n    \1\.register_routes\(app\)\n'
    r'except Exception as (\w+):\n    import sys as _sys\n'
    r'    _sys\.stderr\.write\(f"\[app\] [^"]*? routes not registered: \{\2\}\\n"\)\n', re.M)
_COMPOUND_KEPT = ('app_settings_center', 'app_envfile_editor', 'app_store_raw_editor', 'app_webhooks',
                  'app_apple', 'app_ytdlp_status', 'app_auth', 'app_tags')


def _registry():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module('bulk_downloader.route_registry')


# ── 1. the row's reason, asserted on a file that exists on base ──────────────────

def test_the_gateway_registers_through_a_registry_not_by_hand():
    src = APP_PY.read_text(encoding='utf-8')
    hand_calls = src.count('.register_routes(app)')
    # positive control: the probe really is reading the gateway and really does find its
    # registrations, so a missing registry below is a MISSING REGISTRY and not a dead read.
    assert hand_calls > 0, "probe read no registrations at all from app.py"
    assert 'route_registry' in src, (
        f"the gateway wires {hand_calls} blueprint registrations by hand and imports no registry, "
        "so nothing can name the route-module set or report which modules failed to register")


def test_the_registry_owns_the_uniform_blocks_and_the_gateway_no_longer_repeats_them():
    src = APP_PY.read_text(encoding='utf-8')
    leftover = [m.group(1) for m in _UNIFORM_BLOCK.finditer(src)]
    assert leftover == [], (
        f"{len(leftover)} uniform try/import/register_routes blocks are still written out by hand "
        f"in app.py: {leftover[:5]}")
    assert 'route_registry.register_all(app' in src
    # The boundary is asserted, not implied: the eight compound registrations stay hand-written,
    # so a later reader can see what this row did NOT take over.
    for kept in _COMPOUND_KEPT:
        assert f'from . import {kept}' in src, f"compound registration for {kept} was removed"


# ── 2. the registry: it must know the set, and report the failures ───────────────

def test_the_module_order_is_pinned_data_and_matches_what_the_gateway_used_to_do():
    reg = _registry()
    order = reg.ROUTE_MODULE_ORDER
    assert isinstance(order, tuple) and len(order) >= 150
    assert len(set(order)) == len(order), "a module registered twice would raise on the endpoint"
    assert all(name.startswith('app_') for name in order)
    for name in order:
        assert (ROOT / f'bulk_downloader/{name}.py').is_file(), f"{name} is named but not present"


def test_the_executed_set_plus_the_hand_registered_set_is_exactly_the_pinned_order():
    """N6-A E1: register_all iterates GATEWAY_ROUTE_MODULES, not ROUTE_MODULE_ORDER. Dropping a
    tuple from the executed set must fail here, because the static import edges that used to make
    such a drop visible to the import-graph gate are gone."""
    reg = _registry()
    executed = [name for name, *_ in reg.GATEWAY_ROUTE_MODULES]
    hand = list(reg.HAND_REGISTERED)
    assert len(set(executed)) == len(executed), "a module executed twice"
    assert not set(executed) & set(hand), sorted(set(executed) & set(hand))
    missing = set(reg.ROUTE_MODULE_ORDER) - set(executed) - set(hand)
    extra = (set(executed) | set(hand)) - set(reg.ROUTE_MODULE_ORDER)
    assert not missing and not extra, (
        "route modules neither registered by the registry nor by hand: %s | unknown: %s"
        % (sorted(missing), sorted(extra)))
    src = APP_PY.read_text(encoding='utf-8')
    not_by_hand = [name for name in hand if f'{name}.register_routes(app)' not in src]
    assert not not_by_hand, f"HAND_REGISTERED names app.py never registers: {not_by_hand}"


def test_register_all_reports_each_module_outcome_rather_than_writing_to_stderr():
    reg = _registry()

    class _App:
        def __init__(self):
            self.registered = []

    calls = []

    class _Mod:
        def __init__(self, name, boom=False):
            self.__name__ = name
            self._boom = boom

        def register_routes(self, app):
            if self._boom:
                raise RuntimeError("no database")
            calls.append(self.__name__)

    good, bad = _Mod('app_good'), _Mod('app_bad', boom=True)
    report = reg.register_all(_App(), modules=[('app_good', good), ('app_bad', bad)])
    assert calls == ['app_good']
    assert report['registered'] == ['app_good']
    assert list(report['failed']) == ['app_bad']
    assert 'no database' in report['failed']['app_bad']
    assert report['counts'] == {'registered': 1, 'failed': 1}


def test_a_failing_module_does_not_stop_the_ones_after_it():
    """The hand-written blocks had exactly this property and it must survive the decomposition:
    one blueprint that cannot import must not take the other 142 down with it."""
    reg = _registry()
    seen = []

    class _Mod:
        def __init__(self, name, boom=False):
            self.__name__, self._boom = name, boom

        def register_routes(self, app):
            if self._boom:
                raise ImportError("optional dependency missing")
            seen.append(self.__name__)

    mods = [('app_a', _Mod('app_a')), ('app_b', _Mod('app_b', boom=True)), ('app_c', _Mod('app_c'))]
    report = reg.register_all(object(), modules=mods)
    assert seen == ['app_a', 'app_c']
    assert report['counts']['registered'] == 2 and report['counts']['failed'] == 1


def test_a_failure_that_cannot_be_written_to_stderr_is_still_reported():
    """ORDERS-0068 (DP-13 at the report write): when stderr itself raises (closed or detached
    under a service manager), the failure line is lost. That loss is data too: the report names
    the module whose failure reached no stream, and registration still carries on past it."""
    reg = _registry()
    seen = []

    class _DeadStream:
        def write(self, text):
            raise ValueError("I/O operation on closed file")

    class _Mod:
        def __init__(self, name, boom=False):
            self.__name__, self._boom = name, boom

        def register_routes(self, app):
            if self._boom:
                raise ImportError("optional dependency missing")
            seen.append(self.__name__)

    mods = [('app_a', _Mod('app_a', boom=True)), ('app_b', _Mod('app_b'))]
    report = reg.register_all(object(), modules=mods, stderr=_DeadStream())
    assert seen == ['app_b']
    assert list(report['failed']) == ['app_a']
    assert report.get('unreported') == ['app_a'], (
        "a failure whose stderr line could not be written vanished from the report: %r" % (report,))

    quiet = reg.register_all(object(), modules=[('app_c', _Mod('app_c', boom=True))],
                             stderr=__import__('io').StringIO())
    assert quiet['unreported'] == [], "a written failure line must not be counted as unreported"


def test_discovery_finds_the_route_modules_on_disk_and_is_not_vacuous():
    """NEGATIVE CONTROL for discovery: it must find the real modules, and must NOT claim a module
    that has no register_routes -- otherwise 'discovered == pinned' would be satisfiable by junk."""
    reg = _registry()
    found = set(reg.discover_route_modules(ROOT / 'bulk_downloader'))
    assert len(found) >= 150
    assert 'app_a11y' in found and 'app_auth' in found
    assert 'app_kernel' not in found or 'register_routes' in (
        ROOT / 'bulk_downloader/app_kernel.py').read_text(encoding='utf-8')
    assert 'storage_tier' not in found and 'route_registry' not in found


def test_the_pinned_order_is_exactly_what_discovery_finds():
    """The registry must not silently drop a route module: pinned data and the filesystem agree,
    and the diff is named in the failure when they do not."""
    reg = _registry()
    found = set(reg.discover_route_modules(ROOT / 'bulk_downloader'))
    pinned = set(reg.ROUTE_MODULE_ORDER)
    assert found - pinned == set(), f"route modules on disk that the registry never registers: {sorted(found - pinned)}"
    assert pinned - found == set(), f"registry names modules that expose no register_routes: {sorted(pinned - found)}"


_ARCHIVE_ANCHOR = re.compile(r"`app\.py(?::\d+)?` \(`_tg_bot\.parse_allowlist`")


def _allowlist_reparse_lines(app_src):
    """Lines of every `_tg_bot.parse_allowlist(...)` call that re-parses `tg_bot_allowlist`, found by
    SYMBOL in the AST (H733): a line-number pin broke main twice (T72 7259, T86 7056->7063) whenever
    a train touched app.py, because the VM gate does not run this test."""
    import ast
    hits = []
    for node in ast.walk(ast.parse(app_src)):
        f = getattr(node, "func", None)
        if (isinstance(node, ast.Call) and isinstance(f, ast.Attribute) and f.attr == "parse_allowlist"
                and isinstance(f.value, ast.Name) and f.value.id == "_tg_bot"
                and any(isinstance(c, ast.Constant) and c.value == "tg_bot_allowlist"
                        for a in node.args for c in ast.walk(a))):
            hits.append(node.lineno)
    return hits


def _anchor_problem(row, app_src):
    """None when archive row 238 names the allowlist re-parse and app.py still performs it."""
    if not _ARCHIVE_ANCHOR.search(row):
        return "row 238 no longer carries an app.py `_tg_bot.parse_allowlist` anchor"
    if not _allowlist_reparse_lines(app_src):
        return "app.py no longer re-parses tg_bot_allowlist via _tg_bot.parse_allowlist"
    return None


def test_the_archived_allowlist_anchor_still_names_the_reparse():
    """T72 drop: shrinking app.py left IMPROVEMENT_BACKLOG_ARCHIVE row 238's `app.py:7259` past EOF
    (bd-freshcheck STALE). The anchor is resolved by symbol, not by the cited line number (H733)."""
    row = next(l for l in (ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG_ARCHIVE.md")
               .read_text(encoding="utf-8").splitlines() if l.startswith("| 238 |"))
    problem = _anchor_problem(row, APP_PY.read_text(encoding="utf-8"))
    assert problem is None, problem


def test_the_allowlist_anchor_survives_app_py_line_shifts_but_not_losing_the_reparse():
    """H733: an app.py edit above the re-parse moves its line; the check must not care. Removing the
    re-parse (negative control) must still fail it, so the symbol probe can say NO."""
    row = next(l for l in (ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG_ARCHIVE.md")
               .read_text(encoding="utf-8").splitlines() if l.startswith("| 238 |"))
    src = APP_PY.read_text(encoding="utf-8")
    base_lines = _allowlist_reparse_lines(src)
    assert base_lines, "positive control: the probe finds the re-parse in the real app.py"
    for shift in ("", "\n", "\n" * 7, "\n" * 400):
        assert _anchor_problem(row, shift + src) is None, f"shift of {shift.count(chr(10))} lines broke it"
    assert _allowlist_reparse_lines("\n" * 7 + src) == [n + 7 for n in base_lines]
    gone = src.replace("_tg_bot.parse_allowlist(", "_tg_bot.parse_nothing(")
    assert _anchor_problem(row, gone) is not None, "negative control: a vanished re-parse must fail"
