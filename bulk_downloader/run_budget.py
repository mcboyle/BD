"""bulk_downloader.run_budget -- RUN-3: unified per-run resource budget.

Before this, the three run-cost dimensions were enforced in separate places and
memory was not covered at all:
  * net  -> daily_budget (per-site / global byte cap; queue-level breach->pause)
  * wall -> hls_downloader max_runtime_s (terminates a stuck ffmpeg)
  * mem  -> (nothing)

`RunBudget` unifies the three into one evaluator, and the runner wires the
previously-missing MEMORY leg as an admission gate (mirroring the proven
daily_budget seam: on breach it requeues + pauses the site rather than crashing).

Everything is DEFAULT-OFF: a zero/unset limit is uncapped, so an operator who
sets nothing sees byte-identical behavior. Config is read from the site cfg dict
(like daily_byte_budget) with an env fallback -- no GLOBAL_CONFIG_SCHEMA key, so
no ratchet / SPA surface. An active memory gate reports sampler failure as
UNKNOWN so the runner can hold admission without treating missing RSS as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _int(v, default: int = 0) -> int:
    try:
        n = int(v)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return default


@dataclass
class RunBudget:
    """A per-run resource ceiling. 0 on any field means "uncapped"."""
    wall_s: int = 0        # max wall-clock seconds for one run
    mem_mb: int = 0        # max process RSS (MB) before we stop taking new work
    net_bytes: int = 0     # max bytes for one run / window (net leg)

    def is_active(self) -> bool:
        return bool(self.wall_s or self.mem_mb or self.net_bytes)

    def breach(self, *, elapsed_s: float = 0.0, rss_mb: float = 0.0,
               bytes_done: int = 0) -> Optional[str]:
        """Return the name of the first breached dimension ('wall'|'mem'|'net'),
        or None. A zero limit on a dimension never breaches."""
        if self.wall_s and elapsed_s >= self.wall_s:
            return "wall"
        if self.mem_mb and rss_mb >= self.mem_mb:
            return "mem"
        if self.net_bytes and bytes_done >= self.net_bytes:
            return "net"
        return None


def from_config(cfg: Optional[dict]) -> RunBudget:
    """Build a RunBudget from a site cfg dict. Keys (all optional, 0/absent =
    uncapped): run_wall_budget_s, run_mem_budget_mb, daily_byte_budget (reused
    as the net leg). Read from the site config only -- no env vars, so this adds
    no entry to the config-surface env inventory."""
    cfg = cfg or {}
    wall = _int(cfg.get("run_wall_budget_s"))
    mem = _int(cfg.get("run_mem_budget_mb"))
    net = _int(cfg.get("daily_byte_budget"))
    return RunBudget(wall_s=wall, mem_mb=mem, net_bytes=net)


def current_rss_mb() -> Optional[float]:
    """Current process RSS in MB, or ``None`` when it cannot be sampled."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        return None


def is_over_mem_budget(cfg: Optional[dict], *, rss_mb: Optional[float] = None) -> dict:
    """Admission-gate check: is the process over its configured memory budget?

    Returns {over, rss_mb, budget_mb, available, unknown}. A disabled gate is a
    measured over=False. With an active gate, sampler failure is over=None and
    unknown=True, which the runner treats as a visible admission hold.
    """
    b = from_config(cfg)
    out = {"over": False, "rss_mb": 0.0, "budget_mb": b.mem_mb,
           "available": True, "unknown": False, "error": ""}
    if b.mem_mb <= 0:
        return out
    r = current_rss_mb() if rss_mb is None else float(rss_mb)
    if r is None:
        return {"over": None, "rss_mb": None, "budget_mb": b.mem_mb,
                "available": False, "unknown": True,
                "error": "RSS measurement unavailable"}
    out["rss_mb"] = round(r, 2)
    out["over"] = r >= b.mem_mb
    return out
