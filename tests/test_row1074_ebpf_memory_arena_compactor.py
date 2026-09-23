"""Row 1074: glibc arena trimming, truthful results, pacing, and GC integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

BD_GATE_SCOPE = "module"


def test_row1074_positive_control_and_capability(monkeypatch: Any) -> None:
    """Check the existing introspection module and initialize its glibc compactor."""
    repo_root = Path(__file__).resolve().parents[1]
    introspection_py = repo_root / "bulk_downloader" / "dev_suite" / "introspection.py"
    assert introspection_py.is_file(), f"Positive control failed: {introspection_py} does not exist"
    from bulk_downloader import arena_compactor

    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor, "_GLOBAL_ARENA_COMPACTOR", None)
        compactor = arena_compactor.get_arena_compactor()
        assert isinstance(compactor, arena_compactor.ArenaCompactor)
        assert arena_compactor.get_arena_compactor() is compactor


def test_arena_compactor_status_counts_completed_calls(monkeypatch: Any) -> None:
    """A completed native call counts even when it releases no memory."""
    native = _NativeTrimProbe(0)
    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, native)
        compactor.compact_arenas(force=True)
        result = compactor.compact_arenas(force=True)
        status = compactor.get_compactor_status()
    assert result.trimmed is False
    assert native.calls == [(0,), (0,)]
    assert status["trim_count"] == 2


def test_arena_compactor_compaction_execution() -> None:
    """Read real process RSS around a glibc trim attempt."""
    from bulk_downloader.arena_compactor import ArenaCompactor

    result = ArenaCompactor().compact_arenas(force=True)
    assert result.rss_before_bytes is not None and result.rss_before_bytes > 0
    assert result.rss_after_bytes is not None and result.rss_after_bytes > 0
    assert result.duration_ms >= 0.0
    assert {
        "rss_before_bytes", "rss_after_bytes", "rss_delta_bytes", "duration_ms"
    } <= result.to_dict().keys()


def test_compaction_pacing_and_interval_guard(monkeypatch: Any) -> None:
    """The interval guard skips a native call; force bypasses that guard."""
    from bulk_downloader import arena_compactor

    native = _NativeTrimProbe(1)
    now = [100.0]
    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor.time, "monotonic", lambda: now[0])
        compactor = _compactor_with_native(patch, native)
        compactor.min_trim_interval_seconds = 10.0
        res1 = compactor.compact_arenas(force=False)
        now[0] = 101.0
        res2 = compactor.compact_arenas(force=False)
        res3 = compactor.compact_arenas(force=True)
        status = compactor.get_compactor_status()
    assert res1.trimmed is True
    assert res1.to_dict()["status"] == "released"
    assert res2.trimmed is False
    assert res2.to_dict()["status"] == "paced"
    assert res3.trimmed is True
    assert res3.to_dict()["status"] == "released"
    assert native.calls == [(0,), (0,)]
    assert status["trim_count"] == 2


def test_glibc_malloc_trim_binding_safety(monkeypatch: Any) -> None:
    """The public helper reports whether the native binding is available."""
    from bulk_downloader import arena_compactor

    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor, "_GLOBAL_ARENA_COMPACTOR", None)
        result = arena_compactor.compact_glibc_arenas()
    assert isinstance(result.glibc_available, bool)
    assert result.duration_ms >= 0.0


def test_introspection_caller_wiring(monkeypatch: Any) -> None:
    """GC preserves its fields while adding the real trim result and RSS delta."""
    from bulk_downloader import arena_compactor
    from bulk_downloader.dev_suite.introspection import force_gc

    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor, "_GLOBAL_ARENA_COMPACTOR", None)
        report = force_gc()
    assert report["ok"] is True
    assert "unreachable_collected" in report
    assert "objects_freed" in report
    arena_stats = report["arena_compaction"]
    assert "rss_before_bytes" in arena_stats
    assert "rss_after_bytes" in arena_stats
    assert "rss_delta_bytes" in arena_stats

class _NativeTrimProbe:
    """Record the native call boundary without replacing compactor behavior."""

    def __init__(self, result: int = 0, error: OSError | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[int, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(tuple(getattr(arg, "value", arg) for arg in args))
        if self.error is not None:
            raise self.error
        return self.result


def _compactor_with_native(patch: Any, native: _NativeTrimProbe) -> Any:
    from types import SimpleNamespace

    from bulk_downloader import arena_compactor

    patch.setattr(
        arena_compactor.ctypes, "CDLL", lambda name: SimpleNamespace(malloc_trim=native)
    )
    return arena_compactor.ArenaCompactor()


def test_regression_status_does_not_invent_kernel_observations(monkeypatch: Any) -> None:
    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, _NativeTrimProbe())
        status = compactor.get_compactor_status()

    assert "ebpf_tracer" not in status, "E1: glibc trimming cannot claim attached eBPF probes"
    assert "total_reclaimed_bytes" not in status, "E2: RSS differences are not attributed reclaim"
    assert status["trim_count"] == 0


@pytest.mark.parametrize(
    ("native_return", "expected_trimmed", "expected_status"),
    [(0, False, "no_release"), (1, True, "released")],
)
def test_regression_native_return_controls_trim_result(
    monkeypatch: Any, native_return: int, expected_trimmed: bool, expected_status: str
) -> None:
    import ctypes

    native = _NativeTrimProbe(native_return)
    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, native)
        result = compactor.compact_arenas(force=True, pad=37)
        status = compactor.get_compactor_status()

    assert result.trimmed is expected_trimmed, "E2: trimmed must reflect malloc_trim's return"
    assert native.calls == [(37,)], "E2: malloc_trim needs exactly one requested pad argument"
    assert native.argtypes == [ctypes.c_size_t], "E2: malloc_trim size_t ABI is unbound"
    assert native.restype is ctypes.c_int, "E2: malloc_trim int result ABI is unbound"
    assert result.glibc_available is True
    assert result.to_dict()["status"] == expected_status
    assert status["trim_count"] == 1


@pytest.mark.parametrize("failure", ["library_unavailable", "symbol_unavailable"])
def test_regression_missing_native_capability_never_reports_trim(
    monkeypatch: Any, failure: str
) -> None:
    from types import SimpleNamespace

    from bulk_downloader import arena_compactor

    def load_libc(name: str) -> Any:
        if failure == "library_unavailable":
            raise OSError("fixture: libc unavailable")
        return SimpleNamespace()

    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor.ctypes, "CDLL", load_libc)
        compactor = arena_compactor.ArenaCompactor()
        result = compactor.compact_arenas(force=True)
        status = compactor.get_compactor_status()

    assert result.trimmed is False, "E2: unavailable malloc_trim must not report a trim"
    assert result.glibc_available is False
    assert result.to_dict()["status"] == "unavailable"
    assert status["glibc_available"] is False
    assert status["trim_count"] == 0


def test_regression_native_error_never_reports_completed_trim(monkeypatch: Any) -> None:
    native = _NativeTrimProbe(error=OSError("fixture: native invocation failed"))
    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, native)
        result = compactor.compact_arenas(force=True, pad=13)
        status = compactor.get_compactor_status()

    assert result.trimmed is False, "E2: failed native invocation cannot report trimming"
    assert native.calls == [(13,)]
    assert result.glibc_available is True
    assert result.to_dict()["status"] == "error"
    assert status["trim_count"] == 0


def test_regression_rss_growth_is_signed_observation_not_reclaimed_bytes(monkeypatch: Any) -> None:
    from bulk_downloader import arena_compactor

    readings = iter((8192, 12288))
    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, _NativeTrimProbe(1))
        patch.setattr(arena_compactor, "_read_process_rss_bytes", lambda: next(readings))
        summary = compactor.compact_arenas(force=True).to_dict()

    assert "bytes_reclaimed" not in summary, "E2: process RSS delta cannot prove allocator reclaim"
    assert summary["rss_before_bytes"] == 8192
    assert summary["rss_after_bytes"] == 12288
    assert summary["rss_delta_bytes"] == -4096, "E2: process RSS increases must not be clamped"


def test_regression_unavailable_rss_is_null_not_peak_or_placeholder(monkeypatch: Any) -> None:
    from bulk_downloader import arena_compactor

    with monkeypatch.context() as patch:
        compactor = _compactor_with_native(patch, _NativeTrimProbe(0))
        patch.setattr(arena_compactor.Path, "is_file", lambda path: False)
        assert arena_compactor._read_process_rss_bytes() is None, (
            "E2: unavailable current RSS must not become peak RSS or a fabricated 100 MB"
        )
        summary = compactor.compact_arenas(force=True).to_dict()

    assert "bytes_reclaimed" not in summary
    assert summary["rss_before_bytes"] is None
    assert summary["rss_after_bytes"] is None
    assert summary["rss_delta_bytes"] is None


def test_regression_real_libc_call_preserves_abi_and_native_result(monkeypatch: Any) -> None:
    import ctypes
    from types import SimpleNamespace

    from bulk_downloader import arena_compactor

    native = ctypes.CDLL("libc.so.6").malloc_trim
    calls: list[tuple[int, ...]] = []
    returns: list[int] = []

    class NativeSpy:
        @property
        def argtypes(self) -> Any:
            return native.argtypes

        @argtypes.setter
        def argtypes(self, value: Any) -> None:
            native.argtypes = value

        @property
        def restype(self) -> Any:
            return native.restype

        @restype.setter
        def restype(self, value: Any) -> None:
            native.restype = value

        def __call__(self, *args: Any) -> int:
            calls.append(tuple(getattr(arg, "value", arg) for arg in args))
            result = native(*args)
            returns.append(result)
            return result

    spy = NativeSpy()
    with monkeypatch.context() as patch:
        patch.setattr(
            arena_compactor.ctypes, "CDLL", lambda name: SimpleNamespace(malloc_trim=spy)
        )
        compactor = arena_compactor.ArenaCompactor()
        result = compactor.compact_arenas(force=True, pad=0)

    assert calls == [(0,)], "E2: compaction must invoke real malloc_trim exactly once"
    assert native.argtypes == [ctypes.c_size_t], "E2: real libc malloc_trim size_t ABI is unbound"
    assert native.restype is ctypes.c_int
    assert len(returns) == 1
    assert returns[0] in (0, 1)
    assert result.trimmed is bool(returns[0])
    assert result.glibc_available is True


def test_regression_force_gc_invokes_compactor_once_and_preserves_gc_fields(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from bulk_downloader import arena_compactor
    from bulk_downloader.dev_suite.introspection import force_gc

    marker = {"status": "fixture-native-result", "trimmed": False, "rss_delta_bytes": -4096}
    calls: list[bool] = []

    def compact(*, force: bool) -> Any:
        calls.append(force)
        return SimpleNamespace(to_dict=lambda: marker)

    with monkeypatch.context() as patch:
        patch.setattr(arena_compactor, "compact_glibc_arenas", compact)
        report = force_gc()

    assert calls == [True], "E2: force_gc must request exactly one forced native trim"
    assert report["arena_compaction"] == marker
    assert report["ok"] is True
    assert {
        "unreachable_collected", "objects_before", "objects_after", "objects_freed", "gc_garbage"
    } <= report.keys()
