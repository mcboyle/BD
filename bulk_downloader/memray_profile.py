"""Row 1073: Continuous Heap Profiling Integration with Memray and Automated Flamegraph Generation.

Provides high-resolution heap allocation profiling, continuous memory tracking,
leak detection, and automated interactive flamegraph generation for bulk_downloader.
When `memray` is installed in the runtime environment, it leverages the native
Memray tracking engine; otherwise, it provides a high-fidelity tracemalloc-backed
heap profiling engine with interactive SVG/HTML flamegraph generation.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Check memray availability dynamically (row331 / http3_client pattern)
def _load_memray():
    try:
        import importlib
        return importlib.import_module("memray")
    except Exception:
        return None

memray = _load_memray()
_MEMRAY_AVAILABLE = memray is not None


def is_memray_available() -> bool:
    """Return True if native memray package is importable."""
    return _MEMRAY_AVAILABLE


from enum import Enum


class ProfileState(str, Enum):
    """Three-state profiling lifecycle disposition (O1224)."""
    ACTIVE = "active"
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"


@dataclass
class AllocationRecord:
    file_path: str
    line_number: int
    size_bytes: int
    count: int = 1
    traceback: List[str] = field(default_factory=list)


class MemrayProfile:
    """Continuous Heap Profiling Manager with automated flamegraph output."""

    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        capture_leaks: bool = False,
        native: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir or tempfile.gettempdir())
        self.capture_leaks = capture_leaks
        self.native = native
        self._lock = threading.RLock()
        self._is_active = False
        self._state: ProfileState = ProfileState.UNAVAILABLE
        self._destination: Optional[Path] = None
        self._tracker = None
        self._engine: str = "memray" if _MEMRAY_AVAILABLE else "tracemalloc_fallback"
        self._start_time: float = 0.0
        self._start_snapshot: Optional[tracemalloc.Snapshot] = None
        self._peak_bytes: Optional[int] = None
        self._allocation_count: Optional[int] = None
        self._last_stats: Dict[str, Any] = {}
        self._flamegraph_path: Optional[Path] = None
        self._records: List[AllocationRecord] = []

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    @property
    def engine(self) -> str:
        return self._engine

    def start(
        self,
        destination: Optional[Union[str, Path]] = None,
        capture_leaks: Optional[bool] = None,
        native: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Start continuous heap profiling."""
        with self._lock:
            if self._is_active:
                return {
                    "ok": True,
                    "already_active": True,
                    "state": self._state.value,
                    "engine": self._engine,
                    "destination": str(self._destination) if self._destination else None,
                }

            if capture_leaks is not None:
                self.capture_leaks = capture_leaks
            if native is not None:
                self.native = native

            self.output_dir.mkdir(parents=True, exist_ok=True)
            if destination:
                self._destination = Path(destination)
            else:
                ts = int(time.time() * 1000)
                ext = ".bin" if _MEMRAY_AVAILABLE else ".tm"
                self._destination = self.output_dir / f"heap_profile_{ts}{ext}"

            self._start_time = time.time()
            self._records.clear()
            self._flamegraph_path = None

            if _MEMRAY_AVAILABLE and not self.native:
                try:
                    self._tracker = memray.Tracker(
                        destination=memray.FileDestination(
                            str(self._destination), overwrite=True
                        ),
                        native_traces=self.native,
                    )
                    self._tracker.__enter__()
                    self._engine = "memray"
                except Exception as exc:
                    logger.warning("Failed to initialize Memray tracker: %s; falling back", exc)
                    self._engine = "tracemalloc_fallback"

            if self._engine == "tracemalloc_fallback":
                if not tracemalloc.is_tracing():
                    tracemalloc.start(25)
                self._start_snapshot = tracemalloc.take_snapshot()

            self._is_active = True
            self._state = ProfileState.ACTIVE
            self._peak_bytes = 0
            self._allocation_count = 0
            return {
                "ok": True,
                "already_active": False,
                "state": self._state.value,
                "engine": self._engine,
                "destination": str(self._destination),
                "started_at": self._start_time,
            }

    def stop(self) -> Dict[str, Any]:
        """Stop continuous heap profiling and summarize stats."""
        with self._lock:
            if not self._is_active:
                return {
                    "ok": True,
                    "was_active": False,
                    "state": self._state.value,
                    "engine": self._engine,
                    "peak_memory_bytes": self._peak_bytes,
                    "allocation_count": self._allocation_count,
                }

            duration = time.time() - self._start_time
            peak_bytes = 0
            allocation_count = 0

            if self._engine == "memray" and self._tracker:
                try:
                    self._tracker.__exit__(None, None, None)
                except Exception as exc:
                    logger.warning("Error exiting Memray tracker: %s", exc)
                self._tracker = None

            elif self._engine == "tracemalloc_fallback":
                try:
                    current_snap = tracemalloc.take_snapshot()
                    if self._start_snapshot:
                        top_stats = current_snap.compare_to(self._start_snapshot, "lineno")
                    else:
                        top_stats = current_snap.statistics("lineno")

                    for stat in top_stats[:100]:
                        size = getattr(stat, "size", 0)
                        count = getattr(stat, "count", 0)
                        frame = stat.traceback[0] if stat.traceback else None
                        fpath = frame.filename if frame else "unknown"
                        lineno = frame.lineno if frame else 0
                        tb_frames = [f"{f.filename}:{f.lineno}" for f in (stat.traceback or [])]

                        peak_bytes += size
                        allocation_count += count
                        self._records.append(
                            AllocationRecord(
                                file_path=fpath,
                                line_number=lineno,
                                size_bytes=size,
                                count=count,
                                traceback=tb_frames,
                            )
                        )
                except Exception as exc:
                    logger.warning("Error computing tracemalloc stats: %s", exc)

            self._peak_bytes = peak_bytes
            self._allocation_count = allocation_count
            self._is_active = False
            self._state = ProfileState.MEASURED

            summary = {
                "ok": True,
                "was_active": True,
                "state": self._state.value,
                "engine": self._engine,
                "duration_seconds": round(duration, 4),
                "peak_memory_bytes": self._peak_bytes,
                "allocation_count": self._allocation_count,
                "destination": str(self._destination) if self._destination else None,
                "record_count": len(self._records),
            }
            self._last_stats = summary
            return summary

    def get_stats(self) -> Dict[str, Any]:
        """Return current profiling telemetry with three-state disposition (O1224)."""
        with self._lock:
            if not self._is_active and self._state == ProfileState.UNAVAILABLE:
                return {
                    "is_active": False,
                    "state": ProfileState.UNAVAILABLE.value,
                    "engine": self._engine,
                    "memray_available": _MEMRAY_AVAILABLE,
                    "destination": None,
                    "peak_memory_bytes": None,
                    "current_memory_bytes": None,
                    "allocation_count": None,
                    "records_collected": 0,
                }

            current_bytes = None
            if self._is_active and tracemalloc.is_tracing():
                current_bytes, _ = tracemalloc.get_traced_memory()

            peak = self._peak_bytes
            if current_bytes is not None:
                peak = max(peak or 0, current_bytes)

            return {
                "is_active": self._is_active,
                "state": self._state.value,
                "engine": self._engine,
                "memray_available": _MEMRAY_AVAILABLE,
                "destination": str(self._destination) if self._destination else None,
                "peak_memory_bytes": peak,
                "current_memory_bytes": current_bytes,
                "allocation_count": self._allocation_count,
                "records_collected": len(self._records),
            }

    def reset(self) -> None:
        """Reset internal buffers and return to UNAVAILABLE state."""
        with self._lock:
            if self._is_active:
                self.stop()
            self._records.clear()
            self._peak_bytes = None
            self._allocation_count = None
            self._destination = None
            self._flamegraph_path = None
            self._state = ProfileState.UNAVAILABLE

    def generate_flamegraph(
        self,
        output_file: Optional[Union[str, Path]] = None,
        title: str = "Memray Heap Profile Flamegraph",
    ) -> Path:
        """Generate an interactive HTML/SVG flamegraph report."""
        with self._lock:
            out_path = Path(output_file) if output_file else self.output_dir / "flamegraph.html"
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if native memray reporter can run
            if self._engine == "memray" and self._destination and self._destination.exists():
                try:
                    import importlib
                    flamegraph_mod = importlib.import_module("memray.reporters.flamegraph")
                    FlamegraphReporter = getattr(flamegraph_mod, "FlamegraphReporter")
                    memray_mod = importlib.import_module("memray")
                    FileReader = getattr(memray_mod, "FileReader")

                    reader = FileReader(str(self._destination))
                    reporter = FlamegraphReporter(
                        reader=reader,
                        title=title,
                    )
                    with open(out_path, "w", encoding="utf-8") as f:
                        reporter.render(f)
                    self._flamegraph_path = out_path
                    return out_path
                except Exception as exc:
                    logger.warning("Memray flamegraph render fallback triggered: %s", exc)

            # Standalone interactive SVG/HTML flamegraph generator
            stats = self.get_stats()
            records_data = [
                {
                    "file": r.file_path,
                    "line": r.line_number,
                    "bytes": r.size_bytes,
                    "count": r.count,
                    "stack": r.traceback or [f"{r.file_path}:{r.line_number}"],
                }
                for r in self._records
            ]

            html_content = _render_flamegraph_html(
                title=title,
                engine=self._engine,
                stats=stats,
                records=records_data,
            )
            out_path.write_text(html_content, encoding="utf-8")
            self._flamegraph_path = out_path
            return out_path

    def __enter__(self) -> "MemrayProfile":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def _render_flamegraph_html(
    title: str,
    engine: str,
    stats: Dict[str, Any],
    records: List[Dict[str, Any]],
) -> str:
    """Render self-contained HTML flamegraph report."""
    records_json = json.dumps(records)
    stats_json = json.dumps(stats, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 20px;
      background: #1e1e24;
      color: #e0e0e0;
    }}
    .header {{
      border-bottom: 1px solid #333;
      padding-bottom: 15px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 8px 0;
      font-size: 24px;
      color: #ff758c;
    }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 12px;
      background: #2b303c;
      color: #61afef;
      margin-right: 8px;
    }}
    .stats-card {{
      background: #252830;
      border-radius: 6px;
      padding: 12px 16px;
      margin-bottom: 20px;
      font-family: monospace;
      font-size: 13px;
    }}
    .flamegraph-container {{
      background: #181a1f;
      border: 1px solid #31363f;
      border-radius: 6px;
      padding: 15px;
      overflow-x: auto;
    }}
    .frame-bar {{
      height: 22px;
      margin-bottom: 2px;
      border-radius: 3px;
      padding: 0 6px;
      line-height: 22px;
      font-size: 12px;
      font-family: monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      cursor: pointer;
      transition: opacity 0.15s ease;
    }}
    .frame-bar:hover {{
      opacity: 0.85;
    }}
    .c0 {{ background: #e06c75; color: #fff; }}
    .c1 {{ background: #d19a66; color: #fff; }}
    .c2 {{ background: #e5c07b; color: #000; }}
    .c3 {{ background: #98c379; color: #000; }}
    .c4 {{ background: #56b6c2; color: #000; }}
    .c5 {{ background: #61afef; color: #fff; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>{title}</h1>
    <div>
      <span class="badge">Engine: {engine}</span>
      <span class="badge">Records: {len(records)}</span>
      <span class="badge">Continuous Heap Profiler</span>
    </div>
  </div>

  <div class="stats-card">
    <pre>{stats_json}</pre>
  </div>

  <div class="flamegraph-container" id="flamegraph">
    <!-- Hierarchical Frame Rendering -->
    <div id="frames"></div>
  </div>

  <script>
    const records = {records_json};
    const container = document.getElementById("frames");
    const colors = ["c0", "c1", "c2", "c3", "c4", "c5"];

    if (!records || records.length === 0) {{
      container.innerHTML = '<div style="padding:20px; color:#888;">No distinct memory allocations recorded in this window.</div>';
    }} else {{
      let maxBytes = records.reduce((max, r) => Math.max(max, r.bytes || 0), 1);
      records.forEach((rec, idx) => {{
        let div = document.createElement("div");
        let pct = Math.max(5, Math.min(100, (rec.bytes / maxBytes) * 100));
        div.className = "frame-bar " + colors[idx % colors.length];
        div.style.width = pct + "%";
        div.textContent = `${{rec.file}}:${{rec.line}} — ${{rec.bytes}} bytes (${{rec.count}} allocs)`;
        div.title = `Stack: ${{rec.stack.join(" -> ")}}`;
        container.appendChild(div);
      }});
    }}
  </script>
</body>
</html>
"""


# Global singleton profiler
_GLOBAL_PROFILER: Optional[MemrayProfile] = None
_GLOBAL_LOCK = threading.Lock()


def get_memray_profiler() -> MemrayProfile:
    """Return the global continuous MemrayProfile singleton."""
    global _GLOBAL_PROFILER
    with _GLOBAL_LOCK:
        if _GLOBAL_PROFILER is None:
            _GLOBAL_PROFILER = MemrayProfile()
        return _GLOBAL_PROFILER
