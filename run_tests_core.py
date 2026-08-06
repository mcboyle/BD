#!/usr/bin/env python3
"""Minimal pytest-compatible runner for the BulkDownloader test suite.

Used in environments without pytest. Re-implements just enough of the
pytest discovery + fixture protocol to exercise the test files.

NOT a replacement for pytest in production — use `pytest tests/` there.
This exists to catch authoring mistakes during development without
internet access for pip install.
"""
import importlib.util
import inspect
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from contextlib import contextmanager

# v3.43.62: sentinel for monkeypatch shim (attribute didn't exist on target).
_MISSING = object()

# v3.43.15: disable session keep-alive threads in tests. Without this,
# importing bulk_downloader.app spawns daemon threads that try to load
# cookies, call do_login(), etc. — which interacts poorly with the
# isolated tmpdirs that individual tests use.
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

TESTS_DIR = Path(__file__).resolve().parent / "tests"
PKG_ROOT  = Path(__file__).resolve().parent
RUNNER_CLI = PKG_ROOT / "run_tests.py"
sys.path.insert(0, str(PKG_ROOT))


# ── Stub out pytest for the test files. We re-implement only the
# protocol features they actually use: fixture decorator (no-op),
# parametrize (records cases as attribute), skip helpers.
_ABS = abs  # real builtin captured before any `abs=` param shadowing


class _PytestStub:
    @staticmethod
    def fixture(*args, **kwargs):
        # v3.49: track autouse so the runner can invoke these fixtures
        # before each test. Decorator runs in TWO forms:
        #   @pytest.fixture                  → bare; args=(fn,), kwargs={}
        #   @pytest.fixture(autouse=True)    → parens; args=(), kwargs={autouse:True}
        # v3.66.44 (custom-runner gap 2): also stamp `_is_fixture` on
        # every decorated function — bare AND parens form — so the runner
        # can resolve a NAMED local fixture requested by a test's
        # signature (e.g. `def test_x(sandbox_home): ...`), not just
        # autouse ones. Previously the bare form returned the function
        # untouched, leaving named local fixtures unresolvable → the test
        # ran with a missing positional arg and errored.
        autouse = bool(kwargs.get("autouse"))
        def deco(fn):
            fn._is_fixture = True
            if autouse: fn._autouse = True
            return fn
        if len(args) == 1 and callable(args[0]):  # @fixture (no parens)
            # Bare form — no autouse possible since no kwargs were passed
            args[0]._is_fixture = True
            return args[0]
        return deco

    class _MarkMeta(type):
        # v3.66.909: `mark` had only the four attributes it defined, so
        # `@pytest.mark.slow` died at IMPORT with "type object 'mark' has no
        # attribute 'slow'" and took its whole suite with it.
        #
        # An unknown mark is INERT, which is what real pytest does here: this
        # repo has no pytest.ini/pyproject/setup.cfg and no --strict-markers,
        # and tests/conftest.py registers markers via addinivalue_line, so an
        # unregistered mark is metadata plus a warning rather than an error.
        #
        # But a blanket no-op would be a false green for marks that change the
        # VERDICT or the SETUP. Those are refused by name instead: silently
        # dropping `usefixtures` runs a test without the setup it declares, and
        # silently dropping `xfail` reports an expected failure as a failure.
        # Faithful where real pytest is permissive; loud where silence would
        # change the result.
        _VERDICT_CHANGING = ("usefixtures", "xfail", "filterwarnings")

        def __getattr__(cls, name):
            if name.startswith("__"):
                raise AttributeError(name)
            if name in cls._VERDICT_CHANGING:
                raise NotImplementedError(
                    f"the BulkDownloader pytest stub does not implement "
                    f"pytest.mark.{name}; it would change which tests run or "
                    f"how their result is graded. Use real pytest for this "
                    f"suite, or extend the stub deliberately.")

            def _inert(*a, **k):
                # Bare @pytest.mark.foo -> called with the function.
                if len(a) == 1 and not k and callable(a[0]):
                    return a[0]
                # Parameterised @pytest.mark.foo(...) -> return the decorator.
                return lambda fn: fn
            return _inert

    class mark(metaclass=_MarkMeta):
        # v3.66.37: the project's custom `bd_module_wipe` marker, used as
        # `pytestmark = pytest.mark.bd_module_wipe` by ~112 test files
        # (registered in conftest.py, applied by the isolated_bd_home
        # autouse fixture under real pytest). The stub previously lacked
        # it entirely, so every such file raised
        #   AttributeError: type object 'mark' has no attribute 'bd_module_wipe'
        # at import under the custom runner — the same latent-omission
        # class as the PT9 `skip` fix below. It's a sentinel the runner
        # detects on the module's `pytestmark` to mirror the per-test
        # sys.modules wipe (see discover_and_run / run_test module_wipe).
        bd_module_wipe = "bd_module_wipe"

        @staticmethod
        def parametrize(argnames, argvalues, *args, **kwargs):
            # v3.66.44: accept and ignore pytest's `ids=` (and any other)
            # kwarg. Real pytest uses `ids` only for human-readable case
            # labels; the runner labels by index. Previously the stub's
            # signature was (argnames, argvalues) only, so a test using
            # @parametrize(..., ids=...) raised TypeError at import.
            def deco(fn):
                fn._parametrize = (argnames, list(argvalues))
                return fn
            return deco
        @staticmethod
        def skipif(condition, *, reason=""):
            # PT9 fix: this used to be a no-op, which meant
            # @pytest.mark.skipif(True, reason="...") silently ran the
            # test anyway. The bug was invisible whenever the
            # condition happened to be False on the test machine
            # (e.g. ffmpeg was always installed in practice). Now the
            # decorator inspects `condition` at decoration time and,
            # if truthy, replaces the function with one that raises
            # _Skipped — same path pytest.skip() takes.
            def deco(fn):
                if condition:
                    _r = reason or "skipif"
                    def _skipped(*a, **kw):
                        raise _Skipped(_r)
                    _skipped.__name__ = fn.__name__
                    # Preserve any earlier marks (e.g. @parametrize
                    # already applied below this decorator — unusual
                    # but possible).
                    for attr in ("_parametrize", "_autouse"):
                        if hasattr(fn, attr):
                            setattr(_skipped, attr, getattr(fn, attr))
                    return _skipped
                return fn
            return deco
        @staticmethod
        def skip(reason=""):
            # PT9 fix: was missing entirely. @pytest.mark.skip would
            # AttributeError on import (no failure mode in current
            # tree because no test used it, but the omission was a
            # latent — adding @pytest.mark.skip to any test would
            # crash collection).
            def deco(fn):
                _r = reason or "skip"
                def _skipped(*a, **kw):
                    raise _Skipped(_r)
                _skipped.__name__ = fn.__name__
                for attr in ("_parametrize", "_autouse"):
                    if hasattr(fn, attr):
                        setattr(_skipped, attr, getattr(fn, attr))
                return _skipped
            return deco

    @staticmethod
    def param(*values, **kwargs):
        # v3.66.908: absent until now, so all 49 `pytest.param` sites -- across
        # 5 suites, one of them an axis-6 gate -- died at IMPORT with
        # "'_PytestStub' object has no attribute 'param'", and bd-band could
        # not run them at all.
        #
        # RETURN THE TUPLE, NOT values[0]. Real pytest returns a ParameterSet
        # whose .values tuple is zipped against argnames, and the injection in
        # discover_and_run already wraps a scalar and zips a tuple -- so the
        # tuple IS the correct semantics and that code needs no change. The
        # tempting `return values[0]` silently feeds one value to a
        # multi-argument test: measured over `git ls-files -- 'tests/*.py'`,
        # 45 of the 49 sites carry 2-5 values, so it would be wrong nearly
        # everywhere and right only on the 4 single-value sites.
        #
        # `id=` is accepted and ignored, exactly as parametrize ignores `ids=`,
        # because the runner labels cases by index. Anything else is REFUSED:
        # `marks=` would change WHICH cases run, and a stub that quietly drops
        # it turns a skipped case into a silently-executed one. An unimplemented
        # feature that fails loudly is a stub; one that pretends is a false
        # green. Measured: `id` is the only kwarg in use, on 49 of 49 sites.
        unsupported = sorted(set(kwargs) - {"id"})
        if unsupported:
            raise NotImplementedError(
                "the BulkDownloader pytest stub does not implement "
                f"pytest.param({', '.join(unsupported)}=...); it would change "
                "which cases run. Use real pytest for this suite, or extend "
                "the stub deliberately.")
        return values

    @staticmethod
    def importorskip(modname, minversion=None, reason=None):
        # v3.66.909: absent, so four suites died at IMPORT -- and because the
        # call sites are module-level, none of their tests ran at all.
        #
        # Returns the MODULE. A stub returning None would turn "this optional
        # dependency is missing" into an AttributeError inside the test body,
        # which reads as a code defect rather than a skip.
        import importlib
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            raise _Skipped(reason or f"could not import {modname!r}")
        if minversion is not None:
            have = getattr(mod, "__version__", None)
            if have is None:
                raise _Skipped(
                    f"{modname!r} has no __version__ to compare against "
                    f"minversion={minversion!r}")
            # Numeric-tuple compare; good enough for the dotted releases in
            # use, and it refuses rather than guessing on anything else.
            def _parts(v):
                try:
                    return tuple(int(p) for p in str(v).split("."))
                except ValueError:
                    raise _Skipped(
                        f"cannot compare {modname!r} version {v!r} against "
                        f"minversion={minversion!r} in the stub")
            if _parts(have) < _parts(minversion):
                raise _Skipped(
                    f"{modname!r} is {have}, need >= {minversion}")
        return mod

    @staticmethod
    def skip(reason=""): raise _Skipped(reason)

    @staticmethod
    def fail(reason="", pytrace=True):
        # v3.66.870: the stub had skip/skipif/approx but no `fail`, so all 50
        # pytest.fail sites in tests/ raised AttributeError under the minimal
        # runner. The test still FAILED, so this never hid a defect -- but the
        # DIAGNOSTIC was destroyed and replaced by a harness error, which is
        # the shape that makes people debug the wrong thing (CLAUDE.md 2a).
        # AssertionError is already the runner's failure path, so no test
        # changes direction. Signature verified safe: zero of the 50 call
        # sites pass msg=/reason=/pytrace= by keyword, so a positional-only
        # caller cannot turn this into a TypeError.
        raise AssertionError(reason)

    @staticmethod
    def approx(expected, rel=None, abs=None):
        # v3.66.37: minimal pytest.approx shim. The stub previously
        # lacked it, so any test using `== pytest.approx(x)` raised
        # AttributeError once it actually ran (was masked while the file
        # import-crashed on the bd_module_wipe marker). Default tolerance
        # mirrors pytest: rel=1e-6, abs=1e-12, combined as max.
        _rel = 1e-6 if rel is None else rel
        _abs = 1e-12 if abs is None else abs

        class _Approx:
            def __init__(self, exp): self.exp = exp
            def _close(self, a, b):
                try:
                    return _ABS(a - b) <= max(_abs, _rel * _ABS(b))
                except TypeError:
                    return a == b
            def __eq__(self, other):
                if isinstance(self.exp, (list, tuple)) and isinstance(other, (list, tuple)):
                    return (len(self.exp) == len(other)
                            and all(self._close(o, e) for o, e in zip(other, self.exp)))
                return self._close(other, self.exp)
            def __ne__(self, other): return not self.__eq__(other)
            def __repr__(self): return f"approx({self.exp!r})"
        return _Approx(expected)

    @staticmethod
    def raises(*a, **k):
        # Used as context manager — emulate. Exposes `.value`/`.type`/
        # `.traceback` like pytest's ExceptionInfo so tests that inspect
        # the raised exception (e.g. `str(excinfo.value)`) work under the
        # shim instead of failing with AttributeError on `.value`.
        class Ctx:
            def __init__(self, exc):
                self.exc = exc
                self.value = None
                self.type = None
                self.traceback = None
            def __enter__(self): return self
            def __exit__(self, et, ev, tb):
                if et is not None and issubclass(et, self.exc):
                    self.value, self.type, self.traceback = ev, et, tb
                    return True
                return False
        return Ctx(a[0] if a else Exception)


class _Skipped(Exception): pass


@contextmanager
def activated_pytest_stub():
    """Temporarily bind the BulkDownloader pytest stub for runner execution.

    Real pytest and foreign bindings are never replaced. Nested activation is
    allowed only when this module's own stub is already active.
    """
    existing = sys.modules.get("pytest", _MISSING)
    if existing is _MISSING:
        stub = _PytestStub()
        sys.modules["pytest"] = stub
    elif isinstance(existing, _PytestStub):
        stub = existing
    else:
        raise RuntimeError(
            "refusing to replace an existing non-BulkDownloader pytest module")
    try:
        yield stub
    finally:
        if existing is _MISSING:
            sys.modules.pop("pytest", None)
        else:
            sys.modules["pytest"] = existing


class _CapSys:
    """Minimal pytest `capsys` fixture shim. `readouterr()` returns an
    object with `.out`/`.err` (the text captured since the last call) and
    resets the buffers, matching pytest's semantics."""
    def __init__(self):
        import io
        self._out = io.StringIO()
        self._err = io.StringIO()

    def readouterr(self):
        out = self._out.getvalue()
        err = self._err.getvalue()
        self._out.seek(0); self._out.truncate(0)
        self._err.seek(0); self._err.truncate(0)
        class _Captured:
            def __init__(self, o, e): self.out, self.err = o, e
        return _Captured(out, err)


# v3.49: hoisted to module scope so autouse fixtures + tests can share
# a single class definition rather than re-creating it per-test.
class _MonkeyPatch:
    def __init__(self):
        self._undo = []
    def setattr(self, target, name, value=None, raising=True):
        # Support both setattr(obj, 'name', value) and
        # setattr('mod.path.attr', value) forms; we only
        # use the obj+name form internally.
        if value is None and not callable(name):
            # Single-arg dotted-path form: not implemented.
            raise NotImplementedError(
                "monkeypatch.setattr dotted-path form not implemented in shim"
            )
        orig = getattr(target, name) if hasattr(target, name) else _MISSING
        self._undo.append((target, name, orig))
        setattr(target, name, value)
    def setitem(self, dic, name, value):
        # pytest monkeypatch.setitem(dic, key, value): set a dict item and
        # restore it (or delete it) on undo.
        orig = dic[name] if name in dic else _MISSING
        self._undo.append(("__item__", (dic, name), orig))
        dic[name] = value
    def delitem(self, dic, name, raising=True):
        orig = dic[name] if name in dic else _MISSING
        if orig is _MISSING and raising:
            raise KeyError(name)
        self._undo.append(("__item__", (dic, name), orig))
        dic.pop(name, None)
    def setenv(self, name, value):
        orig = os.environ.get(name, _MISSING)
        self._undo.append(("__env__", name, orig))
        os.environ[name] = value
    def delenv(self, name, raising=True):
        orig = os.environ.get(name, _MISSING)
        self._undo.append(("__env__", name, orig))
        os.environ.pop(name, None)
    def chdir(self, path):
        # v3.49: support monkeypatch.chdir (used by v3.50 library tests)
        prev = os.getcwd()
        self._undo.append(("__cwd__", prev, None))
        os.chdir(str(path))
    def syspath_prepend(self, path):
        self._undo.append(("__syspath__", str(path), None))
        sys.path.insert(0, str(path))
    def undo(self):
        while self._undo:
            target, name, orig = self._undo.pop()
            if target == "__env__":
                if orig is _MISSING:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = orig
            elif target == "__cwd__":
                try: os.chdir(name)
                except OSError: pass
            elif target == "__syspath__":
                try: sys.path.remove(name)
                except ValueError: pass
            elif target == "__item__":
                d, k = name
                if orig is _MISSING:
                    d.pop(k, None)
                else:
                    d[k] = orig
            else:
                if orig is _MISSING:
                    try: delattr(target, name)
                    except AttributeError: pass
                else:
                    setattr(target, name, orig)


# v3.66.909: `pytest.MonkeyPatch()` is the CONSTRUCTOR form, used where a test
# needs a patcher outside the fixture protocol (a helper, or a with-block).
# Bound here rather than inside _PytestStub because _MonkeyPatch is defined
# after that class body -- an in-class `MonkeyPatch = _MonkeyPatch` raises
# NameError at import. Pointing the name at the SAME class the `monkeypatch`
# fixture uses means the two forms cannot drift apart.
_PytestStub.MonkeyPatch = _MonkeyPatch


# ── Fixture implementations (mirror conftest.py) ────────────────────────
@contextmanager
def make_clean_workdir():
    """Mimics the clean_workdir fixture.

    v3.62.1: on Windows the app opens logs/bulk_downloader.log inside
    the workdir via a logging FileHandler. Windows refuses to delete a
    file that is still open, so TemporaryDirectory cleanup could crash
    the run with WinError 32. Two-part hardening: close + remove all
    logging handlers in `finally` before teardown, and pass
    ignore_cleanup_errors so a stuck file degrades to a harmless
    leftover folder instead of crashing the suite (Python 3.10+)."""
    import logging
    import os
    try:
        tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:
        tmpdir = tempfile.TemporaryDirectory()  # Python < 3.10
    with tmpdir as tmp:
        prev = os.getcwd()
        os.chdir(tmp)
        try:
            yield Path(tmp)
        finally:
            os.chdir(prev)
            for name in [""] + [n for n in
                                list(logging.Logger.manager.loggerDict)
                                if n == "bulk_downloader"
                                or n.startswith("bulk_downloader.")]:
                lg = logging.getLogger(name)
                for h in list(getattr(lg, "handlers", [])):
                    try:
                        h.close()
                    except Exception:
                        pass
                    try:
                        lg.removeHandler(h)
                    except Exception:
                        pass


@contextmanager
def make_fresh_app(workdir):
    """Mimics the fresh_app fixture."""
    if "bulk_downloader.app" in sys.modules:
        app_mod = sys.modules["bulk_downloader.app"]
        app_mod.runners.clear()
        app_mod.s_cfg.clear()
        app_mod.s_meta.clear()
        app_mod._app_cfg.clear()
        app_mod._app_cfg["global_max_concurrent"] = 0
        if hasattr(app_mod, "_rate_buckets"):
            app_mod._rate_buckets.clear()
    from bulk_downloader.app import app, _load_app_config
    from bulk_downloader.db import db_init
    db_init()
    app.config["TESTING"] = True
    _load_app_config()
    client = app.test_client()
    try:
        yield client
    finally:
        if "bulk_downloader.app" in sys.modules:
            app_mod = sys.modules["bulk_downloader.app"]
            for sid in list(app_mod.runners.keys()):
                try:
                    app_mod.runners[sid].stop()
                    app_mod.runners[sid]._stop_auto_retry()
                except Exception: pass
            app_mod.runners.clear()


@contextmanager
def make_aiassist_module():
    from bulk_downloader import aiassist
    aiassist.configure(endpoint="http://localhost:11434",
                       model_vision="qwen2.5vl:7b",
                       model_text="qwen2.5:7b",
                       enabled=False)
    if "_health" in dir(aiassist):
        aiassist._health["call_count"] = 0
        aiassist._health["fail_count"] = 0
        aiassist._health["recent_latencies"] = []
    yield aiassist


# ── Test runner ────────────────────────────────────────────────────────
def run_test(test_fn, owner=None, autouse_fixtures=(), module_wipe=False,
             named_fixtures=None):
    """Invoke a test function with appropriate fixtures.

    module_wipe: when True (the file carries
    ``pytestmark = pytest.mark.bd_module_wipe``), snapshot and drop all
    ``bulk_downloader.*`` modules from sys.modules around the test, so an
    import inside the test body re-reads env vars at load time — mirroring
    conftest.py's isolated_bd_home behaviour under real pytest."""
    sig = inspect.signature(test_fn)
    params = list(sig.parameters)

    # Drop self for methods
    if owner is not None and params and params[0] == "self":
        params = params[1:]

    # Build fixture stack
    with make_clean_workdir() as workdir:
        # Pre-create screenshots dir so SiteRunner can mkdir its subdirs.
        # Without this, runner construction fails the moment any site is
        # created, because the runner does `self._ss_dir.mkdir()` not
        # `mkdir(parents=True)`.
        (workdir / "screenshots").mkdir(exist_ok=True)

        kwargs = {}
        # autouse setup fixture. Recognize both the deprecated `setup`/`teardown`
        # form and the modern pytest `setup_method`/`teardown_method` form. The
        # latter was added in v3.43.60 to support tests that use class-based
        # state isolation patterns portable to real pytest.
        teardown_fn = None
        if owner is not None:
            setup_fn = getattr(owner, "setup_method", None)
            if not callable(setup_fn):
                setup_fn = getattr(owner, "setup", None)
            teardown_fn = getattr(owner, "teardown_method", None)
            if not callable(teardown_fn):
                teardown_fn = getattr(owner, "teardown", None)
            if callable(setup_fn):
                try:
                    setup_fn()
                except _Skipped:
                    # pytest.skip() inside setup is a legitimate "this
                    # environment can't run this test", not a failure.
                    # Re-raise so the caller treats it as a skip.
                    raise
                except Exception as e:
                    return False, f"setup failed: {type(e).__name__}: {e}"

        ctx = ctx_ai = None
        # v3.43.62 (tmp_path/monkeypatch) and v3.66.37 (capsys): shims so
        # pytest-style tests work under this runner without real pytest. They
        # mirror the canonical pytest API surface enough for our tests to be
        # portable both ways.
        _tmp_path_dir = None
        _monkeypatch_obj = None
        _capsys_obj = None
        _capsys_saved = None

        # ONE SHIM TABLE, READ BY ALL THREE RESOLUTION PATHS.
        #
        # v3.66.885: this used to be three tables. A TEST function's params
        # got all six names below; the autouse loop and `_resolve_named` got
        # `tmp_path` and `monkeypatch` only. So `clean_workdir` resolved for a
        # test and was silently DROPPED for a fixture, and the fixture call
        # then raised TypeError -- CLAUDE.md section 0's shape, two resolution
        # paths for one name with only one of them complete. Measured at
        # v3.66.883: `bd-band` manufactured 80 failing cases across 22 suites
        # that pass 413/413 under real pytest, while section 4 mandates
        # `bd-band` as the band tool.
        #
        # It is ONE table rather than three-with-clean_workdir-added because
        # the defect is the asymmetry, not the missing name. Adding the one
        # name that was reported would have left `fresh_app`, `aiassist_module`
        # and `capsys` broken on the fixture paths -- and `_resolve_named`'s
        # own docstring already claimed capsys support it did not have, so the
        # prose was wrong in the same direction the code was.
        #
        # Ordering is preserved from the original by iterating _SHIM_NAMES
        # rather than `params`: capsys redirects sys.stdout, so it must be
        # created after fresh_app, and a dict-order change here would be an
        # invisible behaviour change.
        _SHIM_NAMES = ("clean_workdir", "fresh_app", "aiassist_module",
                       "tmp_path", "monkeypatch", "capsys")
        _shim_cache = {}

        def _shim(pname):
            """Return the built-in shim named `pname`, or _MISSING.

            Created at most once per test and memoised, so a fixture and the
            test body that both ask for `monkeypatch` share one object and one
            teardown -- which is what makes the single table safe. Every
            producer below assigns the same local the `finally` block already
            tears down, so a shim created for a FIXTURE is cleaned up exactly
            as one created for a test always was.
            """
            nonlocal ctx, ctx_ai, _tmp_path_dir, _monkeypatch_obj
            nonlocal _capsys_obj, _capsys_saved
            if pname in _shim_cache:
                return _shim_cache[pname]
            if pname == "clean_workdir":
                val = workdir
            elif pname == "fresh_app":
                ctx = make_fresh_app(workdir)
                val = ctx.__enter__()
            elif pname == "aiassist_module":
                ctx_ai = make_aiassist_module()
                val = ctx_ai.__enter__()
            elif pname == "tmp_path":
                _tmp_path_dir = Path(workdir) / "tmp_path"
                _tmp_path_dir.mkdir(parents=True, exist_ok=True)
                val = _tmp_path_dir
            elif pname == "monkeypatch":
                _monkeypatch_obj = _MonkeyPatch()
                val = _monkeypatch_obj
            elif pname == "capsys":
                _capsys_obj = _CapSys()
                _capsys_saved = (sys.stdout, sys.stderr)
                sys.stdout, sys.stderr = _capsys_obj._out, _capsys_obj._err
                val = _capsys_obj
            else:
                return _MISSING
            _shim_cache[pname] = val
            return val

        for _pname in _SHIM_NAMES:
            if _pname in params:
                kwargs[_pname] = _shim(_pname)

        # v3.49: run autouse fixtures. They typically use monkeypatch +
        # tmp_path to isolate test state. We need to invoke them BEFORE
        # the test body runs, generator-style: advance to yield, then
        # the test runs, then advance past yield for cleanup.
        #
        # IMPORTANT: a fixture may need `monkeypatch`/`tmp_path` even
        # when the TEST function does not. Those go into `fx_kwargs`
        # (passed to the fixture) — never into `kwargs` (passed to the
        # test) unless the test's own signature asks for them.
        autouse_generators = []
        for fixture_fn in autouse_fixtures:
            fixture_sig = inspect.signature(fixture_fn)
            fx_kwargs = {}
            # Every shim the TEST path offers, not just two of them. The
            # memoised `_shim` hands back the same object the test body gets,
            # so a monkeypatch applied in a fixture is undone by the same
            # teardown -- which is what the hand-rolled version above was
            # reaching for when it reused `_monkeypatch_obj`.
            for _pn in fixture_sig.parameters:
                _v = _shim(_pn)
                if _v is not _MISSING:
                    fx_kwargs[_pn] = _v
            try:
                gen = fixture_fn(**fx_kwargs)
                if inspect.isgenerator(gen):
                    next(gen)  # advance to the yield
                    autouse_generators.append(gen)
            except _Skipped:
                raise
            except Exception as e:
                return False, f"autouse fixture {fixture_fn.__name__} failed: {e}"

        # v3.66.44 (gap 2): resolve NAMED local fixtures the test
        # requested by signature (e.g. `def test_x(sandbox_home):`).
        # Mirrors the autouse path: invoke the fixture, supplying its own
        # tmp_path/monkeypatch/capsys/other-named-fixture deps, advance a
        # generator to its yield, expose the yielded value to the test,
        # and register teardown. Resolved AFTER the built-in shims so the
        # fixture can depend on tmp_path/monkeypatch, and after autouse so
        # ordering matches pytest (autouse first).
        named_generators = []
        if named_fixtures:
            def _resolve_named(fname, _seen):
                if fname in kwargs:
                    return kwargs[fname]
                if fname not in named_fixtures or fname in _seen:
                    return _MISSING
                _seen.add(fname)
                ffn = named_fixtures[fname]
                fx_kwargs = {}
                for pn in inspect.signature(ffn).parameters:
                    # A built-in shim wins over a same-named local fixture,
                    # which is the precedence the hand-rolled chain had.
                    _v = _shim(pn)
                    if _v is not _MISSING:
                        fx_kwargs[pn] = _v
                    elif pn in named_fixtures:
                        dep = _resolve_named(pn, _seen)
                        if dep is not _MISSING:
                            fx_kwargs[pn] = dep
                produced = ffn(**fx_kwargs)
                if inspect.isgenerator(produced):
                    val = next(produced)
                    named_generators.append(produced)
                    return val
                return produced
            for pname in params:
                if pname in kwargs or pname not in named_fixtures:
                    continue
                try:
                    val = _resolve_named(pname, set())
                except _Skipped:
                    raise
                except Exception as e:
                    return False, (f"named fixture {pname} failed: "
                                   f"{type(e).__name__}: {e}")
                if val is not _MISSING:
                    kwargs[pname] = val

        # bd_module_wipe: drop bulk_downloader.* so the test's own imports
        # re-read env at load time. Snapshot for restore in finally.
        _wipe_saved = None
        if module_wipe:
            _wipe_saved = {k: v for k, v in sys.modules.items()
                           if k == "bulk_downloader"
                           or k.startswith("bulk_downloader.")}
            for _m in list(sys.modules):
                if _m == "bulk_downloader" or _m.startswith("bulk_downloader."):
                    del sys.modules[_m]

        try:
            if owner is not None:
                test_fn(owner, **kwargs)
            else:
                test_fn(**kwargs)
            return True, ""
        except _Skipped:
            # pytest.skip() / pytest.skipif() inside the test body —
            # propagate so the outer except _Skipped in discover_and_run
            # records it as SKIP, not FAIL.
            raise
        except Exception as e:
            return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        finally:
            # v3.66.44 (gap 2): drain named-fixture generators first,
            # in reverse order (a named fixture may depend on an autouse
            # one, so it tears down before its dependency).
            for gen in reversed(named_generators):
                try:
                    next(gen, None)
                except StopIteration:
                    pass
                except Exception:
                    pass
            # v3.49: drain autouse fixture generators in reverse order
            # (so later fixtures clean up before earlier ones).
            for gen in reversed(autouse_generators):
                try:
                    next(gen, None)  # advance past yield → fixture teardown
                except StopIteration:
                    pass
                except Exception:
                    pass
            if ctx:
                try: ctx.__exit__(None, None, None)
                except Exception: pass
            if ctx_ai:
                try: ctx_ai.__exit__(None, None, None)
                except Exception: pass
            # v3.43.62: undo monkeypatch shim. Always best-effort -- a
            # broken patch during teardown should not turn a passing
            # test into a failure.
            if _monkeypatch_obj is not None:
                try: _monkeypatch_obj.undo()
                except Exception: pass
            # v3.66.37: restore stdout/stderr if capsys was injected.
            if _capsys_saved is not None:
                sys.stdout, sys.stderr = _capsys_saved
            # v3.43.60: invoke pytest-style teardown_method/teardown if defined,
            # mirroring the setup_method invocation above. Errors here aren't
            # surfaced as test failures because the test itself already passed
            # (or already failed) at this point — teardown is best-effort
            # cleanup.
            if teardown_fn is not None and callable(teardown_fn):
                try: teardown_fn()
                except Exception: pass
            # bd_module_wipe restore: drop whatever the test imported and
            # put back the pre-test snapshot, so the next test starts clean.
            if module_wipe and _wipe_saved is not None:
                for _m in [m for m in list(sys.modules)
                           if m == "bulk_downloader"
                           or m.startswith("bulk_downloader.")]:
                    del sys.modules[_m]
                sys.modules.update(_wipe_saved)


def discover_and_run(test_file):
    """Import a test file and run every test_* function/method."""
    spec = importlib.util.spec_from_file_location(
        f"test_{test_file.stem}", test_file)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        # NOTE: must be a 4-tuple (name, err, ok, duration) to match
        # every other result row this function emits. A 3-tuple here
        # crashes the serial-retry classifier in _retry_failures_serial,
        # which unpacks `n, _, ok, _` (was the F1 bug, fixed v3.66.31).
        return [(test_file.name, f"IMPORT ERROR: {e}", False, 0.0)]

    # bd_module_wipe: a file sets `pytestmark = pytest.mark.bd_module_wipe`
    # (the stub resolves that to the sentinel string). pytestmark may be a
    # single mark or a list of them, mirroring real pytest.
    _pm = getattr(mod, "pytestmark", None)
    _module_wipe = (_pm == "bd_module_wipe"
                    or (isinstance(_pm, (list, tuple))
                        and "bd_module_wipe" in _pm))

    # v3.49: discover module-level @pytest.fixture(autouse=True) functions.
    # We can't tell from the function alone if it was decorated with
    # autouse=True (the @fixture decorator is a no-op in our stub), so we
    # look for the magic marker we set in the stub: any function whose
    # name appears in `_AUTOUSE_FIXTURES` set on the stub. Easier in
    # practice: just inspect the function's defaults / params for the
    # fixture-style signature (`monkeypatch`, `tmp_path`) and infer.
    # Better: have the stub record autouse explicitly.
    autouse_fixtures = []
    named_fixtures = {}
    for name in dir(mod):
        obj = getattr(mod, name)
        # v3.66.885: the MARKER is the discriminator, not the NAME. This used
        # to also require `not name.startswith("_")`, so an underscore-prefixed
        # fixture was never collected and therefore never invoked -- and a
        # suite that declared it PASSED without the setup it asked for. That is
        # worse than the TypeError above precisely because it is silent, and it
        # diverges from real pytest, which collects by decorator and does not
        # care about a leading underscore.
        #
        # The `callable(obj) and getattr(obj, ...)` pair is still load-bearing:
        # dropping the underscore test WITHOUT keeping the marker test would
        # make every private helper in a test module injectable, so a test
        # whose parameter happened to share a helper's name would silently
        # receive the function instead of failing.
        if callable(obj) and getattr(obj, "_autouse", False):
            autouse_fixtures.append(obj)
        # v3.66.44 (gap 2): index named local fixtures by name so a test
        # can request one via its signature. Autouse fixtures run
        # unconditionally and aren't passed by name, so exclude them here.
        elif callable(obj) and getattr(obj, "_is_fixture", False):
            named_fixtures[name] = obj

    results = []
    for name in dir(mod):
        obj = getattr(mod, name)
        if name.startswith("Test") and inspect.isclass(obj):
            # Test class — find methods
            instance = obj()
            # Pytest fixtures with autouse=True
            for mname in dir(obj):
                if mname == "setup":
                    fixture = getattr(obj, mname)
                    if hasattr(fixture, "__wrapped__"): continue
            for mname in sorted(dir(obj)):
                if mname.startswith("test_"):
                    method = getattr(obj, mname)
                    full = f"{name}::{mname}"
                    # Handle parametrize-marked tests
                    if hasattr(method, "_parametrize"):
                        argnames, argvalues = method._parametrize
                        argnames_list = ([s.strip() for s in argnames.split(",")]
                                         if isinstance(argnames, str) else list(argnames))
                        for vi, vals in enumerate(argvalues):
                            if not isinstance(vals, (tuple, list)): vals = (vals,)
                            extra_kwargs = dict(zip(argnames_list, vals))
                            # Build a wrapper with a signature that EXCLUDES
                            # the parametrized arguments, so run_test's
                            # fixture introspection picks up the right
                            # remaining params (self + fixtures).
                            sig = inspect.signature(method)
                            new_params = [p for p in sig.parameters.values()
                                          if p.name not in argnames_list]
                            def make_bound(_m, _extra):
                                def _bound(*a, **kw):
                                    return _m(*a, **kw, **_extra)
                                _bound.__signature__ = sig.replace(parameters=new_params)
                                return _bound
                            wrapped = make_bound(method, extra_kwargs)
                            label = f"{full}[{vi}]"
                            # T49: time each parametrized case (incl.
                            # fixture setup/teardown).
                            _t0 = time.perf_counter()
                            try:
                                ok, err = run_test(wrapped, owner=instance,
                                                   autouse_fixtures=autouse_fixtures,
                                                   module_wipe=_module_wipe,
                                                   named_fixtures=named_fixtures)
                            except _Skipped as e:
                                _dt = time.perf_counter() - _t0
                                results.append((label, f"SKIP ({e})", None, _dt)); continue
                            _dt = time.perf_counter() - _t0
                            results.append((label, err if not ok else "", ok, _dt))
                        continue
                    # T49: time the non-parametrized class method.
                    _t0 = time.perf_counter()
                    try:
                        ok, err = run_test(method, owner=instance,
                                           autouse_fixtures=autouse_fixtures,
                                           module_wipe=_module_wipe,
                                                   named_fixtures=named_fixtures)
                    except _Skipped as e:
                        _dt = time.perf_counter() - _t0
                        results.append((full, f"SKIP ({e})", None, _dt)); continue
                    _dt = time.perf_counter() - _t0
                    results.append((full, err if not ok else "", ok, _dt))
        elif name.startswith("test_") and inspect.isfunction(obj):
            # v3.66.44 (custom-runner gap 1): expand @parametrize on
            # module-level test functions. Previously only class-method
            # parametrize was expanded; a module-level parametrized test
            # was invoked ONCE with no args → TypeError. Mirror the
            # class-method expansion block above (minus owner=instance).
            if hasattr(obj, "_parametrize"):
                argnames, argvalues = obj._parametrize
                argnames_list = ([s.strip() for s in argnames.split(",")]
                                 if isinstance(argnames, str) else list(argnames))
                for vi, vals in enumerate(argvalues):
                    if not isinstance(vals, (tuple, list)): vals = (vals,)
                    extra_kwargs = dict(zip(argnames_list, vals))
                    sig = inspect.signature(obj)
                    new_params = [p for p in sig.parameters.values()
                                  if p.name not in argnames_list]
                    def make_bound(_m, _extra, _sig, _np):
                        def _bound(*a, **kw):
                            return _m(*a, **kw, **_extra)
                        _bound.__signature__ = _sig.replace(parameters=_np)
                        return _bound
                    wrapped = make_bound(obj, extra_kwargs, sig, new_params)
                    label = f"{name}[{vi}]"
                    _t0 = time.perf_counter()
                    try:
                        ok, err = run_test(wrapped,
                                           autouse_fixtures=autouse_fixtures,
                                           module_wipe=_module_wipe,
                                                   named_fixtures=named_fixtures)
                    except _Skipped as e:
                        _dt = time.perf_counter() - _t0
                        results.append((label, f"SKIP ({e})", None, _dt)); continue
                    _dt = time.perf_counter() - _t0
                    results.append((label, err if not ok else "", ok, _dt))
                continue
            # T49: time module-level test functions.
            _t0 = time.perf_counter()
            try:
                ok, err = run_test(obj, autouse_fixtures=autouse_fixtures,
                               module_wipe=_module_wipe,
                                                   named_fixtures=named_fixtures)
            except _Skipped as e:
                # PT9 fix: mirror the class-method paths above. Without
                # this catch, pytest.skip() inside a module-level test
                # function propagates out of discover_and_run and
                # crashes the runner on the (ok, err) unpack. Class-
                # method paths (parametrized and not) already wrap
                # run_test with this same except — the bug was the
                # module-level path being the only one that didn't.
                _dt = time.perf_counter() - _t0
                results.append((name, f"SKIP ({e})", None, _dt)); continue
            _dt = time.perf_counter() - _t0
            results.append((name, err if not ok else "", ok, _dt))

    # v3.66.796: invoke the module-level `teardown_module` hook, mirroring real
    # pytest (and the setup_method/teardown_method handling in run_test).
    #
    # Six suites use the `_isolated_bd` idiom -- set BD_INSTALL_DIR in setup,
    # restore it in teardown_module. Real pytest calls that hook, so the stash
    # suite is green; this runner did not, so the env leaked out of those files
    # and every downstream DB suite wrote into the leaking suite's tmpdir (71
    # band failures the binding gate never had). `bd_module_wipe` could not
    # cover it: that restores sys.modules, not os.environ.
    #
    # Best-effort, exactly like teardown_method above: the tests have already
    # been scored by this point, so a raising cleanup hook must not convert
    # passing tests into failures -- otherwise the fix becomes a new way to
    # break the band.
    _td_mod = getattr(mod, "teardown_module", None)
    if callable(_td_mod):
        try:
            try:
                _td_mod(mod)          # pytest passes the module object
            except TypeError:
                _td_mod()             # tolerate a zero-arg definition
        except Exception:
            pass
    return results


# Per-test-file wall timeout for subprocess execution (parallel AND the
# serial retry). Env-tunable so tests can exercise the timeout path.
_FILE_TIMEOUT_S = int(os.environ.get("BD_TEST_FILE_TIMEOUT", "900"))

# C4 (12.1): SOFT per-file wall-time budget. Unlike _FILE_TIMEOUT_S (a hard
# kill that fails the file), this only SURFACES over-budget files in the report
# so a newly-slow file is visible long before it hits the hard timeout. It never
# changes pass/fail. Env-tunable; 0 disables the surfacing.
_FILE_BUDGET_S = int(os.environ.get("BD_TEST_FILE_BUDGET", "120"))


def _files_over_budget(all_durations, budget_s):
    """Sum per-file wall time from ``(duration_seconds, file, test)`` rows and
    return ``[(file, total_seconds), ...]`` for files whose total STRICTLY
    exceeds ``budget_s``, slowest-first. Informational only -- callers must not
    let this affect the exit code. ``budget_s <= 0`` returns ``[]`` (disabled).
    """
    if not budget_s or budget_s <= 0:
        return []
    totals = {}
    for dur, fname, _n in all_durations:
        totals[fname] = totals.get(fname, 0.0) + float(dur)
    over = [(f, round(t, 3)) for f, t in totals.items() if t > budget_s]
    over.sort(key=lambda x: x[1], reverse=True)
    return over


# C4 (12.1 flake-registry): persist the ephemeral flake classifications from
# _retry_failures_serial so a CHRONIC flake is trackable across runs instead of
# re-discovered each time. Pure helpers + opt-in I/O, mirroring _files_over_budget:
# informational-only (never changes pass/fail), default-OFF. The registry is
# written ONLY when BD_FLAKE_REGISTRY names a path -- so the release band (which
# does not set it) sees zero tracked-tree churn and no behavior change.
_FLAKE_REGISTRY_PATH = os.environ.get("BD_FLAKE_REGISTRY", "")
_FLAKE_CHRONIC_THRESHOLD = int(os.environ.get("BD_FLAKE_CHRONIC_THRESHOLD", "3"))
_FLAKE_MAX_AGE_DAYS = int(os.environ.get("BD_FLAKE_MAX_AGE_DAYS", "30"))


def _update_flake_registry(registry, flaky_ids, now):
    """Return a NEW registry (never mutate the input) with each id in
    ``flaky_ids`` incremented. Registry shape:
    ``{"<file> :: <test>": {"count": int, "first_seen": ts, "last_seen": ts}}``.
    An empty ``flaky_ids`` is a no-op (returns an equal copy)."""
    reg = {k: dict(v) for k, v in registry.items()}
    for fid in flaky_ids:
        e = reg.get(fid)
        if e is None:
            reg[fid] = {"count": 1, "first_seen": now, "last_seen": now}
        else:
            reg[fid] = {
                "count": int(e.get("count", 0)) + 1,
                "first_seen": e.get("first_seen", now),   # preserved
                "last_seen": now,                         # advanced
            }
    return reg


def _chronic_flakes(registry, threshold):
    """``[(id, count), ...]`` for entries whose count is >= ``threshold``,
    count-descending (ties by id). ``threshold <= 0`` disables (returns [])."""
    if not threshold or threshold <= 0:
        return []
    items = [(fid, int(e.get("count", 0))) for fid, e in registry.items()
             if int(e.get("count", 0)) >= threshold]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items


def _prune_flake_registry(registry, now, max_age_days=30):
    """MNT-3 (v3.66.660): return a NEW registry with entries whose ``last_seen``
    is older than ``max_age_days`` dropped, so the flake quarantine is EXPIRING --
    a test that stopped flaking ages out instead of lingering forever (the spec's
    "visible + expiring, never silent"). ``max_age_days <= 0`` disables pruning
    (returns the input unchanged). A malformed entry lacking ``last_seen`` is
    treated as fresh (kept) -- prune never drops on ambiguity, never mutates the
    input, never raises."""
    if not max_age_days or max_age_days <= 0:
        return registry
    cutoff = float(now) - float(max_age_days) * 86400.0
    out = {}
    for fid, e in registry.items():
        ls = e.get("last_seen") if isinstance(e, dict) else None
        if ls is None or float(ls) >= cutoff:
            out[fid] = dict(e) if isinstance(e, dict) else e
    return out


# MNT-3 (v3.66.664, choice B): flake quarantine, isolation-first. Both tiers are
# INERT unless opted into -- the release band sets neither env var, so on-stash
# capture.sh always runs the full suite and never suppresses anything.
_QUARANTINE_SKIP_ENABLED = bool(os.environ.get("BD_QUARANTINE_SKIP"))
_QUARANTINE_MANIFEST = (os.environ.get("BD_QUARANTINE_MANIFEST", "")
                        or str(TESTS_DIR / "quarantine_skip.json"))


def _quarantine_files(registry, threshold):
    """Tier 1: filenames (the part before '::') of tests that are CHRONIC flakes
    (count >= threshold). These get pre-isolated to the serial lane so the
    parallelism collision that makes them flake can't recur -- they still RUN,
    still count, and a real failure in isolation still fails. threshold<=0 -> none."""
    files = set()
    for fid, _cnt in _chronic_flakes(registry, threshold):
        fname = fid.split("::", 1)[0].strip()
        if fname:
            files.add(fname)
    return files


def _partition_serial(file_names, iso_names):
    """Split file names into (serial, parallel): serial = the pinned-together set
    PLUS the quarantine-isolated set; parallel = the rest. Pure + testable. An
    empty iso set leaves the pre-existing pinned behavior unchanged (inert)."""
    serial_set = _PINNED_TOGETHER | set(iso_names or ())
    serial = [n for n in file_names if n in serial_set]
    parallel = [n for n in file_names if n not in serial_set]
    return serial, parallel


def _load_skip_manifest(path, now):
    """Tier 2 (opt-in): active file-level quarantine skips. Manifest shape:
    ``{"<file.py>": {"reason": str, "expires": <epoch|null>}}``. An entry whose
    ``expires`` is in the past is IGNORED (auto-lifted -- never a silent permanent
    skip). Missing/unreadable/non-dict -> ``{}``. Returns ``{fname: reason}`` for
    currently-active skips only. Never raises."""
    import json as _json
    try:
        with open(path, encoding="utf-8") as fh:
            d = _json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for fname, meta in d.items():
        if not isinstance(meta, dict):
            continue
        exp = meta.get("expires")
        try:
            if exp is not None and float(exp) < float(now):
                continue
        except (TypeError, ValueError):
            pass  # unparseable expiry -> treat as active (visible), don't crash
        out[fname] = str(meta.get("reason", "quarantined"))
    return out


def _load_flake_registry(path):
    """Load the registry JSON at ``path``; a missing/unreadable/non-dict file
    yields ``{}`` (never raises)."""
    import json as _json
    try:
        with open(path, encoding="utf-8") as fh:
            d = _json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_flake_registry(path, registry):
    """Write the registry JSON to ``path`` (parent dirs auto-created). Returns
    True on success, False on any OSError (never raises)."""
    import json as _json
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(registry, fh, indent=2, sort_keys=True)
        return True
    except OSError:
        return False


def _run_one_file_subprocess(test_file):
    """Run a single test file in a fresh subprocess and return
    (file_name, results) where results is the list of
    (name, err, ok, duration_seconds) tuples discover_and_run would
    have produced.

    Each file runs in its own interpreter process => full isolation of
    module-level globals (runners/s_cfg/_app_cfg/rate buckets). The
    subprocess writes a JSON artifact we read back. Used only by the
    --workers parallel path; the serial path still calls
    discover_and_run directly.
    """
    import json as _json
    import subprocess
    import tempfile as _tf
    tf = Path(test_file)
    with _tf.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = Path(td) / "r.json"
        env = dict(os.environ)
        env["BD_DISABLE_KEEPALIVE"] = "1"
        # Give each worker its own BD_HOME so cwd-relative state
        # (downloader_history.db, sites_config.json) can't collide.
        env["BD_HOME"] = td
        try:
            subprocess.run(
                [sys.executable, str(RUNNER_CLI),
                 str(tf), f"--json={out}"],
                env=env, cwd=td,
                capture_output=True, text=True, timeout=_FILE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return (tf.name, [(tf.name,
                    f"TIMEOUT (>{_FILE_TIMEOUT_S}s)", False,
                    float(_FILE_TIMEOUT_S))])
        if not out.is_file():
            return (tf.name, [(tf.name,
                    "worker produced no result file", False, 0.0)])
        try:
            data = _json.loads(out.read_text(encoding="utf-8"))
        except Exception as e:
            return (tf.name, [(tf.name,
                    f"could not read worker result: {e}", False, 0.0)])
        results = []
        # T49: prefer the full `tests` list when present — it carries
        # per-test names and durations for passes too, not just for
        # failures/skips. Falls back to the old failures/skips/passed
        # reconstruction for back-compat.
        tests_list = data.get("tests")
        if isinstance(tests_list, list):
            for rec in tests_list:
                status = rec.get("status")
                if status == "pass":
                    ok, err = True, ""
                elif status == "skip":
                    ok, err = None, rec.get("reason", "")
                else:
                    ok, err = False, rec.get("error", "")
                results.append((rec.get("test", "<unknown>"),
                                err, ok,
                                float(rec.get("duration_seconds", 0.0))))
        else:
            for f in data.get("failures", []):
                results.append((f["test"], f.get("error", ""),
                                False, 0.0))
            for s in data.get("skips", []):
                results.append((s["test"], s.get("reason", ""),
                                None, 0.0))
            npass = (data.get("passed", 0))
            # Reconstruct pass entries (names aren't in the legacy
            # JSON, but the count is — emit placeholders so totals
            # are exact).
            for i in range(npass):
                results.append((f"{tf.name}::pass_{i}", "", True, 0.0))
        return (tf.name, results)


# Test files that bind fixed ports / shared resources and therefore
# must NOT run concurrently with each other. They are scheduled onto
# the SAME worker so they execute back-to-back, never in parallel.
#
# Historical note: test_v3_43_55_csrf_bootstrap.py was pinned here
# from v3.63.7 through v3.64.5 to work around a Windows fresh-BD_HOME
# / asymmetry. The v3.64.5 D2 diag plus the BD_DROP_CSRF_PIN probe
# (Windows, 2026-05-23) showed the asymmetry was no longer present
# and the tests survive --workers without the pin. Retired in
# v3.64.6. See LESSONS_LEARNED for the probe outcome.
# v3.66.754c: test_v3_66_729_body_contract_fixtures is the 2nd-slowest file in the
# suite (139s serial: ~126 fixture-backed probe replays). Under high --workers it
# starves under CPU oversubscription and crosses the 900s HARD timeout, failing the
# whole FILE (root-caused by bd_729_probe.py: level=FILE-LEVEL, ERROR=TIMEOUT>900s --
# NOT the in-test bug two prior sessions theorised). The quarantine lane that would
# auto-serialise it only arms when BD_FLAKE_REGISTRY is set, which capture.sh does not
# do -- so pin it deterministically here. It still runs, still counts; a real failure
# in isolation still fails. Does not touch the (sound) probe logic in body_contract.py.
_PINNED_TOGETHER = {"test_fixture_site.py", "test_fixture_site2.py",
                    "test_v3_66_729_body_contract_fixtures.py",
                    "test_v3_66_13_phase2_p2_snapshot_replay.py"}


def _run_parallel(files_to_run, workers, iso_names=frozenset()):
    """Run files across a process pool. Returns all_results dict.

    Pinned files (fixed-port fixture sites) AND quarantine-isolated files (MNT-3
    Tier 1 chronic flakes) are run first, serially, in this process -- guaranteeing
    they never overlap. Everything else is fanned out across `workers` subprocesses.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed
    all_results = {}
    _serial_names, _parallel_names = _partition_serial(
        [f.name for f in files_to_run], iso_names)
    pinned = [f for f in files_to_run if f.name in _serial_names]
    parallel = [f for f in files_to_run if f.name in _parallel_names]

    # Pinned + quarantine-isolated files: plain serial, in-process, before anything.
    for tf in pinned:
        _tag = "pinned" if tf.name in _PINNED_TOGETHER else "isolated"
        print(f"  [{_tag}] {tf.name}")
        all_results[tf.name] = discover_and_run(tf)

    if parallel:
        print(f"  dispatching {len(parallel)} file(s) across "
              f"{workers} worker(s)...")
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_one_file_subprocess, tf): tf
                    for tf in parallel}
            done = 0
            for fut in as_completed(futs):
                fname, results = fut.result()
                all_results[fname] = results
                done += 1
                nf = sum(1 for _, _, ok, _ in results if ok is False)
                mark = "FAIL" if nf else "ok"
                print(f"  [{done}/{len(parallel)}] {mark}  {fname}")
    return all_results


def _retry_failures_serial(all_results, files_to_run):
    """Re-run, serially and isolated, every file that had a failure in
    the parallel pass. Classifies each prior failure as:

      - REAL    : failed parallel AND failed serial retry
      - FLAKY   : failed parallel but PASSED serial retry
                  (a parallelism collision, not a code defect)

    Returns (corrected_results, flaky_list). corrected_results has the
    retry outcome substituted in, so the final Total/Failed reflect
    real failures only; flaky_list is reported separately and loudly.
    """
    by_name = {f.name: f for f in files_to_run}
    failed_files = sorted({
        fname for fname, results in all_results.items()
        if any(ok is False for _, _, ok, _ in results)
    })
    flaky = []
    if not failed_files:
        return all_results, flaky
    print()
    print(f"  {len(failed_files)} file(s) had failures under parallel "
          f"execution — re-running them serially to classify...")
    for fname in failed_files:
        tf = by_name.get(fname)
        if tf is None:
            continue
        parallel_fails = {
            n for n, _, ok, _ in all_results[fname] if ok is False}
        # Isolated + timed: the in-process retry had NO timeout, so a single
        # pathologically-slow test could wedge the whole run forever. The
        # subprocess path enforces _FILE_TIMEOUT_S and full state isolation.
        _, retry = _run_one_file_subprocess(tf)
        # Defensive: every row should be a 4-tuple (name, err, ok,
        # duration), but normalize any short rows so a future shape
        # drift degrades gracefully instead of crashing the classifier.
        retry = [r if len(r) == 4 else (tuple(r) + (None, None, 0.0))[:4]
                 for r in retry]
        all_results[fname] = retry  # serial result is authoritative
        retry_fails = {n for n, _, ok, _ in retry if ok is False}
        recovered = parallel_fails - retry_fails
        for n in sorted(recovered):
            flaky.append(f"{fname} :: {n}")
        still = parallel_fails & retry_fails
        tag = []
        if recovered:
            tag.append(f"{len(recovered)} flaky")
        if still:
            tag.append(f"{len(still)} real")
        print(f"    {fname}: {', '.join(tag) or 'clean on retry'}")
    return all_results, flaky


def main():
    # v3.47.3: accept an optional list of test files / pytest-style
    # ids on the command line. Allows the dev-tools API + ad-hoc
    # debugging to run just one file or one class. Without arguments,
    # behavior is unchanged (full suite).
    #
    # Supported shapes:
    #   run_tests.py
    #   run_tests.py tests/test_foo.py
    #   run_tests.py tests/test_foo.py::TestBar
    #   run_tests.py tests/test_foo.py::TestBar::test_baz
    #
    # The ::filter parts are honored at the test-name level so an
    # operator can run a single class or test without learning pytest.
    requested = sys.argv[1:] if len(sys.argv) > 1 else []

    # v3.62.0: optional output-artifact flags. Pulled out of argv before
    # the rest is treated as test-file filters, so they don't get
    # mistaken for a test path.
    #   --json[=PATH]      write a machine-readable JSON result file
    #                      (default path: test_results.json)
    #   --summary[=PATH]   write a plain-text SUMMARY.txt artifact
    #                      (default path: SUMMARY.txt)
    # Both are designed so the file can be pasted verbatim into a chat
    # for cold analysis - deterministic, self-contained, greppable.
    json_path = None
    summary_path = None
    # v3.62.2: --workers N runs files across N subprocesses, then
    # re-runs any failures serially to classify flaky vs real.
    # 0 / unset / 1 = the original serial path (default, unchanged).
    workers = 0
    # v3.66.797: --isolate runs each file in its own subprocess, SERIALLY --
    # the same per-file machinery --workers uses (fresh interpreter, fresh
    # BD_HOME tmpdir + cwd, per-file wall timeout), without the parallelism.
    # This matches the stash gate's file-per-process execution model, so
    # cross-file state leaks (os.environ, sys.modules globals, cwd-relative
    # runtime artifacts) structurally cannot happen -- the band becomes an
    # ABSOLUTE signal instead of one read differentially against a baseline.
    # No failure-retry pass: serial execution has no parallelism collisions,
    # so an isolated failure is a real failure.
    isolate = False
    _kept = []
    for arg in requested:
        # v3.62.1: match flags case-insensitively on the flag NAME so
        # --SUMMARY / --Json work too. An explicit =PATH keeps its
        # original casing (paths are case-sensitive on some systems).
        low = arg.lower()
        if low == "--json":
            json_path = "test_results.json"
        elif low.startswith("--json="):
            json_path = arg.split("=", 1)[1]
        elif low == "--summary":
            summary_path = "SUMMARY.txt"
        elif low.startswith("--summary="):
            summary_path = arg.split("=", 1)[1]
        elif low == "--workers":
            workers = (os.cpu_count() or 2)
            # Windows ProcessPoolExecutor caps at 61 workers
            # (WaitForMultipleObjects limit of 64 minus 3 reserved
            # handles). Cap auto-detected count to 60 there; manual
            # --workers=N still respects the user's choice but will
            # raise ValueError at pool construction if > 61.
            if sys.platform == "win32" and workers > 60:
                workers = 60
        elif low.startswith("--workers="):
            try:
                workers = max(0, int(arg.split("=", 1)[1]))
            except ValueError:
                print(f"  WARN: bad --workers value, ignoring: {arg}")
                workers = 0
        elif low == "--isolate":
            isolate = True
        else:
            _kept.append(arg)
    requested = _kept

    filters = []  # list of (file_path, class_filter|None, test_filter|None)
    if requested:
        for arg in requested:
            parts = arg.split("::")
            file_part = parts[0]
            class_filter = parts[1] if len(parts) > 1 else None
            test_filter = parts[2] if len(parts) > 2 else None
            # Resolve to a real path under TESTS_DIR
            p = Path(file_part)
            if not p.is_absolute():
                p = Path.cwd() / p
            if not p.is_file():
                # Try resolving against tests/
                p2 = TESTS_DIR / Path(file_part).name
                if p2.is_file():
                    p = p2
                else:
                    print(f"  WARN  no such file: {file_part}")
                    continue
            filters.append((p.resolve(), class_filter, test_filter))

    print("=" * 70)
    if filters:
        print(f"Running BulkDownloader test subset ({len(filters)} target(s))")
    else:
        print("Running BulkDownloader test suite (minimal runner)")
    print("=" * 70)

    if filters:
        files_to_run = []
        # De-duplicate while preserving order
        seen = set()
        for path, _, _ in filters:
            if path not in seen:
                files_to_run.append(path)
                seen.add(path)
    else:
        files_to_run = sorted(TESTS_DIR.glob("test_*.py"))

    all_results = {}
    flaky_tests = []

    # MNT-3 (choice B) quarantine -- both tiers inert unless opted in.
    # Tier 2 (opt-in): drop file-level quarantine-skips entirely, loudly + expiring.
    _skips = (_load_skip_manifest(_QUARANTINE_MANIFEST, time.time())
              if _QUARANTINE_SKIP_ENABLED else {})
    if _skips:
        _dropped = [f for f in files_to_run if f.name in _skips]
        files_to_run = [f for f in files_to_run if f.name not in _skips]
        for f in _dropped:
            print(f"  SKIP (quarantined: {_skips[f.name]}) {f.name}")
        if _dropped:
            print(f"  quarantine: {len(_dropped)} file(s) skipped by manifest "
                  f"(BD_QUARANTINE_SKIP opt-in); {len(files_to_run)} will run")
    # Tier 1: pre-isolate chronic-flake files to the serial lane. Inert without a
    # populated BD_FLAKE_REGISTRY (so the release band never pre-isolates anything).
    _iso_names = (_quarantine_files(_load_flake_registry(_FLAKE_REGISTRY_PATH),
                                    _FLAKE_CHRONIC_THRESHOLD)
                  if _FLAKE_REGISTRY_PATH else set())
    if _iso_names:
        print(f"  quarantine: {len(_iso_names)} chronic-flake file(s) pre-isolated "
              f"to the serial lane: {', '.join(sorted(_iso_names))}")

    # v3.62.2: parallel path. Runs whole files, so a ::Class::test
    # filter forces serial — but a plain file list (no ::) is just a
    # file selection and parallelizes fine. files_to_run already
    # reflects the selection either way.
    has_subtest_filter = any(c is not None or t is not None
                             for _, c, t in filters)
    use_parallel = (workers >= 2 and not has_subtest_filter)
    if workers >= 2 and has_subtest_filter:
        print("  note: --workers ignored — running serially because "
              "::-filters were given.")
    # v3.66.797: --isolate resolution, LOUD in both degradation cases. The
    # subprocess path runs whole files, so a ::-filter cannot isolate; and
    # --workers is already subprocess-per-file, so --isolate adds nothing
    # there. A silent fallback would be a band claiming an isolation model
    # it is not using -- exactly the check-that-cannot-see failure shape.
    use_isolate = (isolate and not use_parallel and not has_subtest_filter)
    if isolate and use_parallel:
        print("  note: --isolate implied by --workers "
              "(the parallel path is already subprocess-per-file).")
    if isolate and has_subtest_filter:
        print("  note: --isolate ignored — running serially IN-PROCESS "
              "because ::-filters were given (the subprocess path runs "
              "whole files). This run is NOT process-isolated.")
    if use_parallel:
        all_results = _run_parallel(files_to_run, workers, iso_names=_iso_names)
        all_results, flaky_tests = _retry_failures_serial(
            all_results, files_to_run)
        # Print per-file outcome lines so the console matches serial.
        for fname in sorted(all_results):
            real_fails = [n for n, _, ok, _ in all_results[fname]
                          if ok is False]
            if real_fails:
                print(f"\n--- {fname} ---")
                for n, err, ok, _dt in all_results[fname]:
                    if ok is False:
                        short = "\n".join((err or "").splitlines()[-3:])
                        print(f"  FAIL  {n}")
                        print(f"          {short}")
    elif use_isolate:
        # v3.66.797: serial subprocess-per-file. Reuses the --workers worker
        # verbatim, so each file gets a fresh interpreter, its own BD_HOME
        # tmpdir + cwd, and the per-file wall timeout (_FILE_TIMEOUT_S) -- a
        # wedged file becomes a named per-file TIMEOUT failure instead of a
        # hung band. Console output matches the serial path test-for-test.
        print(f"  isolate: {len(files_to_run)} file(s), "
              f"one subprocess each (serial)")
        for tf in files_to_run:
            print(f"\n--- {tf.name} ---")
            _, results = _run_one_file_subprocess(tf)
            # Same defensive normalization as _retry_failures_serial: a
            # future shape drift degrades gracefully, never crashes the band.
            results = [r if len(r) == 4 else (tuple(r) + (None, None, 0.0))[:4]
                       for r in results]
            all_results[tf.name] = results
            for name, err, ok, _dt in results:
                if ok is True:
                    print(f"  PASS  {name}")
                elif ok is None:
                    print(f"  SKIP  {name} ({err})")
                else:
                    short = "\n".join((err or "").splitlines()[-3:])
                    print(f"  FAIL  {name}")
                    print(f"          {short}")
    else:
      for tf in files_to_run:
        print(f"\n--- {tf.name} ---")
        results = discover_and_run(tf)
        # Apply class + test filters for this file
        active_filters = [(c, t) for p, c, t in filters
                          if p == tf.resolve()]
        if active_filters:
            def keep(name):
                # name is like "ClassName::test_foo" or "test_foo"
                if "::" in name:
                    cls, fn = name.split("::", 1)
                else:
                    cls, fn = None, name
                for cf, tf2 in active_filters:
                    cf_ok = cf is None or cf == cls
                    tf_ok = tf2 is None or tf2 == fn
                    if cf_ok and tf_ok:
                        return True
                return False
            results = [(n, e, ok, d) for (n, e, ok, d) in results if keep(n)]
        all_results[tf.name] = results
        for name, err, ok, _dt in results:
            if ok is True:
                print(f"  PASS  {name}")
            elif ok is None:
                print(f"  SKIP  {name} ({err})")
            else:
                # Truncate traceback to last 2 lines
                short = "\n".join(err.splitlines()[-3:])
                print(f"  FAIL  {name}")
                print(f"          {short}")

    # Summary
    print()
    print("=" * 70)
    total = passed = failed = skipped = 0
    for fname, results in all_results.items():
        for n, e, ok, _dt in results:
            total += 1
            if ok is True:    passed += 1
            elif ok is None:  skipped += 1
            else:             failed += 1
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    print("=" * 70)
    # v3.62.2: flaky-test report. These FAILED under parallel execution
    # but PASSED on the serial retry — a parallelism collision, not a
    # code defect. They do NOT count toward Failed above (the serial
    # retry result is authoritative). Surfaced loudly, not hidden:
    # a file showing up here repeatedly likely needs pinning in
    # _PINNED_TOGETHER, or has an order-dependent test worth a look.
    if flaky_tests:
        print(f"  FLAKY under --workers ({len(flaky_tests)}) — "
              f"passed on serial retry, not counted as failures:")
        for ft in flaky_tests:
            print(f"    ~ {ft}")
        print("=" * 70)

    # C4 (12.1 flake-registry): persist this run's flake classifications so a
    # CHRONIC flake is trackable. Opt-in: only when BD_FLAKE_REGISTRY is set (the
    # release band does not set it, so this is a no-op there). Informational --
    # never affects the exit code.
    if _FLAKE_REGISTRY_PATH:
        _reg = _load_flake_registry(_FLAKE_REGISTRY_PATH)
        _reg = _update_flake_registry(_reg, flaky_tests, time.time())
        # MNT-3: age out entries unseen for > TTL so the quarantine is expiring.
        _reg = _prune_flake_registry(_reg, time.time(), _FLAKE_MAX_AGE_DAYS)
        _save_flake_registry(_FLAKE_REGISTRY_PATH, _reg)
        _chronic = _chronic_flakes(_reg, _FLAKE_CHRONIC_THRESHOLD)
        if _chronic:
            print(f"  CHRONIC FLAKES (>= {_FLAKE_CHRONIC_THRESHOLD} runs) — "
                  f"registry {_FLAKE_REGISTRY_PATH}:")
            for _fid, _cnt in _chronic:
                print(f"    ~ {_fid}  (x{_cnt})")
            print("=" * 70)

    # v3.62.0: optional output artifacts. Written AFTER the human
    # summary so the console output is unchanged when no flag is given.
    if json_path or summary_path:
        import datetime as _dt
        try:
            import bulk_downloader as _bd
            _ver = _bd.__version__
        except Exception:
            _ver = "unknown"
        # Flat, self-contained failure records.
        failures = []
        skips = []
        # T49: full per-test list with durations, for the slowest-N
        # report and for downstream tooling (D-71 dev tool reads
        # this). Keeps `failures` / `skips` lists for back-compat.
        tests_records = []
        all_durations = []
        for fname, results in all_results.items():
            for n, e, ok, dur in results:
                tests_records.append({
                    "file": fname, "test": n,
                    "status": ("pass" if ok is True
                               else ("skip" if ok is None else "fail")),
                    "duration_seconds": round(float(dur), 4),
                    **({"error": e} if ok is False
                       else ({"reason": e} if ok is None else {})),
                })
                all_durations.append((float(dur), fname, n))
                if ok is False:
                    failures.append({"file": fname, "test": n,
                                     "error": e})
                elif ok is None:
                    skips.append({"file": fname, "test": n,
                                  "reason": e})
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        # Slowest 20, descending. all_durations may be empty if the
        # whole run produced no tests (degenerate case).
        slowest = sorted(all_durations, reverse=True)[:20]
        # C4 (12.1): per-file soft-budget surfacing (informational).
        over_budget = _files_over_budget(all_durations, _FILE_BUDGET_S)
        if json_path:
            import json as _json
            payload = {
                # T49: schema version. Consumers (D-71) must reject
                # older artifacts that lack `tests` / durations.
                "schema_version": 2,
                "version": _ver, "timestamp": stamp,
                "total": total, "passed": passed,
                "failed": failed, "skipped": skipped,
                "ok": failed == 0,
                "tests": tests_records,
                "failures": failures, "skips": skips,
                # C4 (12.1): additive, optional. Older consumers ignore it;
                # never gates the run. `over` is [] when nothing exceeds the
                # per-file budget or the budget is disabled (0).
                "budget": {
                    "threshold_s": _FILE_BUDGET_S,
                    "over": [{"file": f, "seconds": s}
                             for f, s in over_budget],
                },
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    _json.dump(payload, fh, indent=2)
                print(f"  JSON results written: {json_path}")
            except OSError as _e:
                print(f"  WARN: could not write {json_path}: {_e}")
        if summary_path:
            lines = [
                "BulkDownloader test summary",
                f"version : {_ver}",
                f"run at  : {stamp}",
                f"result  : {total} total | {passed} passed | "
                f"{failed} failed | {skipped} skipped",
                "",
            ]
            if failures:
                lines.append(f"FAILURES ({len(failures)}):")
                for f in failures:
                    lines.append(f"  FAIL {f['file']} :: {f['test']}")
                    for el in (f["error"] or "").splitlines()[-3:]:
                        lines.append(f"       {el}")
                lines.append("")
            else:
                lines.append("FAILURES: none")
                lines.append("")
            if skips:
                lines.append(f"SKIPPED ({len(skips)}):")
                for s in skips:
                    lines.append(f"  SKIP {s['file']} :: {s['test']}"
                                 f"  ({s['reason']})")
                lines.append("")
            # T49: slowest-20 tail. Kept short so the top block stays
            # readable. Format never appears before SKIPPED so any
            # consumer parsing the existing top section is unaffected.
            if slowest:
                lines.append(f"SLOWEST {len(slowest)} TESTS:")
                for dur, fname, n in slowest:
                    lines.append(f"  {dur:7.3f}s  {fname} :: {n}")
            # C4 (12.1): over-budget files, after SLOWEST so any consumer
            # parsing the existing top section is unaffected. Informational.
            if over_budget:
                lines.append("")
                lines.append(
                    f"OVER BUDGET ({len(over_budget)}, per-file "
                    f">{_FILE_BUDGET_S}s wall):")
                for f, s in over_budget:
                    lines.append(f"  {s:8.3f}s  {f}")
            try:
                with open(summary_path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                print(f"  Summary written: {summary_path}")
            except OSError as _e:
                print(f"  WARN: could not write {summary_path}: {_e}")

    # @860 -- A FILE THAT COLLECTED NOTHING IS NOT A FILE THAT PASSED.
    #
    # This was `sys.exit(1 if failed > 0 else 0)`. A requested file from which
    # pytest collects zero tests prints
    #     Total: 0 | Passed: 0 | Failed: 0 | Skipped: 0
    # and exited 0 -- and all three band runners grade on exactly that shape:
    # bd-band and bd-parband test ('Failed: 0' in blob and rc == 0), bd-fullsuite
    # counts the file green. So a RED-first battery could be reported as proven
    # failing while collecting nothing at all, and the first honest signal would
    # be the box. That defeats CLAUDE.md section 2's first rule.
    #
    # Reproduced: a file whose only assertion lives in a non-Test* class (pytest
    # collects nothing) was graded PASS by bd-band and bd-parband.
    #
    # SKIPS ARE NOT THIS. An all-skipped file has total > 0 and stays green --
    # environment skips are legitimate and gating on them would be the section 0
    # over-correction. The bug is collecting NOTHING.
    #
    # Only when files were explicitly REQUESTED: a bare run globs the whole
    # directory, and an empty tests/ is a different problem with its own signal.
    # Measured before changing this: 14588 tests collect across tests/, and 0 of
    # 60 sampled tracked files collect zero, so nothing legitimate trips it.
    if total == 0 and requested:
        print()
        print("  BD-RUNNER UNEVALUABLE: %d file(s) requested, ZERO tests "
              "collected." % len(files_to_run))
        print("  This is not a pass. Nothing ran, so nothing was proven --")
        print("  check for a non-Test* class, a bad node-id, or a typo in the path.")
        sys.exit(2)
    sys.exit(1 if failed > 0 else 0)
