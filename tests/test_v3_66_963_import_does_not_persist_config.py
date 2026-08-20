"""Importing the app must not write app_config.json to the cwd (item 40).

THE DEFECT. `_load_app_config()` runs at MODULE SCOPE, and on a truly first run
it seeds `path_allowlist` and persisted it immediately -- so a bare
`import bulk_downloader.app` from any directory deposited app_config.json
there. This is item 11's class for a non-database resource: @926 removed every
module-scope DATABASE writer and moved them into boot_once(), and its
denominator was databases, so this one survived.

THE SECURITY PROPERTY MUST SURVIVE THE FIX. The seed exists because a DAST
audit flagged that an empty allowlist accepts cookie_file=/etc/passwd (v3.47.8,
#80). `_validate_path()` reads the IN-MEMORY `_app_cfg["path_allowlist"]`, so
seeding at import is what protects; persisting only makes it visible in
Settings. Deferring the WRITE costs nothing defensively -- but a fix that
dropped the SEED would quietly widen the attack surface, so both are asserted.

AND THE WRITER MATTERS AS MUCH AS THE TIMING. The first attempt deferred the
write but kept calling `_save_app_config()`, which writes app.py's in-memory
`_app_cfg` WHOLESALE -- a snapshot taken at import. Anything another writer
persisted since is absent from it and gets erased. `api_tokens._signing_secret()`
stores `api_auth_token_secret` through `global_config.set_config()`, so the
deferred write erased it, the next call minted a fresh secret, and every
already-issued token failed verification: 403 became 401. `set_config()` does a
read-modify-write and merges. The lost-update hazard in `_save_app_config()`
itself predates this cut and is item 41.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _run(tmp: str, probe: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "BD_INSTALL_DIR"}
    env["LC_ALL"] = "C"  # row 178: collation must not depend on the host locale
    env["PYTHONPATH"] = str(_REPO)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    r = subprocess.run([sys.executable, "-c", probe], cwd=tmp, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"probe failed: {r.stderr[-800:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_importing_the_app_does_not_write_app_config():
    tmp = tempfile.mkdtemp(prefix="item40_cfg_")
    _run(tmp, "import json, bulk_downloader.app; print(json.dumps({}))")
    assert not (Path(tmp) / "app_config.json").exists(), (
        "importing bulk_downloader.app wrote app_config.json into the cwd. A "
        "module import must not persist configuration; the seed belongs in "
        "boot_once(), where @926 put the database writers for the same reason.")


def test_the_first_run_allowlist_seed_still_happens_in_memory():
    """The over-correction direction: dropping the seed widens the attack
    surface the v3.47.8 DAST fix narrowed."""
    tmp = tempfile.mkdtemp(prefix="item40_seed_")
    out = _run(tmp, "import json, bulk_downloader.app as a;"
                    "print(json.dumps({'a': list(a._app_cfg.get('path_allowlist') or [])}))")
    assert out["a"], (
        "path_allowlist is EMPTY after a first-run import. An empty allowlist "
        "is the permissive setting the seed exists to replace -- the write may "
        "be deferred, the seed may not.")


def test_boot_once_persists_the_deferred_seed():
    """The other end of the deferral.

    An earlier draft bound the drain's logger to a name unbound in
    boot_once()'s scope. Both tests above still passed -- neither calls
    boot_once(), so the deferred half never executed. Deferring work into a
    function nothing in the band invokes is how a fix ships broken.
    """
    tmp = tempfile.mkdtemp(prefix="item40_boot_")
    out = _run(tmp, "import json, pathlib, bulk_downloader.app as a;"
                    "a.boot_once(force=True);"
                    "p = pathlib.Path('app_config.json');"
                    "print(json.dumps({'e': p.exists(),"
                    " 'a': (json.loads(p.read_text()).get('path_allowlist') if p.exists() else [])}))")
    assert out["e"], "boot_once() did not persist the deferred seed"
    assert out["a"], "the persisted config carries an empty allowlist"


def test_the_deferred_write_does_not_erase_another_writers_key():
    """The regression that killed the first attempt, pinned.

    A whole-dict write would erase api_auth_token_secret and turn every issued
    API token invalid. This drives the real boot path with a sentinel already
    on disk, exactly as api_tokens._signing_secret() would have left it.
    """
    tmp = tempfile.mkdtemp(prefix="item40_lost_")
    out = _run(tmp, "import json, pathlib, bulk_downloader.app as a;"
                    "from bulk_downloader import global_config as gc;"
                    "gc.set_config({'api_auth_token_secret': 'SENTINEL'});"
                    "a.boot_once(force=True);"
                    "d = json.loads(pathlib.Path('app_config.json').read_text());"
                    "print(json.dumps({'s': d.get('api_auth_token_secret'),"
                    " 'a': d.get('path_allowlist') or []}))")
    assert out["s"] == "SENTINEL", (
        "the deferred seed erased api_auth_token_secret written by another "
        "writer. _save_app_config() writes app.py's in-memory _app_cfg "
        "wholesale; use global_config.set_config(), which merges. This is what "
        "turned test_no_mintable_scope_can_reach_an_admin_route 403 -> 401.")
    assert out["a"], "the seed itself did not land"
