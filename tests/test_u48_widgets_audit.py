"""U48 — disk_free fix + GPU widget contract.

v3.63.6 unit #5 ships two related fixes:

  Fix A — disk_free_gb call. Pre-v3.63.6 the widgets collector called
    `disk_free_gb()` with no argument, but the function requires a
    `path` positional. TypeError was swallowed by a bare
    `except Exception: pass`, so the disk_free widget always rendered
    '—' on every deployment (silent for years). Fixed by pulling the
    first site's download_dir from app.s_cfg, falling back to the
    function's own empty-string-to-Path.home() handling. Upgraded the
    bare except to log to stderr like every other collector.

  Fix B — GPU widget. Pre-v3.63.6 there was no GPU widget at all. The
    catalog had cpu_ram but no gpu; the operator's dashboard
    suggested GPU stats should be visible but no backend ever
    emitted them. v3.63.6 adds:
      * "gpu" in widgets_config.VALID_WIDGET_IDS
      * Collector in app_widgets_api.py::_collect_data — NVIDIA-only
        via nvidia-smi --query-gpu, fail-open on missing binary or
        non-NVIDIA hardware
      * Renderer in widgets.js — null-guarded, matches cpu_ram pattern

These tests pin both, using fake-subprocess for the GPU collector
(can't run nvidia-smi in the sandbox).
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from unittest.mock import patch


_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_PY = _REPO_ROOT / "bulk_downloader" / "app_widgets_api.py"
_CONFIG_PY = _REPO_ROOT / "bulk_downloader" / "widgets_config.py"


# ── Fix A: disk_free_gb call ────────────────────────────────────────


def test_disk_free_gb_called_with_path_argument():
    """`disk_free_gb` must be called with a real argument, not bare.

    The function signature `def disk_free_gb(path)` has no default —
    calling it with no arg raises TypeError. Pre-v3.63.6 the bare
    `except Exception: pass` swallowed the error silently.
    """
    body = _API_PY.read_text(encoding="utf-8")
    # Find every `disk_free_gb(...)` call IN EXECUTABLE CODE only.
    # Comments may legitimately reference `disk_free_gb()` to document
    # the historical bug. Filter to non-comment lines first.
    executable_lines = [
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    ]
    executable_body = "\n".join(executable_lines)
    calls = re.findall(r"disk_free_gb\s*\(([^)]*)\)", executable_body)
    # The function definition itself isn't in app_widgets_api.py — it
    # lives in detect.py — so every match here is an invocation.
    assert calls, "app_widgets_api.py should call disk_free_gb"
    for arg in calls:
        # Empty parens === bug. The arg can be empty-string literal "",
        # a variable, or any expression — just not literally empty.
        assert arg.strip() != "", (
            "disk_free_gb() called with no argument — this raises "
            "TypeError that gets swallowed by the bare except. Pre-v3.63.6 "
            "this is why the disk_free widget always rendered '—'."
        )


def test_disk_free_failure_path_logs_to_stderr():
    """The outer disk-free collector's except block must log, not silently pass.

    Pre-v3.63.6 the OUTER except (around the disk_free_gb call) was
    `except Exception: pass` — silent. Post-v3.63.6 it logs
    `[widgets-api] disk_free_gb failed: ...` to stderr, matching the
    pattern every other collector in the file already uses. This
    makes the next regression diagnosable.

    A nested inner `except Exception: pass` for best-effort enrichment
    (e.g. trying to read s_cfg.download_dir) is fine — that's an
    optional refinement, not the error path itself.
    """
    body = _API_PY.read_text(encoding="utf-8")
    # The outer block has the shape:
    #     gb = disk_free_gb(...)
    #     if gb is not None:
    #         ...
    #     except Exception as e:
    #         sys.stderr.write(f"[widgets-api] disk_free_gb failed: ...")
    # The contract: somewhere between the `gb = disk_free_gb(...)` line
    # and the next blank line at column 0 (or comment block), there
    # must be an `except` clause that writes to stderr.
    lines = body.splitlines()
    # Find the `gb = disk_free_gb(...)` line.
    call_line = None
    for i, line in enumerate(lines):
        if re.search(r"gb\s*=\s*disk_free_gb\(", line):
            call_line = i
            break
    assert call_line is not None, "gb = disk_free_gb(...) line not found"
    # Look forward to the first `except` at the outer indent level
    # (i.e. an except that catches the disk_free_gb call itself).
    found_logging_except = False
    for j in range(call_line, min(call_line + 30, len(lines))):
        line = lines[j]
        if re.match(r"\s*except\s+Exception\s+as\s+\w+\s*:", line):
            # Check the body of this except.
            for k in range(j + 1, min(j + 5, len(lines))):
                if "sys.stderr.write" in lines[k] or "stderr" in lines[k]:
                    found_logging_except = True
                    break
            break
        if re.match(r"\s*except\s+Exception\s*:\s*$", line):
            # Bare `except Exception:` followed by `pass` is the regression.
            if (j + 1 < len(lines)
                    and re.match(r"\s*pass\s*$", lines[j + 1])):
                # Failed contract.
                break
    assert found_logging_except, (
        "Outer disk_free collector except must log to stderr. Pre-v3.63.6 "
        "it was `except Exception: pass` — silent. The bug it hid "
        "(disk_free_gb called with no arg) went undetected for years."
    )


# ── Fix B: GPU widget catalog entry ─────────────────────────────────


def test_gpu_is_in_valid_widget_ids():
    """The widgets catalog must list 'gpu' as a valid widget id.

    VALID_WIDGET_IDS is the load-bearing source-of-truth set in
    widgets_config.py. A widget id absent from it gets rejected by
    PUT /api/widgets/<scope>, so the operator can't pick the widget
    from the dashboard picker.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        # Import fresh to dodge any stale cache from prior tests.
        if "bulk_downloader.widgets_config" in sys.modules:
            importlib.reload(sys.modules["bulk_downloader.widgets_config"])
        from bulk_downloader import widgets_config
        importlib.reload(widgets_config)
        assert "gpu" in widgets_config.VALID_WIDGET_IDS, (
            f"'gpu' missing from VALID_WIDGET_IDS. Set: "
            f"{sorted(widgets_config.VALID_WIDGET_IDS)}"
        )
    finally:
        sys.path.pop(0)


# ── Fix B: GPU collector — happy path with mocked nvidia-smi ────────


_REAL_NVIDIA_SMI_STASH_OUTPUT = (
    # Real shape from a Tesla T4 (matches stash hardware).
    # --query-gpu=utilization.gpu,memory.used,memory.total,name
    # --format=csv,noheader,nounits
    "5, 1024, 15360, Tesla T4\n"
)


def test_gpu_collector_parses_nvidia_smi_output():
    """With a fake nvidia-smi returning the canonical csv,noheader,nounits
    output, _collect_data emits gpu_util_pct / gpu_mem_used_mib /
    gpu_mem_total_mib / gpu_mem_pct / gpu_name / gpu_mem_total_fmt.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)

        class FakeCompletedProcess:
            stdout = _REAL_NVIDIA_SMI_STASH_OUTPUT
            stderr = ""
            returncode = 0

        with patch.object(app_widgets_api, "sys", sys):  # ensure stderr is real
            with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
                with patch("subprocess.run",
                           return_value=FakeCompletedProcess()):
                    out = app_widgets_api._collect_data(None)

        assert out.get("gpu_util_pct") == 5, out
        assert out.get("gpu_mem_used_mib") == 1024, out
        assert out.get("gpu_mem_total_mib") == 15360, out
        # 1024 / 15360 = 6.666...% → rounded to 6.7
        assert out.get("gpu_mem_pct") == 6.7, out
        assert out.get("gpu_name") == "Tesla T4", out
        # 15360 MiB >= 1024 → "15.0 GiB"
        assert out.get("gpu_mem_total_fmt") == "15.0 GiB", out
    finally:
        sys.path.pop(0)


def test_gpu_collector_fails_open_when_nvidia_smi_missing():
    """If shutil.which returns None (nvidia-smi not installed), the
    collector must NOT emit any gpu_* keys. The widget renders '—'.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        with patch("shutil.which", return_value=None):
            out = app_widgets_api._collect_data(None)
        # No gpu keys at all.
        gpu_keys = [k for k in out if k.startswith("gpu_")]
        assert gpu_keys == [], (
            f"gpu_* keys leaked when nvidia-smi missing: {gpu_keys}"
        )
    finally:
        sys.path.pop(0)


def test_gpu_collector_fails_open_on_nvidia_smi_error():
    """If nvidia-smi runs but returns non-zero (no GPU, driver error,
    etc.), the collector must not emit gpu_* keys.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)

        class FailedRun:
            stdout = ""
            stderr = "No devices were found"
            returncode = 9

        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", return_value=FailedRun()):
                out = app_widgets_api._collect_data(None)
        gpu_keys = [k for k in out if k.startswith("gpu_")]
        assert gpu_keys == [], (
            f"gpu_* keys leaked on nvidia-smi nonzero exit: {gpu_keys}"
        )
    finally:
        sys.path.pop(0)


def test_gpu_collector_fails_open_on_malformed_output():
    """A malformed nvidia-smi response (wrong column count) must not
    raise — fail-open per A1's diagnostic-trace principle: the
    widget showing '—' is correct, a 500-ing /api/widgets/data is not.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)

        class MalformedRun:
            stdout = "this is not csv\n"
            stderr = ""
            returncode = 0

        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", return_value=MalformedRun()):
                # Must not raise.
                out = app_widgets_api._collect_data(None)
        gpu_keys = [k for k in out if k.startswith("gpu_")]
        assert gpu_keys == [], (
            f"gpu_* keys leaked on malformed output: {gpu_keys}"
        )
    finally:
        sys.path.pop(0)


def test_gpu_query_includes_required_fields():
    """The nvidia-smi invocation must query the four canonical fields:
    utilization.gpu, memory.used, memory.total, name. A future
    well-meaning cleanup that drops one would break the widget.
    """
    body = _API_PY.read_text(encoding="utf-8")
    for field in ("utilization.gpu", "memory.used", "memory.total", "name"):
        assert field in body, (
            f"nvidia-smi query missing {field!r}: required for the GPU "
            f"widget renderer"
        )
    # And the canonical format flags.
    assert "csv,noheader,nounits" in body, (
        "nvidia-smi query must use csv,noheader,nounits format "
        "(the parser depends on this exact shape)"
    )
