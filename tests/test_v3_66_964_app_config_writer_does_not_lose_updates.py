"""`_save_app_config()` must not clobber another writer's keys or widen the mode.

ITEM 41, found while fixing item 40. Two writers persist app_config.json:

  * `global_config.set_config()` -- read-modify-write, merges, and chmods the
    temp file to 0600 BEFORE the rename so the persisted file is never
    group/world-readable (F-COREBD11-01);
  * `app.py._save_app_config()` -- writes app.py's in-memory `_app_cfg`
    WHOLESALE, from a snapshot taken at import, with no chmod.

So `_save_app_config()` loses BOTH things set_config was careful about.
Measured at v3.66.964 before the fix:

    after set_config      : mode 0o600  secret=SENTINEL
    after _save_app_config: mode 0o644  secret=None

THE KEY LOSS IS NOT COSMETIC. `api_tokens._signing_secret()` stores
`api_auth_token_secret` through set_config(). Erasing it makes the next call
mint a FRESH secret, and every already-issued API token then fails
verification -- exactly the 403 -> 401 that took item 40 two attempts. Any live
path calling `_save_app_config()` after a token is minted does this today.

THE MODE LOSS IS THE SAME SHAPE: a security property established deliberately
in one writer and silently undone by the other. 0644 on a file holding a
signing secret is world-readable on a multi-user host.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _run(probe: str) -> dict:
    tmp = tempfile.mkdtemp(prefix="item41_")
    env = {k: v for k, v in os.environ.items() if k != "BD_INSTALL_DIR"}
    env["PYTHONPATH"] = str(_REPO)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    r = subprocess.run([sys.executable, "-c", probe], cwd=tmp, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"probe failed: {r.stderr[-800:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


_PROBE = (
    "import json, os, stat, pathlib;"
    "import bulk_downloader.app as a;"
    "from bulk_downloader import global_config as gc;"
    # log_level is written to DISK as INFO and held in MEMORY as DEBUG, so the
    # two sources DISAGREE on the same key. Without that the merge direction is
    # unobservable -- a key absent from disk yields DEBUG either way, and a
    # mutation inverting the merge escaped exactly that way.
    "gc.set_config({'api_auth_token_secret': 'SENTINEL', 'foreign_key': 7,"
    " 'log_level': 'INFO'});"
    "a._app_cfg['log_level'] = 'DEBUG';"
    "a._save_app_config();"
    "p = pathlib.Path('app_config.json');"
    "d = json.loads(p.read_text());"
    "print(json.dumps({'secret': d.get('api_auth_token_secret'),"
    " 'foreign': d.get('foreign_key'), 'own': d.get('log_level'),"
    " 'mode': oct(stat.S_IMODE(p.stat().st_mode))}))"
)


def test_saving_preserves_another_writers_keys():
    out = _run(_PROBE)
    assert out["secret"] == "SENTINEL", (
        "_save_app_config() erased api_auth_token_secret. It writes app.py's "
        "in-memory _app_cfg wholesale -- a snapshot from import -- over a file "
        "global_config.set_config() also writes. Erasing that key invalidates "
        "every issued API token (item 40's 403 -> 401).")
    assert out["foreign"] == 7, (
        "_save_app_config() erased an unrelated key another writer persisted; "
        "the loss is not specific to the token secret.")


def test_saving_still_persists_its_own_values():
    """The over-correction direction: a merge that lost app.py's OWN edits
    would be worse than the bug. Disk must not win over the in-memory dict."""
    out = _run(_PROBE)
    assert out["own"] == "DEBUG", (
        f"_save_app_config() persisted {out['own']!r} for log_level, but "
        f"app.py holds 'DEBUG' and disk held 'INFO'. The merge direction is "
        f"disk first, _app_cfg overlaid ON TOP -- app.py's values win for the "
        f"keys it manages. Disk winning would silently discard every setting "
        f"the running process changed.")


def test_saving_does_not_widen_the_file_mode():
    """set_config chmods 0600 before the rename precisely so a token-bearing
    config is never group/world-readable. A second writer that renames a
    default-umask temp file over it undoes that silently."""
    out = _run(_PROBE)
    assert out["mode"] == "0o600", (
        f"app_config.json is {out['mode']} after _save_app_config(), not 0o600. "
        f"set_config establishes owner-only on a file that may hold tokens "
        f"(F-COREBD11-01); this writer must not widen it back.")
