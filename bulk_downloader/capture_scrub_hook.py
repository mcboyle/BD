"""F0.3 — scrub-on-capture (default ON).

After an operator capture finishes and its raw WACZ is written, this hook
auto-invokes the standalone ``tools/capture_scrub.py`` redactor to produce
a share-ready ``<name>.redacted.wacz`` twin next to the raw, and appends a
value-free manifest line recording the scrub tool version + redaction
kinds/counts. The raw WACZ is **never** touched and stays local-only — F2
policy is byte-for-byte unchanged; this only *adds* a scrubbed sibling so
the operator always has a share-ready copy without remembering to run the
tool by hand.

Wiring (zero guard edit): the WACZ writer is ``tools/capture_session.py``
(guard #3), so the scrub is invoked at the *callers* post-save — the
cockpit task-completion path (``tools/cockpit_core.py``) and the
sentinel-finish wrapper (``tools/onboard_site_template.py``). The scrubber
itself (``tools/capture_scrub.py``) is invoked unchanged.

Posture / safety:
  * **Fail-soft, never blocks the save.** A missing tool, a non-zero exit,
    a timeout, or any exception is swallowed and recorded; the raw WACZ is
    preserved regardless.
  * **Default ON** (``automation.scrub_on_capture_enabled``, default True)
    — this is the one capture-frontier toggle that ships on, per F0.3.
    Flip it off for opt-in behaviour.
  * **Counts only.** The manifest records redaction *kinds + counts* parsed
    from the tool's non-preview stdout (which prints no values) plus the
    tool's content SHA as a version pin — never any captured value.
  * **Idempotent.** A ``*.redacted.wacz`` input is skipped, so re-running
    over a captures dir never scrubs a scrub.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ENABLE_KEY = "automation.scrub_on_capture_enabled"   # default TRUE (ships on)
TOOL_KEY = "automation.scrub_on_capture_tool"        # optional absolute-path override
DEFAULT_MODE = "safe"
_SCRUB_TOOL_REL = "tools/capture_scrub.py"
_MANIFEST_NAME = "scrub_manifest.jsonl"


def _root() -> Path:
    # bulk_downloader/capture_scrub_hook.py -> repo root is two parents up.
    return Path(__file__).resolve().parent.parent


def _enabled() -> bool:
    try:
        from . import global_config
        return bool(global_config.get(ENABLE_KEY, True))  # DEFAULT ON
    except Exception:
        return True


def _tool_path() -> Optional[Path]:
    try:
        from . import global_config
        override = global_config.get(TOOL_KEY, None)
    except Exception:
        override = None
    p = Path(override) if override else (_root() / _SCRUB_TOOL_REL)
    return p if p.is_file() else None


def _expected_output(wacz_path: str) -> str:
    """The scrubber's native output naming for a .wacz input."""
    return re.sub(r"\.wacz$", "", wacz_path, flags=re.I) + ".redacted.wacz"


def _tool_sha(tool: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(tool.read_bytes()).hexdigest()[:12]
    except Exception:
        return "sha256:unknown"


def _parse_redactions(stdout: str) -> Dict[str, int]:
    """Parse the tool's value-free 'redactions:' block into {kind: count}.

    The tool prints (non-preview):
        redactions:
             6  some_kind
             2  other_kind
    one '<count>  <kind>' line per kind. Robust to formatting drift; on any
    surprise it just returns what it could parse (possibly empty)."""
    out: Dict[str, int] = {}
    if not stdout:
        return out
    in_block = False
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not in_block:
            if line.strip().lower() == "redactions:":
                in_block = True
            continue
        m = re.match(r"\s+(\d+)\s+(\S.*)$", line)
        if m:
            out[m.group(2).strip()] = int(m.group(1))
            continue
        # a non-matching, non-blank line ends the block
        if line.strip():
            break
    return out


def _default_runner(tool: Path, wacz_path: str,
                    mode: str) -> Tuple[int, str, str]:
    """Run the scrubber as a fixed-argv subprocess (shell=False). Returns
    (exit_code, stdout, expected_output_path)."""
    out_path = _expected_output(wacz_path)
    interp = sys.executable or str(_root() / "venv" / "bin" / "python")
    proc = subprocess.run(
        [interp, str(tool), wacz_path, "--mode", mode],
        capture_output=True, text=True, shell=False, timeout=300,
        cwd=str(_root()),
    )
    return proc.returncode, (proc.stdout or ""), out_path


def _status_for(code: int) -> str:
    return {0: "clean", 2: "residual"}.get(code, "error")


def _manifest_path_for(wacz_path: str,
                       manifest_path: Optional[Path]) -> Path:
    if manifest_path is not None:
        return Path(manifest_path)
    return Path(wacz_path).resolve().parent / _MANIFEST_NAME


def _write_manifest(line: Dict[str, Any], path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


def scrub_on_capture(wacz_path: str, *,
                     enabled: Optional[bool] = None,
                     mode: str = DEFAULT_MODE,
                     manifest_path: Optional[Path] = None,
                     _runner: Optional[Callable[[Path, str, str],
                                                Tuple[int, str, str]]] = None
                     ) -> Dict[str, Any]:
    """Produce a share-ready scrubbed twin of ``wacz_path`` and record a
    manifest line. Never raises; never touches the raw WACZ. Returns a small
    status dict.

    ``_runner`` is an injection seam for tests so the real subprocess isn't
    invoked; it must return ``(exit_code, stdout, output_path)``."""
    try:
        wp = str(wacz_path)
        if not wp.lower().endswith(".wacz") or wp.lower().endswith(".redacted.wacz"):
            return {"ran": False, "reason": "not_a_raw_wacz"}
        if not (enabled if enabled is not None else _enabled()):
            return {"ran": False, "reason": "disabled"}

        tool = _tool_path()
        mpath = _manifest_path_for(wp, manifest_path)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if tool is None:
            line = {"ts": ts, "source": os.path.basename(wp),
                    "status": "tool_missing", "mode": mode}
            _write_manifest(line, mpath)
            return {"ran": False, "reason": "tool_missing"}

        try:
            code, stdout, out_path = (_runner or _default_runner)(tool, wp, mode)
        except Exception as e:
            line = {"ts": ts, "source": os.path.basename(wp),
                    "status": "error", "error": type(e).__name__, "mode": mode,
                    "scrub_tool": _SCRUB_TOOL_REL, "scrub_tool_sha": _tool_sha(tool)}
            _write_manifest(line, mpath)
            return {"ran": False, "reason": f"error:{type(e).__name__}"}

        status = _status_for(code)
        redactions = _parse_redactions(stdout)
        out_written = status == "clean" and bool(out_path) and Path(out_path).exists()
        line = {
            "ts": ts,
            "source": os.path.basename(wp),
            "output": os.path.basename(out_path) if out_written else None,
            "status": status,
            "exit_code": int(code),
            "mode": mode,
            "redactions": redactions,
            "redaction_total": sum(redactions.values()),
            "scrub_tool": _SCRUB_TOOL_REL,
            "scrub_tool_sha": _tool_sha(tool),
        }
        _write_manifest(line, mpath)
        return {"ran": True, "status": status, "exit_code": int(code),
                "output": line["output"], "redaction_total": line["redaction_total"]}
    except Exception as e:  # belt-and-braces: the save must never break
        return {"ran": False, "reason": f"error:{type(e).__name__}"}


def main(argv) -> int:
    """CLI entry for the sentinel-finish wrapper's generated script:
    ``python -m bulk_downloader.capture_scrub_hook <wacz>``. Always returns 0
    so a non-zero never aborts the (``set -e``) capture script."""
    if len(argv) < 2:
        return 0
    try:
        res = scrub_on_capture(argv[1])
        sys.stderr.write(f"[scrub] {res}\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
