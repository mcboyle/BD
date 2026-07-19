"""v3.66.321 — data-layer fresh-process import path.

Regression lock for the `/api/data/template_health` failure observed on stash
right after a restart: ``{"error":"No module named 'template_inventory'"}``.

Root cause: the data-layer collectors import their tools modules as
``import tools.template_core`` (needs the REPO ROOT on sys.path), but those tools
modules in turn use CLI-style bare sibling imports (``import template_inventory``)
that need ``tools/`` ITSELF on the path. ``_ensure_path()`` only added the repo
root, so the FIRST hit to a collector in a fresh process raised
ModuleNotFoundError. The in-process test suite doesn't catch it because by the
time it runs, some earlier import has already put ``tools/`` on sys.path — so this
test drives a CLEAN interpreter (subprocess) with ONLY the repo root on the path,
exactly reproducing the live service's post-restart state.

Sandbox: custom runner; zero-arg test; uses a subprocess for a pristine sys.path.
"""
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _run_in_clean_interpreter(collector):
    """Call a data-layer collector in a fresh interpreter whose sys.path has the
    repo root but NOT tools/ (the live post-restart shape). Returns (rc, out, err)."""
    code = (
        "import sys;"
        "sys.path[:] = [p for p in sys.path if not p.rstrip('/').endswith('tools')];"
        f"sys.path.insert(0, {str(_REPO)!r});"
        f"from bulk_downloader.app_data_layer import {collector} as c;"
        "o=c();"
        "assert isinstance(o, dict), type(o);"
        "print('OK', sorted(o)[:3])"
    )
    env = dict(os.environ)
    # keep prestaged Flask importable; strip any tools/ that PYTHONPATH injects
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in pp.split(os.pathsep) if p and not p.rstrip("/").endswith("tools"))
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=env)


def test_template_health_resolves_in_fresh_process():
    r = _run_in_clean_interpreter("collect_template_analytics")
    assert r.returncode == 0, f"template_health collector failed:\n{r.stderr}"
    assert "OK" in r.stdout, r.stdout


def test_capture_diagnostics_resolves_in_fresh_process():
    # same bare-sibling-import family; proves the fix generalizes past template_core
    r = _run_in_clean_interpreter("collect_capture_diagnostics")
    assert r.returncode == 0, f"capture_diagnostics collector failed:\n{r.stderr}"
    assert "OK" in r.stdout, r.stdout
