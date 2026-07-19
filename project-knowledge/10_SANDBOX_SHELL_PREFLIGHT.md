<!-- verified-against: v3.66.185 -->
# #10 — Sandbox shell pre-flight (treat as a hard checklist)

`bash_tool` is **`/bin/sh` (dash)**, a fresh shell per call, no persisted env, network OFF. Most mid-task
command failures trace back to forgetting one of these. Read before the first command, not after a failure.

## The hard constraints
- **Process substitution `<()` does NOT exist in dash.** `diff <(a) <(b)` errors with
  `Syntax error: "(" unexpected`. Use temp files: `a >/tmp/x; b >/tmp/y; diff /tmp/x /tmp/y`.
- **Bash-isms / heredocs / arrays / `[[ ]]` need `bd bash -c '…'`.** Plain `bash_tool` won't run them.
- **No persisted env between calls.** Each call re-enters a bare shell. Use `bd python3` / `bd bash` to load the
  env (`PYTHONPATH=/tmp/prestaged_site_packages` for Flask, `PLAYWRIGHT_BROWSERS_PATH`, `DISPLAY=:99`).
- **Network is off** for `bash_tool`; loopback works. Live browser/noVNC launches are **not** runtime-testable
  here.

## Test-running constraints
- **Never run the whole `tests/` dir** — it hangs at `test_perf_lab.py`. Run targeted:
  `python3 run_tests.py tests/<file>.py`. (`test_v3_66_146_nav_guard` also times out >200s — known.)
- Custom runner: **chdirs to a temp dir per run** (tests derive repo root from `__file__`); **no pytest
  builtins** (no `tmp_path` → use `tempfile.mkdtemp`); zero-arg test functions; **`monkeypatch` unreliable →
  restore module globals in `try/finally`**.
- App-booting tests need `pytestmark = pytest.mark.bd_module_wipe` + `db.db_init()`; conftest autouse
  `isolated_bd_home` sets `BD_HOME=tmp_path`.

## Before-you-edit
- **Snapshot originals** into `/home/claude/patches/originals/` (one per file per version baseline) before any
  edit.

## Quick template (the shape that works)
```
bd bash -c 'cd /home/claude/work && BD_DISABLE_KEEPALIVE=1 python3 run_tests.py tests/<file>.py'
```
