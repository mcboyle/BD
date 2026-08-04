<!-- verified-against: v3.66.47 -->
KB_VERSION: 6
LAST_VERIFIED: v3.66.47 merge, 2026-05-29

# BulkDownloader — DANGER MAP (consolidated)

Things a fresh Claude instance could confidently break without
realizing: fragile areas, load-bearing invariants, intentional designs
that look like bugs, and platform gotchas.

If the operator keeps a structured `INVARIANT_REGISTRY.md` /
`02_DANGER_MAP.md`, those are canonical — this file consolidates and
supplements them.

**A.1 inline `# INV-NNN` tag status (as of v3.64.5 merge).** A.1 is
RESTORED. v3.64.4 brought back 33 `# INV-` lines across 10 source
files (`db.py`, `detect.py`, `dev_suite.py`, `extractors.py`,
`heuristic_scoring.py`, `hls_downloader.py`, `runner.py`,
`secrets_store.py`, `session_keeper.py`, `vpn_config.py`), plus
`INV_TAGS.md` (locality index) and `KB_BUDGET_BASELINE.md` at repo
root, plus `_pre_compaction_v3.64.1/` (snapshot dir, prune at
v3.66.0), plus regression test
`tests/test_inv_tags_not_regressed.py` with `INV_TAG_FLOOR = 33`.
The breakdown: **19 inline tags + 8 file-top markers + 2 INV-003
explanatory comments + 4 pre-existing `dev_suite.py` dispatch-tracer
tags = 33 total.**

INV-IDs in section headers below correspond to inline tags in
source. To find the in-source location for an invariant, either
grep `# INV-NNN` in `bulk_downloader/` or run
`venv/bin/python tools/explain_invariant.py INV-NNN` (always
`venv/bin/python`, never bare `python`/`python3` -- see CLAUDE.md
section 5). The prior merge's
"operator decision pending" prose is retired (see
`OPEN_THREADS_archive.md` for the resolution narrative).

-----

## LOAD-BEARING INVARIANTS — do not “clean up”

### `runner.py::_process_one` dispatch order

The central download dispatch walks a fixed priority chain; each step
fails open and falls through on failure. The real chain (verified
against the v3.62.6 source via the `dev_suite._DISPATCH_CHAIN` mirror,
which `tests/test_dispatch_chain.py` pins) is, in order:

1. **retry_backoff** — `job.retry_after` set and still future → defer.
2. **auto_teach** — `_handle_auto_teach_check()` handled the URL.
3. **cluster_rate** — `use_cluster_rate` on and lease cap hit → defer.
4. **cookie_relogin** — `_check_cookies_or_relogin()` failed → defer.
5. **stash_dedup** — `_stash_dedup_check()`: already in Stash → skip.
6. **library_extractor** — `use_library_extractor` + a maintained
   extractor library matches the host → in-process direct/HLS.
7. **jsonapi_extractor** — `use_jsonapi` + `jsonapi_url` set.
8. **qbittorrent** — `backend == 'qbittorrent'` or a magnet/.torrent.
9. **jdownloader** — `backend == 'jd'`.
10. **playwright_teach** — nothing above handled it → `_do_download`.

Order matters: the stash-dedup check (step 5) must stay before any
download step, or the box re-downloads multi-GB files it already has.
Never reorder or insert a short-circuit. (Invariant INV-002.)

**Correction note:** earlier KB text described a “content-rights /
blocked-content check running before the library extractor.” No such
branch is in `_process_one` — the chain goes stash-dedup →
library-extractor directly. (A `content_rights.py` module does exist,
but it is not wired into the dispatch chain.) If the chain ever does
legitimately change, update both `dev_suite._DISPATCH_CHAIN` and
`tests/test_dispatch_chain.py` together — see the dispatch-tracer
invariant below.

### Phase B login fallback stays in `login_async` — not `_process_one`

The v3.62.2 login-template runtime fallback (a failed templated
auto-login escalates to a manual takeover) lives in
`runner.py::login_async`, in its handling of the `do_login` result —
deliberately NOT in `_process_one`. It was kept off the dispatch
surface precisely so INV-002 stays untouched. A fresh instance
“consolidating all login handling into `_process_one`” would drag a
load-bearing chain back open for no benefit. Leave the fallback where
it is.

### The dispatch tracer mirrors `_process_one` — keep the two in sync

`dev_suite` carries `_DISPATCH_CHAIN` (today in
`bulk_downloader/dev_suite/audit_security.py`, re-exported from the
package `__init__.py` -- grep for the name rather than trusting the
path), a hand-maintained mirror of
the branch order inside `runner.py::_process_one`, plus
`dispatch_chain()` / `dispatch_dry_run()` tools built on it. A drift
guard protects the mirror: `dispatch_chain()` re-reads `_process_one`
and verifies all ten branch markers appear in documented order, and
`tests/test_dispatch_chain.py` pins that sequence — a reorder of
`_process_one` fails the suite loudly. Do not edit `_process_one` to
“help” the tracer (a debug log line, a tidier branch) — any change to
its branch order is a real INV-002 event. If you legitimately change
the chain, update `_DISPATCH_CHAIN` *and* the pinned test together.

### `learned.login` is the login-template teach-skip trigger

Applying a login template populates `config['learned']['login']`.
`runner.py` (around line 2615) reads
`learned_login = (config.get("learned") or {}).get("login")` and
treats a populated `learned.login` as “a template is already applied,
skip the teach step.” Clearing, renaming, or restructuring
`learned.login` silently disables teach-skip, so every templated site
re-runs the teach flow. Treat `config['learned']['login']` as a
load-bearing key, not scratch state.

### `session_keeper` — pause before any nested `sync_playwright` spawn

Playwright’s sync API is thread-bound. The session keeper holds a
persistent Chromium per (site, account). Any code path that spawns a
**new** `sync_playwright` for a site with a live keeper must call
`session_keeper.pause_site_keepers(site_id)` first, or the two collide
and hang/crash. This is the single most-repeated bug class in the
project’s history (shipped live twice). Known-correct call sites:
`login_async`, `_playlist_expand_one`, `_search_site`, `do_login`’s
callers, `_auto_relogin`. (Invariant INV-001.)

### `resume_site_keepers` does NOT exist — and must never be created

`session_keeper.py` has `pause_site_keepers` but deliberately **no**
`resume_*`. After a pause tears down the keeper’s browser, the keeper
detects the torn-down browser on its next heartbeat and relaunches
itself. A fresh instance will naturally assume a matching `resume_*`
exists and “fix” the asymmetry — do not. v3.43.79 removed exactly that
mistake. (INV-001 anti-pattern clause.)

### `db.py::db_conn` — the WAL isolation-level hack is load-bearing

`db_conn()` sets `connection.isolation_level = None` before
`PRAGMA journal_mode=WAL`, calls `fetchone()` on the result, then
restores `isolation_level = ""`. This looks like removable
boilerplate. It is not — without it, Python’s implicit `BEGIN` wraps
the PRAGMA in a transaction and WAL silently never sets, reintroducing
“database is locked” under concurrent workers. The pragmas
`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000` are all
required. (INV-004.)

### Database backups must include the WAL sidecars

A WAL-mode DB is three files: `.db`, `.db-wal`, `.db-shm`. Backing up
only `.db` captures a stale snapshot. `backup.py` copies all three —
do not write a “simpler” backup that copies only `.db`.

### bw_chart time-series must be built in UTC

`hourly_bandwidth()` and `hourly_jobs()` group rows by SQLite
`strftime` over the UTC `ts` column. The Python-side dense-series keys
MUST also be built in UTC (`datetime.now(timezone.utc)`), or they miss
on every non-UTC machine and the charts read zero. Fixed in v3.62.1 —
do not reintroduce `datetime.now()` (local) here or anywhere matching
SQLite time grouping.

### `history.honeypot_score` is a value column, not a time column

Added v3.66.36 (migration v7) as `REAL DEFAULT NULL`. It holds a float
in [0,1] or NULL — it is NOT part of the UTC-text / epoch-REAL time
split, so don't treat it like `ts` / `retry_after`. Written only via
the optional `db_log(honeypot_score=...)` kwarg; stays NULL until the
P5-2b resolve→runner→`db_log` self-feeding wire lands (the per-site
learner is inert in the meantime). See SQL_SCHEMA.md and OPEN_THREADS
P5-2b.

### Two parallel resolution tables must stay synced

`heuristic_scoring.RESOLUTION_TIERS` and `detect.res_label` /
`detect._RES_LABEL_PATTERNS` encode resolution data via two separate
code paths (`res_score` uses a generic `\d{3,4}p\b` regex; `res_label`
uses explicit bracket logic). They are not generated from a shared
source. Changing one without the others causes silent
quality-selection bugs. Regex quirk: `\b` does not match between `p`
and `_`. (INV-005.)

### Credentials must route through the vault abstraction

Never read `cfg["password"]` directly. Login code calls
`secrets_store.resolve_password()`; VPN code calls
`vpn_config.resolve_secrets()` before rendering OpenVPN/WireGuard
config. A raw read leaves `@cred:LABEL` reference syntax unresolved and
can leak the placeholder into an auth file. (INV-006.)

### Module-level startup must be env-gated

`app.py` spawns daemon threads (`_start_session_keepers()`,
schedulers) at **import time**. `BD_DISABLE_KEEPALIVE=1` gates them.
Any new background subsystem must be gated the same way, or it spawns
during every test import.

### The dev-tools modules must stay import-clean

The dev-tools modules — `perf_lab.py`, the `dev_suite/` package
(every submodule and its `__init__.py`), and (added
v3.62.6) `dev_metrics.py` and `dev_events.py` — do **no work at
import**: no thread spawns, no DB access, no network, no file I/O, no
heavy allocation at module load. `app.py` reaches them by *lazy*
import inside route bodies / hooks. `app.py` imports are exercised by
every test; a module doing work at import would spawn during every
test run (the same reason `BD_DISABLE_KEEPALIVE` exists). Allowed at
module scope: empty containers and constants — a bounded `deque`, a
`Lock`, hint tuples/sets, an empty registry dict. The ring buffers in
`dev_metrics.py` / `dev_events.py` and the fixture-site registry are
empty at import; they fill only when a hook fires or a tool is called.
The perf-lab load injector’s threads start ONLY when `start_injection()`
is called. Do not add module-level initialization to any of these
files — if a tool needs setup, do it lazily on first call.

### The `dev_metrics` request hooks run on every request — keep them fail-safe

`app.py` has a `before_request` hook (stamps a start time on
`flask.g`), an `after_request` hook (records timing + status into
`dev_metrics`), and a `got_request_exception` signal receiver
(records unhandled exceptions). These run for **every request** and
are wrapped defensively — the recording is in `try/except`, and
`after_request` always returns the response object — so a metrics bug
cannot break request handling. Ordering note: if an earlier hook
(`_check_token`, `_check_csrf`) short-circuits with a response, the
timing hook may not run; `after_request` falls back gracefully on the
missing start-time. Do not “improve” these three hooks in a way that
can raise or that returns anything other than the response — that
breaks the whole app, not just the metrics.

### The perf-lab load injector’s reserved DB `site_id` is load-bearing

The `queue` load profile inserts synthetic rows under the reserved
`site_id` `"__loadtest__"` (`perf_lab._LOADTEST_SITE_ID`). This is
safe ONLY because the `queue` table has no foreign key on `site_id`
and `db.queue_delete_site()` removes all rows for an id in one
statement — that is how `purge()` cleanly reverses the profile. Do not
change the synthetic rows to a real `site_id`, and do not assume the
queue table gained an FK without checking.

### `perf_lab.purge()` is the single cleanup entry point

Every load profile holds real references (`_load_blobs`,
`_load_objects`, `_load_threads`) so its footprint is genuine until
released. `purge()` clears them, signals load-threads to exit, and
deletes the synthetic `__loadtest__` queue rows. `stop_injection()`
only *cancels* a run — it does NOT free held memory. Do not
“simplify” `stop` to also free, and do not drop `purge()`: the
stop/purge distinction is what the tests rely on.

### The dev-suite SQL console is SELECT-only by a token-boundary filter

`dev_suite.sql_console()` refuses anything that is not a single
read-only `SELECT`: it rejects multiple statements (a `;`), rejects a
leading keyword that is not `select`/`with`, and rejects any forbidden
keyword (`insert`, `update`, `delete`, `drop`, `pragma`, `vacuum`,
…). The forbidden-keyword check matches on **word tokens**
(`re.findall` over `[a-z_]+`), NOT substrings — deliberately, so a
column named `updated_at` is not mistaken for `UPDATE`. Do not
“tighten” this to a substring scan (it would reject legitimate column
names), and do not relax it to allow writes (mutation must go through
the app’s own code paths, not this console).

### `dev_suite` — exactly one definition per public function

`dev_suite` was built by independent appends across many turns and was
a ~7,600-line monolith; it is now the package
`bulk_downloader/dev_suite/` (`_common.py`, `audit_security.py`,
`capture_diag.py`, `config_tools.py`, `db_tools.py`, `housekeeping.py`,
`integrations_diag.py`, `introspection.py`, `jobs_runner.py`, `logs.py`,
`perf_metrics.py`, `release_lint.py`, `test_meta.py`, `vpn_diag.py`,
plus an `__init__.py` re-export shim -- re-derive the member list with
`ls bulk_downloader/dev_suite/`). The duplicate-def hazard still applies
within each submodule and across the `__init__.py` re-export surface.
Python uses the **last** module-level def of a
name; a re-introduced duplicate silently makes the earlier one dead
code, and a route that calls the dead def with the old signature
returns 500 (or fails open and falsely returns ok=True if the route
catches it). v3.62.7 cleaned up three live duplicates that had
slipped in (`filename_template_preview`, `vpn_config_render`,
`vpn_provider_rotation_view`). **Before appending any function to a
`dev_suite` submodule, grep first:**
`grep -rnE "^def [a-z_]+" bulk_downloader/dev_suite/ | awk -F'[: (]' '{print $4}' | sort | uniq -c | sort -rn | head`
— any count > 1 within a single submodule is a real bug, and a name
exported twice through `__init__.py` is the same bug across files.
No contract test currently enforces
uniqueness; the grep IS the contract. (Distinct from the duplicate
*section-number comments* described below — those are cosmetic and
correctly stay; duplicate *function definitions* are the bug.)

### `vpn._redact_config` is the canonical secret-redaction helper

Any dev tool, log line, or response body that emits VPN config calls
`vpn._redact_config` (or `Tunnel.to_dict(redact_secrets=True)`, which
calls it). v3.62.7’s T13 tests assert raw secret strings do not
appear in tool output. **Do not re-implement redaction in a dev
tool** — call the canonical helper. The T13 tests’ “no raw secret in
result” assertions catch regressions in tools that already use it; a
new VPN-config-emitting tool that skips the helper would leak with no
test catching it. If you change the helper’s masking rules, all
existing tests still apply.

### The custom runner injects exactly three named conftest fixtures

`tests/conftest.py` defines several `@pytest.fixture`s, but the
custom `run_tests.py` hard-codes the three names it auto-injects by
parameter — `clean_workdir`, `fresh_app`, `aiassist_module` — plus
`tmp_path` / `monkeypatch`. A fourth `@pytest.fixture` added to
`conftest.py` will work under real pytest but **not** under this
runner. The established alternative is a plain helper module under
`tests/` (e.g. `tests/_env.py`, added v3.62.7), imported via the
file-relative pattern:
`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _env`.
**Do not add `tests/__init__.py`** to make `from tests import _env`
work — the runner loads test files file-by-file via
`importlib.util.spec_from_file_location`, and turning `tests/` into a
package would break that loader. No contract test enforces these
rules; failure is at test-run time.

### `tests/_env.py` probes are cached at module import — keep them that way

`HAS_SYSTEMD_BUS`, `HAS_BULKDOWNLOADER_UNIT`, `HAS_OLLAMA_LOCAL` are
assigned at import (subprocess + socket connect). The host
environment is constant for the test run, so caching is correct and
fast. A “tidy up” that turns them into properties or lazy lookups
makes every assertion in `test_u41_systemd_live_tests.py` fire a
fresh subprocess probe and slows the file significantly. **Leave them
as module-level constants computed at import.**

### The `events` table is GONE — do not reintroduce it

*Retired in v3.64.3 merge. See `DANGER_MAP_archive.md` for the full
rationale. Stable since v3.62.6 (11+ releases); the actual schema is
documented in `SQL_SCHEMA.md` and the absence of `events` from
`migrations.EXPECTED_SCHEMA` is the structural enforcement.*

### The `live_tests/` WARN-vs-FAIL contract is load-bearing

A live test (`@live_test`-registered function in `live_tests/`)
returns `(level, detail)` and must **degrade to WARN — never FAIL or
raise — when it cannot conclude** (target unreachable, no DB, no
systemd bus, capability absent). FAIL means a genuine invariant was
violated; the harness records an uncaught raise as a hard FAIL.
Disruptive checks (real downloads, systemd lifecycle) run only when
the caller passes `include_disruptive=True`. A new live test that
FAILs on “could not connect” turns the suite into a crying-wolf
signal — the operator runs it post-deploy and a sandbox-style “can’t
reach it” reads as a broken deployment. Every new live test must
honour this contract.

### `live_tests/checks.py::_unit_exists()` is the only correct systemd-unit existence probe

`_unit_exists(name)` (added v3.63.3 / T56) calls
`systemctl is-enabled <name>` and treats the literal stdout
`not-found` as the signal that the unit is absent. **It must use
`is-enabled` specifically — every other systemctl query returns the
*systemd default* for the queried thing when the unit is missing,
with no error indication.** `show -p Restart <missing>` returns
`Restart=no` (rc=0, empty stderr). `is-active <missing>` returns
`'inactive'` (rc=3, empty stderr). Only `is-enabled` uniquely
returns `not-found`. A “smarter” existence probe that reads any
other property cannot distinguish “no unit” from “unit exists but
misconfigured.” Removing or replacing `_unit_exists` brings back the
five-test FAIL pattern that lived in
`test_u41_systemd_live_tests.py` from v3.62.6 through v3.63.2 —
L25/L27 mis-FAILing on any host where the bulkdownloader service
hasn’t been installed yet, which includes the fresh-machine capture
flow on stash (capture script runs the suite before
`install_service.sh`).

### `live_tests/checks.py::_L34_STREAMING_SKIP` exists for routes `urlopen.read()` cannot bound

`_L34_STREAMING_SKIP` (added v3.63.4) is a frozenset of
parameter-free GET routes that L34 `full-route-smoke` must NOT GET.
Currently `{"/api/stream"}`. The harness’s `ctx.get()` uses
`urllib.request.urlopen(url, timeout=N)`, whose `timeout` arg **only
bounds connect + headers**, not `r.read()`. An SSE endpoint returns
200 + headers in milliseconds and then blocks the response body
forever, so the `read()` call sits in the kernel until something
else kills it (T55’s 90s wall, in practice). The pre-fix capture
showed exactly this: 13s of activity, then 77s of silence in
`L34.log`. `/api/stream` is the only parameter-free streaming route
in the codebase as of v3.63.3 — the other two (`/api/import/stream/ <job_id>`, `/stream/<token>`) are already excluded by L34’s existing
`"<" not in rule` parameter-skip. New streamers must be added to
the set; the regression-pin in `tests/test_u44_l34_route_smoke.py`
greps the source to assert L34’s target filter references the set
(`not in _L34_STREAMING_SKIP`), so the gate cannot be silently
deleted while keeping only the constant. If a future fix changes
`ctx.get` to honour a true read-timeout (e.g. a low-level socket
deadline), this skip set is still correct — streaming endpoints
should never be in a one-shot smoke regardless.

### L34’s loop discriminates `rstatus is not None` from `rstatus is None`

L34’s loop has three branches, NOT two (added v3.63.4).
`ctx.get` returns `(False, code, None, ms)` on
`urllib.error.HTTPError` — i.e. the route *did* respond, just with a
4xx (or 5xx). L34’s docstring promises “A 4xx is treated as OK —
the route is wired and responding (it may simply require auth).”
The pre-fix `if rok: ... else: ...` two-branch shape silently
violated that intent: every 4xx was bucketed `unreachable` (because
the `not rok` branch saw `rbody=None` and “timed out” wasn’t in
`""`). The fix is `if rok / elif rstatus is not None / else`. Only
the `else` (rstatus is None — connection-level failure) buckets as
truly unreachable. 5xx still FAILs from either of the first two
branches. A regression-pin in
`tests/test_u44_l34_route_smoke.py::test_l34_loop_discriminates_on_rstatus`
greps the source for `elif rstatus is not None` and the functional
tests cover 400/401/500/ConnectionRefused. Do not “simplify” the
loop back to two branches — the streaming-skip fix surfaces this
discriminator (without the skip, L34 wedged before reaching most
4xx routes); reverting one without the other reopens the wedge.

### `_svc_property()` and `l27_survives_logout` must gate on `_unit_exists` BEFORE reading state

The pattern looks redundant — probe with `is-enabled`, *then* read
with `show` — but the systemd defaults-for-missing-units quirk above
is what makes the gate necessary. `_svc_property` returns `None` for
a missing unit; `l27_survives_logout` short-circuits to WARN before
its `is-active` read. A regression test in
`tests/test_t56_systemd_missing_unit.py`
(`test_l27_gates_on_unit_exists_before_reading_inactive_state`) greps
the source of `live_tests/checks.py` to ensure `_unit_exists` appears
before the inactive-FAIL branch in `l27_survives_logout`. Do not
disable that test.

### `tests/_env.py` systemd probes use an 8-second timeout with one retry

T54’s `_run_with_retry` (v3.63.2) raises the subprocess timeout to
`_SYSTEMD_PROBE_TIMEOUT_S = 8.0` and retries once on `TimeoutExpired`.
This is **defense in depth, not the v3.62.6→v3.63.2 fix** — T56 was
the real fix. T54 is kept because the systemd probes genuinely can
time out under heavier `--workers=N` load on an underpowered host,
and the cost is one extra subprocess call at module import. A
contract test in `tests/test_t54_env_probe_timeout.py:: test_source_has_no_2_second_subprocess_timeout` greps `tests/_env.py`
for the literal strings `timeout=2)` and `timeout=2,` — a future
edit cannot silently revert T54.

### `live_tests/harness.py::_run_with_timeout` daemon-thread leak is intentional

T55’s per-check timeout (v3.63.2) runs each check in a daemon thread
with a 60s default (`DEFAULT_PER_CHECK_TIMEOUT_S = 60.0`,
configurable via `--per-check-timeout`). On timeout, the thread is
**LEAKED** rather than killed. This is correct: Python has no safe
way to terminate a thread holding native resources (Playwright pages,
ffmpeg subprocesses, socket reads). The thread is a daemon so it
dies with the process at run-end. Do not “fix” the leak with
`_thread._async_raise`, `ctypes`, or any other forced-termination
trick — they corrupt native state and have caused worse failures
historically. A timed-out check is recorded as
`FAIL ... TIMEOUT after Ns (limit 60.0s)` and the run continues.

### `tests/test_live_harness.py` autouse fixture restores to the module-load snapshot

`autouse_restore_live_registry` snapshots `harness._REGISTRY` at
**module import** and restores from that snapshot around every test.
The naive pattern — “save at test entry, restore at test exit” — is
broken: if a previous test in the same file left fake registrations
in the registry, save-at-entry preserves the bad state into the next
test. The module-load snapshot is the only correct anchor. Same
pattern in `test_t55_live_test_harness_timeout.py`. **Also: the
fixture name must NOT start with underscore** — the custom runner’s
autouse discovery filters underscore-prefixed names
(`run_tests_core.py`: `not name.startswith("_")` in the autouse-
discovery filter -- grep for it; `run_tests.py` is a 12-line shim and
the line number moves), so an autouse
fixture renamed to `_restore_live_registry` silently never fires.
See LESSONS_LEARNED C6 for the transferable rule.

### T36 immediate-override windows are tagged `_immediate_override_`

`maintenance.add_window_now()` creates a one-shot window starting
now, with its label prefixed `_immediate_override_<note>`.
`end_active_overrides()` finds and removes them by that exact
prefix (`_OVERRIDE_LABEL_PREFIX` constant). A “clean up the label
format” pass that drops the underscore, prettifies the prefix, or
changes the casing without updating the constant in lockstep
orphans existing overrides — they stay active and keep pausing
workers, but the disable path can no longer see them. Treat the
prefix as a contract between writer and remover, not a cosmetic
label.

### `request_replay` redacts sensitive headers at capture time, not read time

`request_replay.record()` redacts `Cookie`, `Authorization`, and
`X-CSRF-Token` headers **before** writing into the bounded buffer.
A “safer” refactor that moves redaction to the list/get/replay
boundary breaks the actual safety property: if capture is on, the
buffer immediately holds raw live credentials; if the operator
flips the flag back off without resetting the buffer, those
credentials persist in memory. Capture-time redaction means the
buffer never holds them in the first place. Same logic for any
future capture-style buffer: redact before store, not before read.

### `request_replay.replay()` refuses to re-issue truncated bodies

Bodies in `request_replay` are truncated to `_BODY_TRUNC = 4096`;
oversized bodies end with the literal marker `...(truncated)`.
`replay()` checks for that marker and refuses to re-issue the
request. A “send it anyway with a warning” change breaks request
semantics — a half-truncated multipart, a clipped JSON, or a POST
missing trailing fields reaches the server as a malformed request
that may still partially succeed (worst case). The right answer is
refuse; the operator can re-trigger the original action from the
UI if a replay genuinely matters. (The 4KB cap itself is also
deliberate — 200 × 4KB × 2 ≈ 1.6 MB; a chatty SSE-style endpoint
would otherwise blow the buffer past tens of MB. The dev_events
event tap is sized differently for the full-body use case.)

### `login_flow_recorder.delete_login_flow()` requires the `login_flow` tag

`macro_recorder` is the underlying store, and a scroll-pagination
macro and a login flow can share a name. `delete_login_flow()`
refuses to delete a macro that exists but does not carry the
`login_flow` tag, returning `{"ok": false, "error": "macro exists but is not a login flow..."}`. A “relax to delete by name” change
makes `DELETE /api/dev/login_flow_delete?name=main` cheerfully
remove an unrelated `scroll-pagination main` macro. The tag check
is the only guard against this cross-delete; do not remove it.

### T46 L-29’s PASS does not prove the kill switch blocks under load

The shipped L-29 (`vpn_kill_switch_config_check` in
`live_tests/checks.py`, v3.63.0) is `disruptive=False` and only
inspects VPN kill-switch *configuration*. The **active counterpart**
— actually toggling ufw rules, taking down a tunnel under load,
verifying the DNS leak path is blocked, restoring state on failure
— shipped separately at v3.63.6 as `tools/vpn_kill_switch_probe.py`
(see the invariants above: ufw-only mutation, ambiguity refusal,
restore-in-finally). The two are deliberately separate: L-29 is a
read-only check that runs unattended in any capture; the active
probe needs root, operator confirmation per toggle, and never runs
as part of `live_tests/`. Do not collapse them — a “natural
extension to actually test the kill switch from inside L-29” would
require root, prompts, and restore in `live_tests/`, breaking the
suite’s read-only contract. The PASS verdict on L-29 explicitly
says it does NOT prove the kill switch actually blocks leaks under
load; do not relax that wording so a green light can be mistaken
for proof of leak blocking. For that proof, run the active probe.

### `feature_flags.set_flag` rejects string values like `"true"`

`set_flag` requires `isinstance(value, bool)`. A “be nicer to
callers” change that coerces `"true"` / `"1"` / `1` to `True`
hides a real bug: a misrouted form value reaching this API as a
string instead of a JSON literal `true` should be a rejection, not
a silent flag flip. Same gate logic as the T25 schema-migrate
three-flag mutation. Pair this with the `delete_flag` idempotency
note below — both shape the surface deliberately.

### `extension/icon{16,48,128}.png` must remain present

The extension manifest references all three icon files in
`action.default_icon`, and `background.js` resolves
`chrome.runtime.getURL("icon128.png")` for its notification call.
Chromium REJECTS the entire extension on load if any referenced icon
is missing — service worker never registers, badge never appears,
the three `test_extension_live.py` tests skip silently and L3 in
`live_tests/checks.py` skips with them. The PNGs shipped in
v3.63.5 are solid `#2C5282` placeholders; replacing with branded
artwork is fine but DELETING is a regression that’s visually
identical to an unrelated headless-Chromium problem (see
LESSONS_LEARNED A1 for the misdiagnosis that survived for over a
year). If the extension’s icon set changes (e.g. a new size
referenced from `manifest.json` or `background.js`), the new file
must be added with it — Chromium’s reject-on-missing-icon
behaviour is strict.

### `channel="chromium"` in extension tests is required

`tests/test_extension_live.py` and `live_tests/checks.py::l3_*` both
launch Playwright with `channel="chromium"`. Playwright’s default
`pw.chromium` binary is the headless-shell
(`/opt/pw-browsers/chromium_headless_shell-NNNN/.../headless_shell`),
which CANNOT host MV3 extension service workers regardless of
`--headless=new`. `channel="chromium"` swaps to the full
Chromium-for-Testing binary
(`/opt/pw-browsers/chromium-NNNN/.../chrome`) that `playwright install chromium` already pulls alongside — they’re TWO binaries,
not one. Removing the channel argument “because it looks redundant”
is a silent regression: the three extension tests start skipping
again with the exact same misleading message (“this Chromium build
may not run extensions in headless”). The harness also waits via
`wait_for_event("serviceworker", timeout=30_000)` rather than a
fixed-duration poll because SW registration latency varies with host
load (see LESSONS_LEARNED A1).

### `requirements-optional.txt` has NO `beeg_api` or `xnxx_api` pin

At v3.63.5 both pins were removed entirely. The adapters for both
sites in `extractors.py` are unchanged and still call
`_try_import("beeg_api" / "xnxx_api")`, which returns `None` and
triggers the fail-open `library_not_installed` path — BD’s generic
teach-based extractor covers both sites without the library. Adding
either pin back without first verifying that the package is on PyPI
and actually maintained will reintroduce the install-time noise (and
potentially a 3rd-party supply-chain risk) that v3.63.5 removed.
The historical comment block in `requirements-optional.txt` records
what was removed and why; a future maintainer should read it before
restoring.

### `install_windows.bat` uses `robocopy /E`, not per-file copies

Pre-v3.63.5 the installer copied an explicit list of files
(`bulk_downloader/`, `downloader_ui.py`, `requirements*.txt`, the
spec, the uninstaller). That list silently missed `tests/`,
`live_tests/`, `extension/`, `tools/`, `fixtures/`, `run_tests.py`,
and the `.bat` test runners — leaving the Windows install target
unable to run its own test suite or load the extension. Reverting to
a per-file list (because robocopy “looks heavier”) brings the bug
back. The
`robocopy /E /XD __pycache__ .git .venv venv build build_tmp dist node_modules /XF *.pyc *.pyo .DS_Store Thumbs.db sites_config.json`
form is the proven-correct pattern that `install_dev.bat` has used
for years; matching it keeps both installers symmetric.

### `gpu_check.sh --install-drivers` has explicit refusal guards

The flag will exit with code 2 BEFORE attempting any apt action if:
(a) `apt-get` is absent (non-Debian-family), (b) `sudo` is absent,
(c) no NVIDIA hardware was detected, or (d) combined with `--json`
(prompts can’t coexist with machine output). Removing any of these
guards because “the script should be robust enough to try anyway”
is wrong — they exist so the script doesn’t run an apt-mutation
against a system whose package manager it doesn’t understand. The
post-install path also prompts for reboot rather than calling
`sudo reboot` directly when the operator hasn’t been asked. Secure
Boot detection (via `mokutil --sb-state`) triggers a MOK enrollment
warning before the install prompt; suppressing that warning under
the assumption “the user knows about Secure Boot” is wrong — the
MOK enrollment step is non-obvious and missing it leaves the
system without a working driver after reboot.

### `gpu_check.bat --install-drivers` is a browser-handoff, not silent execution

There is no winget package that just installs the NVIDIA driver as
of May 2026. `Nvidia.CUDA` 13.x stopped bundling the driver
(microsoft/winget-pkgs #283373); NVIDIA App’s winget package is
blocked on microsoft/winget-pkgs #140696; the Microsoft Store ID
`9NF8H0H7WMLT` is NVIDIA Control Panel (no driver). The Windows
flag offers a two-option prompt: (1) open the NVIDIA App download
page in the default browser, (2) `winget install Nvidia.CUDA` only
if the operator also wants the CUDA toolkit. “Simplifying” this to
a silent `winget install Nvidia.CUDA` reintroduces the
misleading-3GB-install bug we deliberately avoided — an operator
who only wants a driver would get a 3 GB toolkit install that may
not actually deliver one. When microsoft/winget-pkgs #140696
unblocks, the right move is a one-line patch to swap option [1] from
browser-handoff to `winget install`; the rest of the structure
stays the same.

### `bat_lint()` is now content-correct, not just shape-correct (v3.63.8)

At v3.63.8 `dev_suite`'s `bat_lint()` (today
`bulk_downloader/dev_suite/release_lint.py::bat_lint`) was rewritten to
delegate to
`bulk_downloader/_bat_lint.lint_bytes()` — a paren-aware tokenizer
with quote awareness, caret escaping, `^`-line-continuation joining,
REM/`::` comment skipping, and `for ... in (...)` arg recognition.
The prior regex-based detector is gone. The contract test
`test_bat_lint_all_shipped_files_clean` enforces `with_issues == 0`
across every shipped `.bat` (the old `test_bat_lint_shape`
shape-only check is superseded); a second contract test
`test_bat_lint_catches_v3_63_7_bug_class` pins L4 against weakening.
A fresh instance that “simplifies” `bat_lint()` by inlining the old
regex body will (a) lose the L4 detection that catches the v3.63.7
bug class, and (b) break both contract tests. Don’t inline-revert.
The implementation lives in the separate module so it can be
unit-tested without the rest of the suite (27 parser unit tests in
`tests/test_bat_lint_parser.py`). The return-dict shape is preserved
for back-compat.

### `_bat_lint.py` does NOT enforce L5 or L6 — and must not be made to

L5 (labels inside `(...)` blocks are illegal) and L6 (`::` comments
inside `(...)` blocks are illegal) were prototyped during the v3.63.8
work, false-positived against real shipped `.bat` files
(`gpu_check.bat` lines 404/427/434 use labels-inside-blocks
legitimately — cmd.exe permits them when reached via `goto`), and
**removed before integration**. The module’s header comment block
documents the retract decision. A fresh instance “completing” the
lint by adding L5/L6 back will produce false positives on those
exact lines. The decision was to remove the rules, not whitelist
the working corpus — whitelisting a working file from a rule is a
tell that the rule is wrong (see LESSONS_LEARNED C8).

### `bat_lint`’s “unquoted redirection inside a for-loop” is a tolerated false positive on `2^>nul`

Six of the .bat files in v3.63.5 (`install_windows.bat`,
`install_ai_ollama.bat`, `uninstall_windows.bat`, `gpu_check.bat`,
`tools/dast.bat`, `tools/sast.bat`) flag the lint at lines
containing `2^>nul` — the caret escapes the `>` and cmd.exe sees a
literal `>` as a command argument, NOT a redirection. The lint’s
regex doesn’t understand caret escapes; the false positive is
accepted across the codebase. Verify the flagged line contains
`^>` or `^<` before treating any individual flag as real. Do NOT
rewrite working `2^>nul` constructs to “silence the lint.”

### `install_ai_ollama.sh` step [1] delegates to `gpu_check.sh`

The historical inline 17-line NVIDIA-only check was replaced
(v3.63.5) with a single `"$SCRIPT_DIR/gpu_check.sh"` call, with a
`nvidia-smi`-only fallback if `gpu_check.sh` isn’t present
alongside. The delegation is intentional — one detector that both
scripts share keeps the matrix consistent (NVIDIA/AMD/Intel/Apple
coverage, JSON output, recommendation tiering). “Restoring” the
inline check because it’s simpler reintroduces the divergence and
loses AMD/Intel coverage; AMD users would only see CPU-fallback
warnings, not the chip-by-chip ROCm verdict.

### `capture.sh` step [3] uses `from bulk_downloader.app import app`

The CSRF diagnostic block imports the module-level Flask `app`
directly, NOT via a `create_app()` factory. BD has no `create_app()`
factory; the module-level instance IS the contract. The earlier
`capture.sh` version (pre-v3.63.5) tried to call `create_app()` and
ImportErrored on every capture for years. “Modernizing” the diag
block to a factory pattern would break it again. The same import
shape is used in `tests/test_dev_suite_tier0.py` and elsewhere — it
is the canonical way to get a handle on the Flask app from outside
the package. **Enforced by**: `tests/test_u45_capture_sh_shipped.py:: test_step_3_does_not_execute_create_app` — filters comment lines
before grepping (executable mentions only). `capture.sh`’s header
deliberately mentions `create_app` in its comment block to document
why the symbol was removed — that history has value; do NOT strip
the comment to “clean up” the file.

### `capture.sh` step [6] includes L3 in its `LIVE_IDS`

Pre-v3.63.5 captures excluded L3 from the `--only` list because the
L3 live test wedged on the headless-Chromium issue (the same false
diagnosis that affected the unit tests). The v3.63.5 channel +
event-wait fixes resolve the actual cause; L3 PASSes against a
capable host. If a future capture shows L3 failing again, the right
move is to diagnose the underlying extension/Chromium issue (rerun
`chrome --enable-logging=stderr`), NOT to re-remove L3 from
`LIVE_IDS`. The capture header also documents which live tests are
in scope; updating the header alone without the `LIVE_IDS` list is
a partial change that silently drops coverage. **Enforced by**:
`tests/test_u45_capture_sh_shipped.py::test_live_ids_contains_l3`.

### `capture.sh` step [6] `--per-check-timeout` ≥ 90

T55’s per-check harness wall defaults to 60s. L34 streaming-skip
work (v3.63.4) bounds the wedge but the per-check budget still has
to exceed the wedge’s natural runtime; the 90s budget gives L34
fair room before T55’s wall fires. Tightening below 90s
re-introduces the fail-on-budget pattern v3.63.4 was added to
prevent. **Enforced by**:
`tests/test_u45_capture_sh_shipped.py::test_live_tests_per_check_timeout_at_least_90`.

### `tools/vpn_kill_switch_probe.py::apply_rule` only speaks ufw

ufw is a frontend over `iptables-nft`/`nftables` on Ubuntu 24.04 and
regenerates its chains on every `ufw reload` or interface change.
Direct `iptables -D ...` or `nft delete ...` calls **race with
ufw’s regeneration** — the rule disappears the next time ufw
reloads. The probe must manipulate firewall state exclusively via
`ufw` subcommands (`ufw allow`, `ufw deny`, `ufw default`, `ufw status numbered`, `ufw show added`, `ufw --force reset`). A
well-meaning “speed it up by going to iptables directly” refactor
is a real foot-gun. The operator’s stash specifically runs ufw on
top of `iptables-nft` — confirmed during T46 design. Distinct from
the v3.63.0 read-only L-29 inspector — the active probe lives in
`tools/`, the inspector lives in `live_tests/checks.py`; the
inspector’s PASS verdict does NOT prove the kill switch actually
blocks under load (see the L-29 invariant above).

### `tools/vpn_kill_switch_probe.py::detect_vpn_interface` ambiguity refusal

When *both* `wg*` and `tun*` are UP simultaneously, the function
returns `(None, None, "ambiguous: ...")` and the probe refuses to
run. This is **deliberate**, not a “we couldn’t decide” bug. The
operator runs multi-provider (WireGuard + OpenVPN) but never both
at once. Both up means the routing state is genuinely ambiguous —
which interface “is” the VPN, and which one should the kill switch
pin? Refusing is the correct posture; auto-picking one would lock
the operator into the wrong tunnel. A “just pick the first one”
patch would be a regression. **Enforced by**:
`tests/test_u46_vpn_kill_switch_probe.py::test_detect_vpn_both_up_is_ambiguous`.

### `tools/vpn_kill_switch_probe.py::run_apply` restore is in a `finally`

The mutating path wraps the toggle loop in `try` / `except KeyboardInterrupt` / `except Exception`, each branch routing to
`restore_and_log`. The restore must run **even on uncaught
exception, Ctrl-C, or `sys.exit()`** — otherwise the host can be
left with a kill-switch rule that blocks legitimate traffic
(operator’s network breaks until they get console access). A
refactor that “simplifies” by removing one of the except arms, or
moves restore out of the finally, is a real outage risk. A restore
that itself fails is exit code 4 with an OPERATOR ACTION REQUIRED
message; that exit code is itself part of the contract.

### `app_widgets_api.py` reads `app.s_cfg` via dynamic `importlib.import_module`

The disk_free, cookies_oldest, and diagnostics collectors all read
`importlib.import_module("bulk_downloader.app").s_cfg`. **This
must stay a dynamic import**, not a top-level `from bulk_downloader.app import s_cfg`. A top-level import creates a
circular dependency because `bulk_downloader/app.py` imports
`app_widgets_api` to register the widget routes. Pre-existing
pattern but it’s load-bearing for the v3.63.6 new collectors too
(disk_free fix, cookies_oldest backfill). A “clean up dynamic
imports” pass would break the module at import time.

### `disk_free_gb(path)` requires a positional arg; no default

The function signature in `bulk_downloader/detect.py` is `def disk_free_gb(path):` — no default. The body handles empty string
by falling back to `Path.home()`. **Calling `disk_free_gb()` with
no argument raises `TypeError`** — pre-v3.63.6 the collector did
exactly that and a bare `except Exception: pass` hid the failure
for years, so the `disk_free` widget always rendered `'—'`. The
empty-string fallback is the intended “I don’t know which path”
handle; bare-call is not. **Enforced by**:
`tests/test_u48_widgets_audit.py::test_disk_free_gb_called_with_path_argument`
and the disk-free outer except logs to stderr (no more silent
bare-pass — matched to every other collector’s pattern).

### `widgets_config.VALID_WIDGET_IDS` and `widgets.js` widget list must lockstep

`tests/test_contracts.py::test_contract_widget_catalog_python_js_lockstep`
verifies the two sets match. As of v3.63.6 the set is **36 IDs**
(`gpu` was added in v3.63.6, taking it from 35 to 36). Adding or
removing a widget is a both-sides change. A future cleanup that
strips one side (e.g. “we don’t use GPU on this deploy, drop it”)
is caught at the gate, not at the edit — keep changes symmetric.

### `workers_total` is `None` (key absent) on empty fleet — never the magic 16

Pre-v3.63.6 `app_widgets_api.py` computed `out["workers_total"] = total_cap or 16` where `total_cap` is the sum of every runner’s
`max_concurrent`. On a fresh install `total_cap` is 0, the `or 16`
fired, and the widget rendered `"0/16 workers"` — synthesizing a
16-slot fleet that didn’t exist. The 16 was arbitrary; it didn’t
reflect any actual configured capacity. v3.63.6 removed the
fallback: `workers_total` is now `None` when no fleet is
configured, and the renderer null-guards to `'—'`. A future audit
that finds no test asserting `workers_total > 0` and adds `if workers_total <= 0: workers_total = 16` (reasoning that the
dashboard “looks broken with 0”) brings the lie back. It won’t
look broken — `'—'` is honest.

**v3.65.0 V2.1 widened scope.** The invariant now applies to BOTH
`/api/widgets/data` AND `/api/dashboard/v2`. The shared helper
`app_widgets_api.py::compute_worker_counts(runners) -> dict` is the
single source of truth — it returns
`{"workers_active": int, "workers_total": int | None}` with
`workers_total = total_cap if total_cap > 0 else None`. Both
endpoints call it: `app_widgets_api.py` consumes the dict for the
widget payload, and `app.py`'s `/api/dashboard/v2` handler reads the
same dict for the SPA Home status line. The SPA's `DashboardV2`
type extends both fields; the status line reads new fields and
falls back to `"{N} workers active"` when `workers_total` is
null/undefined. **`active_workers` (back-compat field) is preserved
on `/api/dashboard/v2`** so pre-V2.1 SPA builds still render. If
you add a third consumer of worker counts, route it through
`compute_worker_counts` — don't reimplement the `if total_cap > 0`
check inline (the v3.63.6 lesson generalizes: the fallback bug
re-emerges anywhere the inline form gets copied).

**Enforced by**: `tests/test_u49_workers_total_honest.py::test_no_magic_16_fallback_in_workers_total`
(legacy `/api/widgets/data` path) plus
`tests/test_d3_u2_v2_endpoints.py` (V2.1 widening — pins
`workers_active` and `workers_total` on `/api/dashboard/v2`,
including the `None` empty-fleet case).

### `eta_clear_fmt` is absent (not `'∞'`, not `'never'`) when `files_hour` is 0

`'never'` is a UI lie (throughput could resume any second);
`'∞'` is technically correct but unreadable to most users. The
current behavior is key absent → renderer shows `'—'`. When
`queue_depth` is 0 the key emits the literal string `"now"`.
**Enforced by**: `tests/test_u50_widget_backfills.py::test_eta_clear_absent_when_no_throughput`
plus `test_eta_clear_*` (3 contracts).

### `cookies_oldest` picks MAX age, not min

The collector reuses `doctor.cookie_freshness` and reports the
**stalest** site so the dashboard surfaces an action item, not the
freshest. “Fixing” to min would invert the semantics. **Enforced
by**: `tests/test_u50_widget_backfills.py::test_cookies_oldest_picks_max_age_site`.

### `app_widgets_api.py::_collect_data` returns partial data on collector exceptions

Each collector block is `try / except Exception as e: sys.stderr.write(...)`. A failing collector logs and the function
**continues** — the response contains whatever earlier collectors
succeeded plus an absent key for the failed one. This is the A1
fail-open principle: 500-ing `/api/widgets/data` would break the
whole dashboard over a partial-data condition. The renderer null-
guards downstream. A “this should be fail-fast” refactor would
degrade the user experience. Also: bare `except Exception: pass`
(no `as e`, no stderr write) is forbidden in collectors — the
v3.63.6 disk_free bug hid behind exactly that pattern for years.
Every collector must follow the canonical `except Exception as e: sys.stderr.write(f"[widgets-api] X failed: {e}\n")` shape so the
next regression is diagnosable in one grep of `journalctl`.

### `widgets.js::pct(v)` returns 0 (not `'—'`) for null input — null-guard at the caller

`function pct(v) { if (v == null) return 0; return Math.round(Number(v)); }`
— the helper is used in `{ meter: pct(d.foo), ... }` slots where
the meter API needs a number (0 = empty bar). Changing `pct()` to
return `'—'` would inject a string into the meter API and break
meters across multiple widgets. The right fix for “show no data,
not zero” is at the caller — `d.foo != null ? \`${d.foo}%` : ‘—’`— which is what the`cpu_ram`, `workers`, and `gpu`renderers do in v3.63.6 (matching the pre-existing pattern in`lib_watched_pct`, `success_rate_24h`, etc.). **Enforced by**: `tests/test_u47_widgets_data_psutil.py::test_cpu_ram_render_null_guards_*`and`tests/test_u49_workers_total_honest.py::test_workers_renderer_null_guards`.

### `_collect_data` emits some keys as explicit `None`, others as absent — both are correct

Both shapes appear in the response. Keys emitted as explicit `None`
(e.g. `cpu_pct: null` when psutil returns null) and keys not
emitted at all (e.g. `gpu_util_pct` when nvidia-smi is missing)
look identical to a JS `d.foo != null` check. **Don’t normalize**
— the current shape is correct in each case (`None` is “we ran
the collector and got nothing”; absent is “we didn’t run the
collector at all”). Both behave correctly in the frontend.

### `nvidia-smi --query-gpu` output has leading whitespace padding per field

The v3.63.6 GPU collector queries
`utilization.gpu,memory.used,memory.total,name` with
`--format=csv,noheader,nounits`. The output is comma-separated
with **leading whitespace padding** in each field — e.g.
`" 5, 1024, 15360, Tesla T4"`, not `"5,1024,15360,Tesla T4"`.
The parser strips per-field with `p.strip()` before `int()`. A
future “speed up the parser by dropping the strip” would crash
on real nvidia-smi output. **Enforced by**:
`tests/test_u48_widgets_audit.py::test_gpu_collector_parses_nvidia_smi_output`.
GPU collector also fails open on missing binary, non-zero exit,
or malformed output — all three covered by contracts.

### `psutil` is pinned in `requirements.txt`, NOT `requirements-optional.txt`

v3.63.6 caught `psutil` documented as optional in `doctor.py` but
never pinned anywhere — the `cpu_ram` widget had displayed
misleading `"0% / 0%"` on every deployment for years because
`psutil` wasn’t installed and the frontend `pct(null)` returned
0. Now pinned `psutil>=5.9,<7.0` in `requirements.txt`. A future
cleanup that “moves it back to optional” reintroduces the bug.
The rule: a dependency referenced by shipping code whose *primary
value* depends on it belongs in `requirements.txt`, not optional.
“Soft import that gracefully degrades” is the shape that goes in
optional; “soft import that silently leaves the feature broken”
is the shape that goes in main. **Enforced by**:
`tests/test_u47_widgets_data_psutil.py::test_psutil_pinned_in_requirements_txt`.

### `psutil.cpu_percent(interval=None)` returns 0.0 on first call — keep it None

The widget collector calls `psutil.cpu_percent(interval=None)`,
which returns the percentage since the *previous* call. The first
call after process start returns 0.0. This is correct, not a bug —
the widget refreshes every few seconds, so calls 2..N return real
numbers. A “use `interval=1` for accuracy” patch blocks
`/api/widgets/data` for a full second per request, which is bad
for a polling dashboard. The current pattern is intentional.

### `run_tests.py --workers` auto-detect caps at 60 on Windows (v3.63.7)

`ProcessPoolExecutor` enforces a hard limit of 61 workers on
Windows because `WaitForMultipleObjects` caps at 64 handles minus 3
reserved. On a 100+ logical-CPU VM the un-capped auto-detect raised
`ValueError: max_workers must be <= 61` at pool construction. The
cap is **60, not 61**, to leave a one-handle safety margin —
removing this margin is a real failure mode at full saturation,
not paranoia. The cap is only applied when auto-detecting; manual
`--workers=N` still respects N (the operator is opting in). A fresh
instance “simplifying” the platform branch by removing the cap
will fail every Windows full-suite run on the verification VM.

### `_PINNED_TOGETHER` runs its members serially -- two reasons, not one

The set lives in `run_tests_core.py` (line ~1095; `run_tests.py` is a
12-line shim, so do not look for it there). Re-derive the membership
before relying on it -- `grep -n "_PINNED_TOGETHER" run_tests_core.py`.
As measured at v3.66.818 it carries FOUR entries:
`{"test_fixture_site.py", "test_fixture_site2.py",
"test_v3_66_729_body_contract_fixtures.py",
"test_v3_66_13_phase2_p2_snapshot_replay.py"}`.

The set is NOT "fixed-port collisions only" -- that was true when it
held two entries, and stopped being true when the flake pins landed:

- `test_fixture_site.py` / `test_fixture_site2.py` avoid fixed-port
  collisions (8899 / 8898): the fixture sites listen on a known port
  for cross-test harness coordination, and two parallel workers would
  clash. Removing either resurfaces port-bind EADDRINUSE failures
  under `--workers`.
- `test_v3_66_729_body_contract_fixtures.py` and
  `test_v3_66_13_phase2_p2_snapshot_replay.py` are flake/isolation
  pins added at v3.66.754 and v3.66.772, pinned by
  `tests/test_v3_66_754_flake729_serial_pin.py` and
  `tests/test_v3_66_772_flake_hardening.py`.

Do not drop an entry as "unexplained" because it has no fixed port --
check the pinning test for that entry first. Membership grows; treat
any count in this document as stale by default.

**Historical context — the retired third pin.** Pre-v3.65.0 the set
also carried `test_v3_43_55_csrf_bootstrap.py`. It papered over a
Windows + fresh-`BD_HOME` `/` asymmetry (OPEN_THREADS D2): under
`--workers` the parallel path gives each test file a fresh `BD_HOME`
tempdir, and on Windows the app's first-boot init under that fresh
BD_HOME was thought to produce a `/` response whose CSRF meta tag
the three CSRF tests couldn't match. The v3.65.0 B1 cycle finally
ran `tools/diag_d2_fresh_bd_home.py` on a Windows VM and confirmed
Path A (templated render) and Path B (static fallback) produce
**structurally identical** `/` responses: same status, length,
template SHA, csrf meta, `bd_session` cookie. The asymmetry had
resolved naturally somewhere in the v3.64.x deploy chain. Pin
retired. **Don't restore the pin** without a fresh diag run showing
the asymmetry has returned — adding a pin to silence a Windows
failure without diag is the v3.63.2 anti-pattern (theory-driven fix
on an environment you can't observe; see LESSONS_LEARNED A1).

### `install_remote_teach.sh` uses `Wants=` not `Requires=` for `bd-xvfb` (v3.63.8)

The drop-in at
`/etc/systemd/system/bulkdownloader.service.d/10-display.conf`
specifies `Wants=bd-xvfb.service`, not `Requires=`. This is
intentional: if Xvfb is down, `bulkdownloader` still boots and
serves; only manual-teach degrades. A “fix” that strengthens the
dependency to `Requires=` will make the entire app fail to start
whenever Xvfb fails to start — a far worse failure mode than the
designed graceful degradation. The whole remote-teach stack is
explicitly secondary infrastructure; primary service availability
must not depend on it.

### `install_remote_teach.sh` writes four separate static unit files (v3.63.8)

Not a systemd template (`bd-display@.service`). The four units
(`bd-xvfb`, `bd-openbox`, `bd-x11vnc`, `bd-novnc`) are written as
four separate static unit files. Templates would let the operator
instantiate multiple displays, but only one is needed and the
static units are simpler to introspect with `systemctl status`. A
“cleanup” to template form adds complexity without benefit.

### `install_remote_teach.sh` enforces LAN-only trust by binding layers

`x11vnc` binds to `127.0.0.1:5900` (loopback only) and only
`websockify` on `0.0.0.0:6080` is LAN-facing — the VNC password
guards the LAN-facing surface and there is no plaintext VNC port
on the network. A “simplification” that binds `x11vnc` to
`0.0.0.0` directly exposes the raw VNC protocol to the LAN with
only password auth and is explicitly NOT what this stack is for.
The trust model matches port-5555 (LAN-trusted single-operator);
the docs explicitly state it is NOT for internet exposure.

### `/m/ops` CSRF substitution uses `str.replace(..., 1)` — the `1` is load-bearing (v3.63.9)

The `/m/ops` route handler in `app.py` mirrors the index handler:
mints a session inline if no cookie is present, substitutes the
CSRF token into the template’s `__CSRF_TOKEN__` placeholder via
`str.replace(template_html, "__CSRF_TOKEN__", token, 1)`. The `1`
is **load-bearing**: the placeholder appears a SECOND time inside
the page’s JavaScript as a sentinel:

```js
if (CSRF === "__CSRF_TOKEN__" || !CSRF) CSRF = "";
```

This is the **un-substituted-template detector**. If the
server-side replace is changed to substitute ALL occurrences of
the placeholder (e.g. `.replace(..., -1)` or python’s default
global replace via plain `.replace()` without a count), this
sentinel breaks — the JS will never detect that the meta tag was
un-substituted on a raw render, and POSTs will fail silently on
that path. **Correct handling:** keep `replace(..., 1)`. If
extending the pattern to other templates, follow the same shape:
server replaces the first occurrence only, JS sentinel checks
the placeholder by value.

### L37’s WARN-on-mismatch is by design (v3.63.9)

`live_tests/checks.py::l37_deployed_version_coherent` compares
`tools/deployed_version.txt` (what was last started) vs
`bulk_downloader/__init__.py::__version__` (what’s on disk) vs
`/api/health.version` (what’s running). The verdict logic in
`checks.py`:

- PASS — all three sources readable and agree
- WARN — partial readability with the readable ones agreeing, OR
  genuine mismatch between any two
- FAIL — ONLY when no source is readable at all (the edge case)

WARN-not-FAIL on mismatch is **deliberate**. A version mismatch
means the system is incoherent (files landed but the service was not
restarted -- the box deploys with `git fetch origin main` +
`git reset --hard origin/main` + restart, and moving the files does
NOT restart the service; a half-done deploy; stale `__pycache__`
`.pyc` that `git reset --hard` does not clear) but
the system is still SERVING. A release-gate test should not block
on operator-side post-deploy bookkeeping. **Correct handling:**
do not “fix” L37 by promoting WARN to FAIL on mismatch. If a hard
gate is wanted, build a SEPARATE check that runs only on the
operator’s machine post-deploy — not in the live-test suite. The
catalog (`LIVE_TESTS_CATALOG.md`) documents the same contract.

### `KB last merged:` stamp updates ONLY in dedicated merge sessions (v3.63.9)

`PROJECT_STATE.md` line 8 holds `KB last merged: vX.Y.Z / YYYY-MM-DD`.
The stamp value will be one or two versions behind current code
between merges. **This is correct, not a bug.** Updating the stamp
inside a feature/bug session would lie about when the consolidated
KB caught up to the code, defeating the stamp’s purpose entirely.
`KB_WORKFLOW.md`’s merge appendix has explicit instruction: the
stamp is updated ONLY during dedicated merge sessions. **Correct
handling:** never edit the stamp outside a dedicated merge
session — not even when correcting drift in other parts of
PROJECT_STATE (e.g. the retired-diagnostics list) as part of a
PT-series merge-prep pass. The drift-correction may land; the
stamp does not move.

### `tools/build_release.py --skip-tests` is for builder debugging only (v3.63.9)

The flag exists so the builder can be iterated on without paying
the ~5-minute full-suite cost. On an actual release cut, the rule
from the operating instructions overrides: “Run the full suite
from the EXTRACTED release zip, not just the source tree, before
calling a release done.” **Correct handling:** for an actual
release the gate is **source-tree suite + extracted-zip suite,
both `Failed: 0`**. `--skip-tests` is acceptable only for
debugging the builder itself. The builder’s own manifest verifier
(file count, sizes, deterministic 2020-01-01 mtimes) catches zip
drift; it does NOT catch behavioral regressions introduced
post-build. Both gates run.

### `install_windows.bat` Python detection has TWO paths — both load-bearing (v3.63.9, D1)

The D1 fix added an explicit `py -3.12` lead, then fell back to
the historical `python3 / python / py` scan with a “not the
documented target” note logged on the fallback. Two
consequences are deliberate:

1. On a host with both 3.12 and 3.14, the install uses 3.12 even
   if 3.14 is first on PATH. The documented target wins when
   present.
2. On a host with only 3.14 (the v3.63.7 verification VM’s
   situation), the fallback path runs and the install proceeds on
   3.14. 3.14 is supported per the 4414/4414 result.

**Correct handling:** do not “simplify” by removing either path.
The `py -3.12` lead is the D1 fix; the fallback is the
backward-compat path for hosts that don’t have 3.12. Pinning
hard to 3.12 (removing the fallback) would reject a working
greenfield install on a fresh Windows machine.

### `m_ops.html` design tokens are intentionally INLINE, not extracted (v3.63.9, D3 wedge 3 deferral)

Tokens (color/type/spacing/radius) live inline in
`bulk_downloader/templates/m_ops.html` as CSS custom properties.
They are NOT in a shared `tokens.css`. This is deliberate:
extracting tokens that only one template uses is busywork —
“shared” infrastructure with one consumer is moving CSS around.
`UI_TOKENS.md` at the repo root documents the canonical token
names so the next adopter doesn’t re-invent them. **Correct
handling:** when a SECOND template adopts the mockup palette,
that is the trigger to extract. Until then the inline declaration
is correct. See LESSONS_LEARNED E31.

### `pytest` is in `requirements-dev.txt` and IS REQUIRED (re-stated v3.63.9)

The runner is custom and does not LAUNCH pytest. But test files
import the `pytest` library directly for `@pytest.fixture(autouse=True)`,
`@parametrize`, `pytest.raises`, `pytest.fail`, `pytest.skip`,
`pytest.mark.skipif`, `pytest.mark.skip`. The runner **expands**
these decorators itself by importing the pytest module and
reading the decorator’s attached metadata. v3.63.9’s PT9
verification surfaced that the expansion was incomplete for some
of these (now fixed), but the imports remain real and required.
20+ test files import pytest. A prior KB iteration claimed
“pytest is unused” and “harmless to remove” — that claim is
factually wrong and a near-miss happened on it (see
LESSONS_LEARNED E23). **Correct handling:** never remove `pytest`
from `requirements-dev.txt`. “The runner is custom” and “pytest
is unused” are two different statements; do not collapse them.

### `tests/SKIP_BASELINE.txt` placeholder must be initialized post-deploy (v3.63.9, PT9)

The shipped baseline is `0` — a placeholder. The gate either
passes trivially (current Skipped count is 0, matching the
placeholder) or fails noisily (Skipped > 0 → investigate). On
first deploy after v3.63.9 the operator runs
`venv/bin/python tools/check_skip_baseline.py --update` to record the
real number. **Correct handling:** never bump the baseline
silently inside a feature session. Any uptick in Skipped count
should be investigated before raising the baseline; the
`--update` flag is operator-acknowledged, not automatic. A pull
that bumps the baseline without a matching investigation is a
regression hiding behind a process action.

### `runner.py` curl_cffi downloads must use `request(stream=True)`, NOT module-level `stream()` (v3.63.10)

Two call sites in `bulk_downloader/runner.py` (single-stream HTTP
downloader at L~9128 and parallel-chunk downloader at L~9574) use:

```python
cffi_requests.request("GET", url, stream=True, ...)
```

A fresh instance reading curl_cffi docs may “simplify” this to
`cffi_requests.stream("GET", url, ...)` because `Session.stream()`
exists and the symmetry looks natural. **curl_cffi 0.15.x has NO
module-level `stream` function.** Only:

- `cffi_requests.request(...)` with `stream=True` (module level)
- `Session.stream(...)` (instance method on a Session)

Reverting to the bug shape raises `AttributeError: module 'curl_cffi.requests' has no attribute 'stream'` on every direct
HTTP download, causing silent fallback to the browser path (~5-10x
slower, triggers the 15-min worker-hung watchdog under load). The
v3.63.9 release shipped with this bug; v3.63.10 fixed both sites
and pinned them. **Correct handling:** the call shape is
`cffi_requests.request("GET", url, stream=True, ...)`, not
`.stream(...)`. **Pinned by:**
`tests/test_curl_cffi_api.py::test_runner_does_not_call_module_level_cffi_stream`
(grep against the bug shape) and
`test_curl_cffi_module_has_request_function` (runtime API surface
check, skipped via `pytest.importorskip("curl_cffi")` when the
library isn’t installed).

### `tools/check_skip_baseline.py` must accept BOTH parser shapes (v3.63.10, PT9 fix)

The parser deliberately accepts two formats:

- `N skipped` (lowercase, count BEFORE the word — what
  `SUMMARY.txt` contains, written by `run_tests_core.py`)
- `Skipped: N` (capital + colon — what the runner prints to
  stdout, also from `run_tests_core.py`)

(Both writers live in `run_tests_core.py`, not `run_tests.py` -- the
latter is a 12-line shim. Grep `skipped` in `run_tests_core.py`;
the line numbers move.)

A fresh instance may “simplify” this to one regex thinking the
other is unreachable. **Both are reachable.** SUMMARY.txt
exclusively uses the lowercase form; stdout-captured logs may
contain the capital form; either may be the input to `--update`
depending on how it’s invoked. The v3.63.9 PT9 ship had the
capital-only parser and every `--update` against SUMMARY.txt
failed. **Pinned by:**
`tests/test_skip_baseline.py::test_parser_reads_real_runner_summary`
(round-trip pin reproducing the runner’s exact SUMMARY.txt format)
AND `test_parser_falls_back_to_label_colon_shape` (the
runner-stdout shape).

### `install_windows.bat` L162 must use UNQUOTED `%PYTHON_CMD%` (v3.63.10)

```bat
%PYTHON_CMD% -m venv "%VENV_DIR%"
```

A fresh instance applying “quote all expansions” as a generic
robustness pass would change this to `"%PYTHON_CMD%" -m venv ...`.
That **breaks every Windows install that uses the py launcher**
(D1’s documented configuration: `set "PYTHON_CMD=py -3.12"`).
cmd.exe treats `"py -3.12"` as one executable name and fails at
step [3/7] with `'"py -3.12"' is not recognized`. Safe because
`PYTHON_CMD`’s value is always a launcher invocation or single
command name, never a path containing spaces. **`_bat_lint` does
NOT cover quoting bugs and that is intentional** per the v3.63.7
rule-inflation lesson — pin the file instead. **Pinned by:**
`tests/test_install_windows_bat_d1.py` (4 tests:
`test_install_windows_bat_venv_invocation_unquoted`,
`test_install_windows_bat_no_other_quoted_pythoncmd_invocations`,
`test_install_windows_bat_python_cmd_assignment_present`, plus
one shape pin on the D1 assignment).

### D3 SPA invariants (v3.64.0)

The following invariants govern the `/m2` React SPA shipped at
v3.64.0. None overlap with pre-existing entries — the D3 surface is
its own subsystem and these are its load-bearing rules.

### `bd_ui` cookie name is the contract — do not rename

`bulk_downloader/app.py` defines `_M2_COOKIE_NAME = "bd_ui"` and
`D3_OPT_IN.md` references that exact string. Renaming the constant
(`bd_ui_pref`, `ui_choice`, etc.) silently invalidates every opt-in
cookie set by v3.64.0 — operators who opted in will be back to the
legacy `/m` until they visit `?ui=v2` again. The constant is also
the contract with the SPA frontend, which may read the cookie to
show “you are on v2” affordances. Pinned by
`test_d3_u9_cookie_name_is_bd_ui`. Renaming requires (a) a migration
that accepts both names for one release, and (b) updating
`D3_OPT_IN.md` + the test.

### `bd_ui` cookie MUST NOT be `HttpOnly`

The cookie is deliberately readable by JavaScript. The frontend may
want to read it to show “you are on v2” UX. Setting HttpOnly silently
breaks any such future feature. Pinned by
`test_d3_u9_cookie_is_not_httponly`. If you find yourself
“hardening” the cookie, stop — it is a UI preference, not a security
boundary. `bd_session` (the CSRF-bound auth cookie) is separate and
HttpOnly correctly.

### `_m2_opt_state` checks query BEFORE cookie

The order in `_m2_opt_state(req)` is: query-string `?ui=` first,
cookie `bd_ui` second. Inverting this would make the URL-driven
explicit choice a no-op for users who already have a cookie set —
they’d be unable to flip back via the URL. This is exactly the
muscle-memory escape hatch the operator relies on after a bad update.
Do not “optimize” by checking the cookie first.

### `/m2` direct access ignores stale v1 cookie

A user with `bd_ui=v1` who types `/m2/` directly into the URL bar
still gets the SPA. The cookie expresses default-route preference,
not a hard ban on `/m2`. The opt-out redirect in `serve_m2_spa` only
fires on EXPLICIT `?ui=v1` in the query string, never on a stale
cookie alone. This is intentional — inverting it would make `/m2`
unreachable for users who once opted out, which is wrong. Pinned by
`test_d3_u9_m2_serves_spa_with_stale_v1_cookie`.

### `_m2_avatar_color` djb2 determinism is a UI contract

`bulk_downloader/app.py :: _m2_avatar_color(name)` hashes the site
name via djb2 and modulos to one of 12 hues. The frontend uses the
returned color directly for `SiteAvatar` rendering. If the hash
function or the palette size changes, every site’s avatar color
changes — users who learned to recognize their sites by color will
be confused. A future migration to a prettier palette must include a
deterministic mapping from old hue → new hue per slot. Not pinned by
a test; treat as a contract.

### `useQueueStream` reuses the `queue-v2-badge` query key

The hook in `frontend/src/hooks/useQueueStream.ts` does NOT open its
own poll — it reads the cache populated by AppShell’s
`useQuery<QueueV2>({ queryKey: ["queue-v2-badge"], ... })`. Drift in
either side opens a second pipe to `/api/queue/v2` at 15s cadence
for the same data. Pinned by
`test_d3_u8_completion_sound_reuses_queue_query`. Same pattern
applies to `useCompletionSound` for the same reason.

### `applyStoredThemeOnBoot()` MUST be called from `main.tsx` pre-render

`frontend/src/hooks/useTheme.ts` exports `applyStoredThemeOnBoot()`
as a synchronous helper that reads `localStorage["bd-theme"]` and
toggles the `dark` class on `<html>`. `frontend/src/main.tsx` MUST
call it before `ReactDOM.createRoot` — otherwise the first paint is
in the light/default palette and the user sees a flash before React
mounts and `useTheme` applies the dark class via useEffect. Pinned
by `test_d3_u7_main_tsx_calls_boot_helper` and
`test_d3_u7_use_theme_exports_boot_helper`. Same pattern applies to
any future client-state-on-boot need (language preference, sidebar
collapsed/expanded, etc.).

### `_m2_activity_query_fragments` is the shared SQL contract

`bulk_downloader/app.py :: _m2_activity_query_fragments(window_days, q)` returns `(where_clauses, params)` and is used by BOTH
`/api/activity/v2` (JSON) and `/api/activity/v2/export.csv`. Both
endpoints must apply the same filters for “showing X of Y” + “export
the same Y” to be coherent. Reimplementing the filter inline in
either endpoint loses this guarantee. Pinned by
`test_d3_u6_query_fragments_helper_present`.

### `_m2_opt_state` and `_m2_apply_opt_cookie` helpers must exist by name

`test_d3_u9_opt_state_helper_present` and
`test_d3_u9_opt_cookie_helper_present` import them by name. Renaming
or inlining requires a corresponding test update; the named-import
contract exists so the next session can reason about the opt-in
surface without re-grepping `app.py`.

### `bd_ui` cookie TTL must be ≥ 30 days; documented value is 1 year

`_M2_COOKIE_TTL = 365 * 24 * 3600`. `test_d3_u9_cookie_ttl_is_one_year`
is forgiving (≥ 30 days) but the documented value is 1 year.
Drifting to “session” would break the UX (operator’s choice doesn’t
survive a browser restart).

### All 5 SPA route error cards must carry `role="alert"`

`test_d3_u8_error_cards_in_routes_have_role_alert` walks
`routes/Home.tsx`, `routes/Sites.tsx`, `routes/Queue.tsx`,
`routes/Activity.tsx`, `routes/Advanced.tsx` and requires
`role="alert"` near any `border-red bg-red-soft` Card. Adding a 6th
route with an error card must include the role. Note: there is no
shadcn `alert.tsx` primitive in this tree — `role="alert"` is an
attribute on plain Card JSX. Do not add a primitive expecting the
test to pass against an imported component.

### v3.64.2 + v3.64.3 invariants

The following invariants govern the SPA + session_keeper +
release-builder surface as of v3.64.3. None overlap with existing
entries — they are additions for the new subsystems.

### `session_keeper` uses `launch_persistent_context`, not `launch` + `new_context` (v3.64.2)

`bulk_downloader/session_keeper.py:514` calls
`self._pw.chromium.launch_persistent_context(user_data_dir=…)`.
After v3.64.2, `self._browser` is `None` (the persistent-context
API owns the browser process implicitly). A naive "consolidate"
that reintroduces `launch() + new_context()` re-opens the latent
that shipped from v3.43.15 to v3.64.1: rotated session cookies
are lost every 24h browser-age restart, on crash, and on takeover
release. First-launch-only DB-cookie seeding bootstraps existing
installs into the persistent profile. The 24h restart still
triggers but now preserves cookies through the profile directory.
Don't drop the persistence; don't drop the first-launch-only
seeding either (it's how existing installs avoid an empty profile
on first v3.64.2 boot).

### `session_keep_alive_*_min` config keys read from `global_config` on every iteration (v3.64.2)

`bulk_downloader/session_keeper.py:112-148` exposes three
intervals as user-tunable via `app_config.json`:
`session_keep_alive_fetch_interval_min` (default 30),
`session_keep_alive_navigate_interval_min` (default 30),
`session_keep_alive_lead_time_min` (default 30). All read via
`global_config` on every scheduler iteration — operator can
change them in Settings without restarting the service.
Module-level `FETCH_HEARTBEAT_INTERVAL_SEC = 30 * 60` and the
matching constants remain as fallbacks when the config key is
unset or invalid. Do not delete the module-level constants
("the config covers it") — invalid input falls through to them.
Do not move the read out of the iteration loop into module scope
("cache it") — that breaks live-tuning. Range clamps 1-360
minutes happen at the helper layer; out-of-range values silently
clamp.

### `useMediaQuery("(min-width: 1024px)")` is the desktop-shell breakpoint (v3.64.2)

`frontend/src/components/AppShell.tsx:82` branches the entire
chrome render: at ≥1024px it returns `<DesktopShell>` (sidebar +
top utility bar + max-w-7xl content); below it renders the mobile
chrome (`PageHeader + BottomTabBar + max-w-2xl`). Changing the
breakpoint changes who sees which shell. `CommandPalette` is
mounted in BOTH branches so ⌘K works on either shell — do not
move it out of one branch "to dedupe."

### Dialog click-outside is hardened — do not undo (v3.64.2)

`frontend/src/components/ui/dialog.tsx:47-48` sets
`onPointerDownOutside={(e) => e.preventDefault()}` and
`onInteractOutside={(e) => e.preventDefault()}` on
`DialogContent`. This applies to every dialog the SPA renders.
A future "improve UX" pass that removes these and "fixes" close-
on-outside-click reintroduces the v3.64.2 bug: a stray click on
the dimmed scrim closes the dialog and loses any user state.
Esc / X / Cancel still work and are the right close paths. The
transient theme-picker dropdown is deliberately NOT hardened —
that's a picker, not a confirmation dialog.

### Theme catalog is 31 named themes plus three quick modes (v3.64.2)

`frontend/src/lib/themes.ts` is the canonical theme catalog. Each
entry carries the full CSS variable map (`bg`, `surface`,
`surface-2`, `ink`, `ink-2`, `ink-3`, `primary`, `primary-soft`,
`green`, `green-soft`, `amber`, `amber-soft`, `red`, `red-soft`,
`hairline`) plus an `isDark` flag computed from `bg` luminance.
`useTheme` applies the vars to `<html style="…">` overriding
`index.css`, writes the `.dark` class based on `isDark`, and
updates the iOS `theme-color` meta. Adding a theme is a catalog
edit. Removing a theme requires migrating users whose
`localStorage["bd-theme"]` points to the removed name (drop them
back to System). 5 contract tests in `tests/test_themes_catalog.py`.

### Widget catalog is 36 KPI widgets in 7 categories (v3.64.2)

`frontend/src/lib/widgetCatalog.ts` defines 36 widgets across
Activity (4), Performance (5), Capacity (4), Health (4), System
(5), Collection (8), Diagnostics (6). Each is a declarative
`spec(data) => KPISpec` function. `ALL_WIDGET_IDS` is the
canonical set; `DEFAULT_WIDGET_IDS` is the five-widget default
(done_today, throughput, queue_depth, action_req, disk_free -- it was
four, matching the legacy `/` UI, before `disk_free` was added in the
V2 redesign). `useWidgetSelection` reconciles persisted IDs
against `ALL_WIDGET_IDS` on read — unknown IDs are dropped, not
crashed. Adding a widget is a catalog edit + a KPICard test;
removing one requires reconciliation across persisted selections
and the U13 dashboard layout's `extraIds` parameter.
`LEGACY_WIDGET_IDS` in `useDashboardLayout` is an alias of the
inline `WIDGET_IDS` constant so the U13 contract test
(`test_u13_dashboard_layout_widget_ids_complete`) still matches.
Do not rename either constant without updating both consumers.
8 contract tests in `tests/test_widget_catalog.py`.

### `localStorage["bd-widget-selection"]` is per-device (v3.64.2)

The widget catalog selection persists in
`localStorage["bd-widget-selection"]`, cross-tab via the storage
event. Like theme / badge mode / sound preference (default), it's
intentionally per-device — silencing one device shouldn't silence
the others. Don't route it through `global_config` "for
consistency."

### `useSyncedSoundPref` is the dual-source contract (v3.64.3)

`frontend/src/hooks/useSyncedSoundPref.ts` consumes
`localStorage["bd-sound-sync"]` (per-device) to decide whether to
read/write `global_config.sound_on_complete` (synced) or
`localStorage["bd-sound-on-complete"]` (per-device, default).
Both POST keys `sound_sync_enabled` and `sound_on_complete` are
in the `/api/global_config` allowlist in
`bulk_downloader/app_global_config.py` (allowlist entry ~line 85,
branch handling ~lines 218-228 -- NOT in `app.py`),
accepted independently. The hook keeps localStorage in sync as a
side effect of the synced path, so flipping sync OFF preserves
the current value. Flipping sync ON adopts the current
localStorage value (so a sound-on user sees no behavior change).
See the "Sound preference has TWO sources" entry below for the
intentional-design note.

### `_M2_DIST_ROOT` is module-relative, not cwd-relative (v3.64.3)

`bulk_downloader/app.py:821` computes
`_M2_DIST_ROOT = Path(__file__).parent.parent / "frontend" / "dist"`
at module import. Tests that need to control whether `/m2` sees
a built SPA bundle MUST use
`monkeypatch.setattr(a, "_M2_DIST_ROOT", path)` to a
guaranteed-absent path (typically under `tmp_path`). A test fixture
that changes cwd does NOT affect the handler's view of dist. The
pre-v3.64.3 `test_d3_u1_m2_route_503_when_dist_missing` test
silently relied on dist not existing on the dev's machine; once
stash had `frontend/dist/` built post-1b, the test started
failing. Don't refactor `_M2_DIST_ROOT` to be cwd-relative or
recomputed per-request — both would re-open the failure modes
the absolute module-relative path closes. The current shape is
correct; the test needed updating, not the code.

### `_MANIFEST_EXCLUDE_NAMES` is pinned to `db.py`'s source-of-truth constants (v3.64.3)

`bulk_downloader/dev_suite/release_lint.py::_MANIFEST_EXCLUDE_NAMES`
(line ~285 -- grep the name, line numbers move)
lists filenames the release-zip builder excludes. It must stay in
sync with the runtime sentinels `db.py` writes:

- `.integrity_last_run` (defined at `bulk_downloader/db.py:1129`
  as `_INTEGRITY_STATE_FILE`)
- `.fts_optimize_last` (bare string at `db.py:329`)

Why load-bearing: `tools/build_release.py` imports
`bulk_downloader.app` during the endpoint-catalog drift gate.
That import boots `db.py`, which writes both sentinels into the
working tree. Without the exclusion, every release zip built from
a tree where the app had ever booted silently ships with stale
runtime state.

Pinned by
`tests/test_v3_64_3_release_zip_excludes_runtime_sentinels.py`.
The `test_exclusion_matches_sentinel_definitions_in_db_py` test
reads `db._INTEGRITY_STATE_FILE` directly and asserts membership
in `_MANIFEST_EXCLUDE_NAMES`, so a future rename can't silently
desync. Don't "clean up" by deleting any of the three exclusion
entries (`.integrity_check_last`, `.integrity_last_run`,
`.fts_optimize_last`) without first verifying via grep that the
corresponding sentinel is no longer being written.

### `tools/build_release.py --prebuild-spa` is opt-in by design (v3.64.3)

The flag runs `npm install && npm run build` in `frontend/`
before zipping, so the release zip ships `frontend/dist/`. Default
**off** preserves the v3.64.2 contract (build on deploy target).
Sandbox cannot run `npm install` (registry 403), so the release
zip built in the sandbox always ships without dist; a Node-equipped
operator runs `--prebuild-spa --skip-tests` locally for fresh-PC
single-step installs. Don't flip the default to ON "for
convenience" — that breaks every sandbox build. The helper at
`tools/build_release.py:344-368` handles all failure modes
(missing frontend/, missing package.json, no npm on PATH, npm
non-zero exit, silent build break where npm reports success but
`dist/index.html` is missing); each failure returns a distinct
exit code. Pinned by 9 tests in `tests/test_v3_64_3_d5a_prebuild_spa.py`.

### v3.64.4 invariants

### `AdaptiveOpts<T>.query` is a structural minimum, not `Query<T, unknown, T>` (v3.64.4)

`frontend/src/lib/polling.ts:28-29` defines:

```ts
export interface AdaptiveOpts<T> {
  query: { state: { data: T | undefined } };
  // ...
}
```

The `query` field's type is **deliberately not**
`Query<T, unknown, T>` from TanStack. Three reasons:

1. TanStack's `Query` has a contravariant `TError` parameter.
   `useQuery<T>(...)` with a single generic lets TanStack infer
   `TError = Error` by default. `Error` is not assignable to
   `unknown` in the contravariant position, so passing the
   resulting `Query<T, Error, T>` to a function expecting
   `Query<T, unknown, T>` fails strict TypeScript at every call
   site.
2. The structural minimum decouples this helper from TanStack's
   type-shape internals. TanStack can change `Query<...>`'s
   signature freely; this helper still compiles.
3. A "fix" that adds a second generic (`AdaptiveOpts<T, E = unknown>`)
   on the theory that TS will infer `E` from the argument does NOT
   fix the bug — explicit single-generic call sites like
   `adaptiveInterval<QueueV2>(...)` cause TS to use the default for
   the second generic, producing `AdaptiveOpts<QueueV2, unknown>`
   (structurally identical to the original). The structural-minimum
   shape sidesteps the variance question entirely.

A fresh instance that "tightens" the type back to
`Query<T, ...>` will reintroduce the bug latent in v3.64.3 across
7 call sites (Sites, Home, Queue, Activity, NowRunningList,
ThroughputSparkline, useWidgetData) — the first stash SPA build
after v3.64.3 hit it.

No unit test pins this directly; the TS compiler under strict
mode IS the gate. SPA build is the enforcement surface.

### `bd-dashboard-layout-${siteId}` storage namespace is site-scoped (v3.64.4, B-3)

The per-site widget dashboard at `/sites/:siteId` uses
`bd-dashboard-layout-${siteId}` as its localStorage key — NOT
`bd-dashboard-layout` (which is the GLOBAL dashboard's key) and
NOT any other permutation. Two contracts:

1. **Per-site layout writes do NOT touch the global key.** The
   B-3 e2e spec (`frontend/e2e/b3_site_detail.spec.ts:191`)
   asserts this with a "scope-key isolation" test that writes
   to a scoped key under a probe site and verifies the global
   key did not change.
2. **A site's layout state is fully scoped by `siteId`.** Deleting
   a site should clean its layout key — not pinned end-to-end yet,
   but consistent with the B-3 design.

A fresh instance that "unifies" storage keys (single
`bd-dashboard-layout` with a `{global: ..., bySite: {sid: ...}}`
inner structure) breaks the isolation contract and breaks the
e2e spec. The current shape — separate top-level keys per scope
— is intentional and easier to reason about under multi-tab
localStorage events.

Pinned by `frontend/e2e/b3_site_detail.spec.ts` (6 tests, 4
describe blocks). Not load-bearing enough to be a numbered INV,
but worth keeping in mind during any "unify storage" cleanup.

-----

### v3.65.0 + v3.65.1 invariants

### `compute_worker_counts` is the shared helper between two endpoints (v3.65.0 V2.1)

`bulk_downloader/app_widgets_api.py::compute_worker_counts(runners) -> dict`
returns `{"workers_active": int, "workers_total": int | None}` and is
called from both `app_widgets_api.py::_collect_data` (for the
`/api/widgets/data` payload) and `app.py`'s `/api/dashboard/v2`
handler. The two endpoints must report the same numbers for the
status banner and the worker-count widget to agree. A fresh instance
inlining the calculation in one endpoint (e.g. "to avoid the import
cost") will silently break the agreement the SPA assumes. The
`workers_total === None` empty-fleet honesty contract above applies
through this helper to both endpoints — see that entry for the
contract details. Pinned by `tests/test_d3_u2_v2_endpoints.py` on
the V2 side; legacy `tests/test_u49_workers_total_honest.py` on the
widgets side.

### `/api/dashboard/v2` preserves `active_workers` back-compat field (v3.65.0 V2.1)

The v3.65.0 V2.1 widening added `workers_active` and `workers_total`
to `/api/dashboard/v2`'s response **alongside** the existing
`active_workers` field, not in place of it. The SPA's V2.1+ build
reads the new fields; older builds and any external consumers still
read `active_workers`. Removing `active_workers` from the response is
a breaking change for older SPA builds during a rollback window —
keep both until at least v3.66.0. See SHAPE_REGISTRY §4 for the
current payload shape.

### `StatusPill` is the only filter-toggle primitive — Tabs without tabpanels is an a11y lie (v3.65.0 V3, V5)

`frontend/src/components/StatusPill.tsx` is the canonical filter-row
primitive across the SPA. v3.65.0 V3 (Sites filter) and V5 (Activity
window strip) replaced two `Tabs` primitives with StatusPill rows
because the Tabs primitive expects paired `tabpanels` (role=tablist

+ role=tabpanel) and the filter UI doesn't have separate panels —
  the underlying list re-renders in place. Tabs without tabpanels is a
  minor a11y lie for screen readers; StatusPill with `asToggle` + role
  button + `aria-pressed` is the honest mapping. A fresh instance that
  "upgrades the filter row to Tabs for consistency with the rest of
  the design system" reintroduces the a11y lie and breaks the
  docstring-intent contract pinned by
  `tests/test_d3_u4_sites_uses_filter_state` (which deliberately
  checks filter labels + Filter union + StatusPill + asToggle rather
  than the primitive name).

**Counter-example — Settings.** The Count/Percent toggle in Settings
uses `role="radiogroup"` + `role="radio"` + `aria-checked` and stays
that way. Radiogroup (mutex from a fixed set) is semantically
distinct from StatusPill asToggle (independently togglable button +
aria-pressed). Visual similarity is not a license to weaken
semantics. v3.65.0 V5 reviewed Settings and explicitly did not edit
it for this reason.

### `AppShell` forwards `headerVariant` to BOTH PageHeader AND DesktopShell (v3.65.0 V6)

`frontend/src/components/AppShell.tsx` passes the three header props
(`headerVariant`, `headerStatusLine`, `headerBelowStatus`) to both
the mobile `PageHeader` and the desktop `DesktopShell`. Pre-V6 it
forwarded them only to `PageHeader`; on desktop (≥1024px) the props
were silently dropped, and display-variant Home rendered as compact
at the desktop breakpoint. A mobile-only test sweep would not have
caught it. A fresh instance "simplifying" AppShell by removing the
desktop forwarding reverts to the silent-downgrade behaviour. The
contract is: any header-prop AppShell receives flows to both
surfaces.

### `/m`, `/m/`, `/m/ops`, `/m/ops/` all return 302 — NOT 301 (v3.65.1 D4)

The v3.65.1 D4 retirement uses `redirect("/m2/", code=302)` — Found,
not Moved Permanently. The route is permanent in practice, but 301
caches aggressively in browsers — if a future release ever needs to
flip behaviour for hotfix reasons, 302 leaves that door open. Also,
once 301 is in browser caches, the browser won't even re-request
`/m` to see a future change; 302 keeps the round-trip honest. Pinned
by `test_d3_u9_m_redirect_is_302_not_301`. A fresh instance
"hardening" the redirect to 301 breaks the rollback story. Also: do
not collapse the four route decorators (`@app.route("/m")`,
`@app.route("/m/")`, `@app.route("/m/ops")`, `@app.route("/m/ops/")`)
to fewer routes — the slash variants are pinned independently by
the rewritten U9 contract tests (23 tests after the v3.65.1
rewrite, up from 19).

### `/m2?ui=v1` no longer redirects — would loop with unconditional `/m` (v3.65.1 D4)

Pre-D4 the `/m2` handler redirected to `/m` when `?ui=v1` was set in
the query string (opt-out path). Post-D4 `/m` is itself an
unconditional redirect to `/m2/`, so the opt-out path would have
looped (`/m2?ui=v1` → `/m` → `/m2/` → potentially `/m2?ui=v1` again
depending on referer-handling). v3.65.1 removed the `?ui=v1` branch
entirely from `/m2`. Stale `bd_ui=v1` cookies are now harmless on
both routes — they all resolve to the SPA. A fresh instance trying
to "re-add the opt-out for users on slow connections" must add the
loop-detection logic; just restoring the pre-D4 branch makes the
loop return. Pinned by the U9 rewrite.

### U9 cookie helpers stay in `app.py` despite D4 (v3.65.1 D4)

`_m2_opt_state`, `_m2_apply_opt_cookie`, `_M2_COOKIE_NAME`,
`_M2_COOKIE_TTL` are still defined in `app.py` after D4. The only
remaining caller is the `?ui=v2` path on `/m2` — which sets the
cookie for back-compat readers (any external script or future SPA
build that still cares about the cookie). The helpers are slated for
removal in the v3.66.0 cleanup cluster alongside the template
removal. Until then: do not inline-rip them as "dead code" — they're
not dead, they're just down to one caller, and the call site is
visible in `app.py`'s `/m2` handler.

### Release-zip `_MANIFEST_EXCLUDE_*` triple — names, dirs, suffixes (v3.65.1 B4 + B5)

The `bulk_downloader/dev_suite/` package carries three exclusion
mechanisms in the manifest verifier -- `_MANIFEST_EXCLUDE_DIRS` in
`_common.py`, `_MANIFEST_EXCLUDE_NAMES` / `_MANIFEST_EXCLUDE_SUFFIXES`
/ `_MANIFEST_EXCLUDE_PATHS` in `release_lint.py`, all re-exported from
the package `__init__.py` (grep the constant names; do not trust the
file split to have stayed put):

- `_MANIFEST_EXCLUDE_NAMES`: bare filenames matched exactly. Includes
  `downloader_history.db*`, `.integrity_*`, `.fts_optimize_last`,
  `test_results.json`, `SUMMARY.txt`, `sites_config.json`,
  `.DS_Store`, `debug.flag`, **`vapid_keys.json`** (v3.65.1 B5 —
  PEM private key, SECURITY), **`test_singleton.db`** (v3.65.1 B5).
- `_MANIFEST_EXCLUDE_DIRS`: directory names matched against any
  path part. Includes `__pycache__`, `.git`, `venv`, `.venv`,
  `node_modules`, `screenshots`, `.pytest_cache`, `results`,
  `profiles`, `.mypy_cache`, **`state`** (v3.65.1 B5 — the
  `_heartbeat_to_disk_loop`'s working directory).
- `_MANIFEST_EXCLUDE_SUFFIXES`: filename suffixes via `endswith`.
  Includes `.pyc`, `.pyo`, `.log`, **`.zip`** (v3.65.1 B4 — the
  just-written release zip would otherwise appear in `root.rglob("*")`
  AFTER `zip_manifest_check` writes it, reporting "missing from zip"
  and failing the build).

**The exclusions are NOT substring-based.** Two tests in
`TestB5RuntimeArtifactsExcluded` specifically pin this:
`tests/test_state_machine.py` (substring "state") and a hypothetical
`docs/vapid_keys_design.md` (substring "vapid_keys") must NOT be
excluded. A fresh instance "simplifying" the check to a single
substring scan breaks this contract. The current three-mechanism
shape (exact name OR exact dir-part OR endswith-suffix) is the
correct granularity.

**B5 was a SECURITY fix, not just hygiene.** Pre-v3.65.1, a local
rebuild of the zip on a tree that had imported `bulk_downloader.app`
generated `vapid_keys.json` (PEM private key) at the build root and
shipped it. Any operator deploying that zip inherited the build
environment's web-push private key. The defect class matches the
v3.64.3 `.integrity_last_run` finding but with worse blast radius.
See OPEN_THREADS 1b for the operator audit procedure for pre-v3.65.1
local builds.

### `app_config.json` ships pristine — token secrets generate lazily (v3.65.1 B5b)

The shipped `app_config.json` in the release zip is
`{"global_max_concurrent": 0}`. The build process resets the file
to this content before zipping because `_save_app_config()` runs at
app boot (during the endpoint-catalog gate's import), and on any
tree where the suite has executed the file accumulates: pytest
tempdir paths in `path_allowlist`, plus `stream_token_secret` and
`share_token_secret` (cryptographic secrets generated lazily by
`bulk_downloader/shares.py` and the stream broker). The pre-v3.65.1
local rebuilds that shipped this content baked the build
environment's secrets into every deploy.

**Lazy generation is the correct shape**, not eager seeding. Token
secrets are generated by their consumers on first use and persisted
to `app_config.json` per-deploy. `path_allowlist` is populated by
`_load_app_config()`'s first-run seed from `$BD_HOME` and
`~/Downloads/bulk_downloader` on operator deploy. Baking either in
would mean every deploy starts with the same secrets — a SECURITY
defect class. Pinned by 6 tests in
`tests/test_v3_65_1_b5_app_config_pristine.py`:
`test_b5_app_config_ships`, `test_b5_app_config_is_valid_json`,
`test_b5_no_token_secrets_baked_in`,
`test_b5_no_pytest_tempdir_paths_baked_in`,
`test_b5_no_path_allowlist_or_empty`, and
`test_b5_only_deterministic_keys` (whitelist of safe-to-ship keys —
forcing function for any future addition).

### `--amber-dim` is the AA-passing token; `--amber` on amber-soft surface is a WCAG fail (v3.65.1 A1)

`frontend/src/index.css` defines two amber tokens in light mode:

- `--amber: <hue/saturation/lightness>` — passes AA on regular
  surfaces (`bg-bg`, `bg-card`). 9 standalone `text-amber` sites in
  the SPA use this and are correctly NOT swapped.
- `--amber-dim: #92400e` — passes AA on amber-soft surfaces
  (`bg-amber-soft`). Used at 13 swap points across 10 SPA files.
  Dark mode `--amber-dim` is `#fbbf24` (= `--amber`) — symmetric so
  consumers don't have to mode-branch.

**Rule:** `text-amber` on `bg-amber-soft` is a contrast failure in
light mode (1.97:1, below AA's 4.5:1). Always use `text-amber-dim`
on amber-soft surfaces. Tailwind utility `text-amber-dim` is exposed
via `frontend/tailwind.config.js`. A fresh instance adding a new
amber-on-amber-soft pairing must pick `text-amber-dim`; the linter
does not catch this, the WCAG audit on `/m2/` does. The 9 standalone
`text-amber` sites (regular surface) are listed in the v3.65.1
CHANGELOG A1 entry as not-swapped — that list is the reference for
which surface kind each existing site sits on.

-----

## “LOOKS LIKE A BUG, IS INTENTIONAL” — do not fix

- **`/m2` returns 503 (not 404) when `frontend/dist/` is missing.** When
  the Node build step hasn’t run, `/m2` returns 503 with a custom JSON
  body naming `install_linux.sh` / `install_windows.bat` and the
  `X-BD-M2-Status: not-built` header. A future maintainer might think
  “503 is wrong, it should be 404” — but 503 is correct: the route
  exists, the resource is *temporarily* unavailable because
  dependencies aren’t installed. The installer-aware message and the
  `X-BD-M2-Status` header are both load-bearing diagnostics — tools or
  scripts may grep the header to detect a no-build environment.
  Pinned by tests in `test_d3_u1_scaffold.py`. Do not change to 404
  and do not remove the header.
- **Sound preference has TWO sources, default per-device (v3.64.3).**
  The v3.64.2 default of `localStorage["bd-sound-on-complete"]`
  remains the default behavior — silencing one device should not
  silence others. v3.64.3 adds opt-in sync via `useSyncedSoundPref`
  (`frontend/src/hooks/useSyncedSoundPref.ts`):
  - `localStorage["bd-sound-sync"]` is itself per-device (otherwise
    you couldn't opt out from a single device).
  - When the device has opted in, the hook reads/writes
    `global_config.sound_on_complete` (via the two new POST
    allowlist keys `sound_sync_enabled` and `sound_on_complete` in
    `bulk_downloader/app_global_config.py`, ~line 85) instead of
    localStorage.
  - localStorage is kept in sync as a side effect, so flipping
    sync OFF preserves the current value. Flipping sync ON adopts
    the current localStorage value (so a sound-on user sees no
    behavior change).
    Do NOT "simplify" by collapsing the dual-path code and routing
    everything through `global_config`. The per-device default is
    intentional. Pinned by `tests/test_v3_64_3_sound_sync.py` (backend
    allowlist + paired-adoption + default-unset + bool coercion).
- **v3.64.5 is byte-identical to fixed v3.64.4 (v3.64.5).**
  v3.64.5 was cut as an accounting fix-up over v3.64.4. The
  v3.64.4 first cut shipped a stale `FUNCTION_INDEX.md` that
  failed the drift test on the stash suite run (A.1's
  `# INV-NNN` comment additions shifted function-def line
  numbers; the AST walker emits the new line numbers; the
  drift test fires). The fix
  (`tools/build_function_index.py`, invoked today as
  `venv/bin/python tools/build_function_index.py` -- never bare
  `python`/`python3`) was applied in-place
  to the v3.64.4 zip, then the cleaner accounting was to call
  the fixed state v3.64.5. A fresh instance that diffs the two
  zips and finds no functional source delta is correct; the
  version bump is purely for accounting. The threshold counter
  does NOT advance for v3.64.5 — it would double-count v3.64.4.
- **`WaitingJobRow` swipe gesture intentionally has NO keyboard
  equivalent.** The swipe-to-cancel gesture is touch-only. Keyboard
  users have the explicit `X` button on the same row. Adding a
  keyboard chord for swipe would be redundant noise. Do not “fix” by
  adding a `Shift+Backspace` shortcut.
- **`mobile.html` and `m_ops.html` stay on disk until v3.66.0 — but
  the routes no longer serve them (v3.65.1 D4).** D3 ships at `/m2`.
  D4 in v3.65.1 retired the legacy route handlers: `/m`, `/m/`,
  `/m/ops`, `/m/ops/` all return unconditional `302 → /m2/`. The
  templates themselves at `bulk_downloader/templates/mobile.html`
  and `bulk_downloader/templates/m_ops.html` remain on disk as
  emergency-rollback escape hatches until v3.66.0 per the schedule
  in `D3_OPT_IN.md`. A future instance might find the templates,
  grep that nothing renders them, and "clean them up" as dead code.
  Do not remove them before v3.66.0 — emergency rollback requires
  the route handlers to be reachable on a previous-release tree, and
  the template files must exist for that rollback to render. The
  v3.66.0 cleanup cluster bundles the template removal with the U9
  cookie helper retirement (see OPEN_THREADS v3.66.0 cluster). The
  pre-D4 opt-in mechanism (cookie/query-string branching in `/m`)
  is **gone** — restoring it requires reverting D4, not just adding
  a new conditional.
- **Path-A mockups in conversation outputs are NOT implementation
  targets.** The 23 Path-A mockup HTML/PNG files generated during
  v3.63.10 planning live in conversation outputs only. v3.64.0
  implemented Path B (React SPA). A future instance who finds the
  Path-A mockups in old conversation history might think they
  describe the shipped UI and rebuild against them. They do not. The
  Path-B mockups (the design reference for what shipped) were
  rendered in the v3.63.10 session as well; both sets are in
  conversation outputs only, NOT in the repo.
- **`/m/ops` is now a 302 redirect — the historical "no restart, no
  per-cancel" scope note is moot in current behaviour but preserved
  for emergency-rollback context.** v3.65.1 D4 retired the
  `/m/ops` route handler (now `302 → /m2/`). Pre-D4 the page
  deliberately had no restart button and no per-item cancel — no
  `/api/restart` endpoint exists (would require passwordless-sudo
  plumbing on the service account, a separate operator decision),
  and no per-row cancel endpoint exists either — the available
  surface is per-SITE stop via `/api/sites/<sid>/stop`. The SPA's
  `/m2/` covers per-site stop via that same endpoint with CSRF. A
  fresh instance asked to "add restart to /m/ops" or "add per-item
  cancel" should treat it as a NEW multi-piece task (endpoint +
  auth + UI), not a fix to a deprecated PT8 page. Surface the cost
  first. If emergency rollback to the v3.64.x `/m/ops` happens, the
  template still exists — but the rolled-back code's missing-
  restart behaviour is by design, not a bug.
- **Em-dashes (U+2014) are FORBIDDEN in `.bat` but ALLOWED in `.sh`**
  — `bat_lint` rule L1 requires zero non-ASCII bytes in `.bat` files
  (cmd.exe code-page issues — see Platform Gotchas / Windows). The
  `sh_lint` rule only checks CRLF/exec bit and does NOT enforce
  ASCII on `.sh` files; shipped `.sh` files contain em-dashes
  legitimately. A fresh instance “normalizing” `.sh` files by
  stripping em-dashes is gratuitous churn. Conversely, a fresh
  instance writing a new `.bat` file MUST avoid them — the contract
  test catches the violation but only after it ships to the
  `changed/` tree.
- **First-load CSRF 403** — `app.py::index()` mints the session inline
  so the page meta tag carries a valid CSRF token from the first byte
  (v3.43.55). A single 403-then-retry on the very first request is
  expected and was deliberately demoted WARNING→INFO. Repeated 403s for
  the same path in seconds would be a real bug.
- **`/api/pair` is auth-free** — it is the device-pairing entry point;
  adding auth locks out new devices.
- **`extractors.py` / `hls_downloader.py` swallow all exceptions** —
  adapters MUST return `ExtractResult`/`DownloadResult`, never raise.
  The defensive `hasattr`/`getattr` probing and blanket `except` are
  intentional: the EchterAlsFake libraries change APIs between
  versions. Missing ffmpeg → `is_available()` returns `False` and the
  worker falls back; not an error.
- **~373 `except Exception: pass` blocks** — the large majority are
  deliberate fail-open guards for optional dependencies (`ffmpeg`,
  `mutagen`, `videohash`, `apprise`, `pywebpush`, `playwright-stealth`,
  `httpx`, `scrapling`, `flaresolverr`) or best-effort persistence.
  INV-003 mandates fail-open. Do not convert to hard failures. (But —
  a broad `except` can still hide a typo bug; verify the `try` body
  separately without removing the fail-open intent.)
- **`subprocess.run(..., shell=True)` in `hooks.py`** — intentional;
  user-authored hooks need shell metacharacters. Values are
  `shlex.quote`’d (POSIX) / OS-branched quoting (Windows). Single-user
  self-trust model. Do not remove `shell=True`.
- **`_validate_path()` + empty `path_allowlist` is permissive** — a
  documented design choice under the single-operator LAN trust model.
  `path_allowlist` is opt-in; empty means permissive, not restrictive.
- **`cookie_file` accepts arbitrary absolute paths** — by design,
  single-user trust model.
- **Dev mode is ON by default** (v3.47.7+). Kill-switch is the env var
  `BD_DEV_MODE_DISABLE=1`. Do not propose re-locking by default.
- **`/api/dev/*` endpoints return 404 without `BD_DEV_MODE=1`** — the
  gate that keeps the in-GUI test runner, the perf-lab, and the
  ~92-route dev-suite surface out of user installs. `/api/dev/enabled`
  always responds. The whole dev-tools surface depends on this gate
  (`dev_tools.is_dev_mode()` + `_dev_mode_guard()`) — do not remove the
  guard. State-changing (A) dev tools — config hot-reload, cache-clear,
  snapshot/restore, temp-dir cleaner, job replay, the fixture-site
  controller, etc. — are additionally POST + `_check_csrf()`-gated.
  Copying a read-only dev tool’s route pattern (GET, no CSRF) for a
  state-changing tool is the mistake to avoid: read-only is GET,
  state-changing is POST + CSRF. The fixture-site controller in
  particular starts a real in-process werkzeug server thread — ensure
  its stop path is reachable so a test or operator does not leak
  server threads.
- **`quality_preference` fallback to highest-available** on no match
  is intentional. The alias words `best`/`worst`/`highest`/`lowest`
  are case-sensitive in `_pick_quality`.
- **`use_library_extractor: True` doing nothing** when the matching
  EAF library is not installed is an intentional silent fall-through,
  not an error.
- **The `/api/sites/<sid>/history/export` route** (v3.62.1) is the
  download-history exporter; `/api/sites/<sid>/export` is config
  export. They are deliberately distinct — do not merge them back.
- **`do_login(..., allow_manual_takeover=False)` — the gate, and its
  default, are load-bearing.** `do_login` (in `login.py`) defaults
  `allow_manual_takeover=False`. The keeper/worker path calls it with
  `False` (e.g. `app.py` ~1812) so a headless keeper, with no human
  present, never blocks waiting for a manual step. With `True` (the
  interactive path) failure modes such as a detected captcha escalate
  to a visible manual takeover. Removing the parameter or defaulting it
  `True` would make headless keepers hang. Do not “simplify” it away.
- **The interactive login path opens a *visible* browser by design** —
  `login.py` launches the login browser with `headless=False` (e.g.
  lines ~684 and ~1143). A human may need to solve a captcha or finish
  a manual takeover. Do not “optimize” the interactive login to
  headless. Worker browsers are a separate path and ARE headless — do
  not conflate the two.
- **Ollama `bind: address already in use` is not an error** — the
  official Ollama installer registers and starts its own systemd
  service, so a second `ollama start` / `ollama serve` collides on the
  port. Expected. Manage Ollama via `systemctl`, not the tool’s own
  `start` subcommand.
- **Cookie-based login success is intentionally strict via
  `_looks_authenticated` — do not loosen it back.** Earlier code
  treated “the login page closed mid-submit + at least one cookie
  captured” as success; a single stray cookie then read as a logged-in
  session, so a genuinely failed login could be marked succeeded
  (the v3.62.2 loose-success bug). v3.62.4 fixed this:
  `login.py::_looks_authenticated()` (def ~1037, called in the
  `do_login` cookie path ~1432/1461) now requires the *shape* of a
  real session — an auth/session-named cookie, OR several substantial
  non-csrf/consent cookies — before a page-closed-during-submit counts
  as authenticated. The multi-cookie heuristic looks fussy; it is
  deliberate. Do not “simplify” it back to mere cookie presence.
- **The dev-suite SQL console is a POST route despite being
  read-only.** `/api/dev/sql` cannot mutate the DB (see the
  load-bearing SELECT-only filter above), but it is POST + CSRF-gated
  on purpose: a query is better carried in a request body than a URL
  (URLs get logged; bodies are cleaner; CSRF gating is consistent with
  the other dev POST routes). Do not “correct” it to GET.
- **`dev_suite` config/redaction tools mask by key-name heuristic.**
  `config_dump()` redacts any value whose key name contains a secret
  hint (`password`, `token`, `cookie`, `cred`, …). An empty secret
  value is left visible, so it does not itself become the redaction
  marker. This is intentional — do not redact empty values, and do not
  assume the dump shows real secret values.
- **`refreshAIStatus()` updates `window._aiStatus` unconditionally**
  (v3.62.4), then paints the toolbar dot via a no-op-safe helper. This
  is the fix for the stuck-`X` bug — the old top-of-function
  `if (!dot) return;` early return is gone on purpose. Do not
  reintroduce an early return that gates the state update on a DOM
  element existing.
- **`dev_suite` has repeated `# ── N. name ──` section numbers** —
  the code was built by many independent appends (as one ~7,600-line
  module, now split into the `bulk_downloader/dev_suite/` package), so
  the section-header comments carry duplicate numbers (e.g. two `33`s),
  and the split spread them across submodules. This is purely
  cosmetic — every *function name* is unique and Python resolves them
  correctly. Do not do a blanket “renumber the sections” pass: it
  touches live files for zero behavioural gain. (Genuine
  *duplicate function definitions* — where Python silently keeps the
  last — ARE a real bug, distinct from this; see the load-bearing
  invariant above.)
- **`dedup_hash_explore` reports `registry_present=False` when the
  `videohash` library is absent** — this is fail-open by design. The
  whole `dedup` subsystem is optional and silent when `videohash`
  isn’t importable; the dev tool surfaces “no dedup registry — feature
  not in use yet,” which is the correct read. Do not “fix” the tool
  to raise.
- **`flaresolverr_health` calls the network endpoint** — and that is
  its job. It is one of three v3.62.7 dev tools that talk to the
  network (the others being `vision_test_run` and
  `vpn_connectivity_probe`). It uses `flaresolverr_client.ping()`,
  which already fails open with `{ok:False, error:...}`; the dev
  gate (`_dev_mode_guard()`) is the safety boundary. Do not add a
  local-only guard or refuse-to-call-network check — that would
  defeat the diagnostic purpose.
- **`disk_usage` / `download_scan` do a real filesystem walk per
  call** — a single call on a large download root can do tens of
  thousands of `stat()`s. The `max_files` cap (default 20,000, hard
  limit 200,000) and the allowlist enforcement bound this. The tools
  are operator-triggered and one-shot. **Do not remove the cap, do
  not raise the default, and do not add a background sampler “to make
  it instant”** — the module-level-no-work rule (the import-clean
  invariant above) forbids that.
- **`feature_flags.delete_flag` returns `ok=true, removed=false`
  when the flag is absent** — looks like it should be a “not found”
  error. Intentional: the delete path is idempotent so two racing
  deletes can both report success without one looking spuriously
  broken. The `removed` field disambiguates “we changed state” from
  “no change needed.” Do not “fix” to raise on missing.
- **`request_replay._BODY_TRUNC = 4096` is small on purpose** —
  looks like it should be larger for useful debug context.
  Intentional cap: 200 entries × 4KB × 2 (request + response) ≈
  1.6 MB worst case. A single chatty SSE-style endpoint would blow
  the buffer past tens of MB otherwise. Full-body capture is the
  `dev_events` event tap’s job; `request_replay` is a debug aid for
  replaying *requests*, not for archiving full payloads.
- **`login_flow_recorder` does its `macro_recorder` imports inside
  functions, not at module top** — looks like cosmetic mess. The
  file is imported during `app.py` boot for route wiring, and the
  in-function import pattern is what keeps `login_flow_recorder`
  safe to import in any BD context (tests, CLI, lightweight
  handlers). Mirrors the existing `macro_recorder` pattern. Do not
  hoist the imports to the top.
- **T43 `tls_cert_check` does its own SAN match in `_san_matches`
  even though `ssl.wrap_socket` already validated** — looks
  redundant. Intentional: the report has to explain to an operator
  *why* a wildcard matched (e.g. `*.example.com` matched
  `api.example.com`). The handshake validated implicitly; the report
  shows the match in plain terms so the operator can confirm it’s
  the cert they expected.
- **T38 `i18n_coverage` only scans HTML templates, not JS files** —
  the underlying `i18n.py` machinery could extract from any file,
  and JS files do hold a lot of user-visible strings. The skip is
  deliberate: a JS scan returns extremely noisy output (regex
  selectors, error strings, internal IDs) with no clean way to
  discriminate user-facing strings from internal ones. PASS /
  uncovered / stale verdicts would be dominated by false positives.
  A future session wanting JS coverage needs a JS-specific extractor
  with heuristics (e.g. only strings inside `t('...')` calls).
- **`_systemctl()` returns `ok=True` for `is-active <missing>` even
  though rc=3** — looks wrong. `ok` here means “systemctl ran and
  reached the bus”, NOT “the unit exists” and NOT “rc==0”. The
  docstring spells this out explicitly (T56 update). Callers must NOT
  take `ok=True` as proof the unit exists; they gate on
  `_unit_exists()` separately. “Tightening” `_systemctl` to return
  `ok=False` on rc!=0 breaks callers that use the return value as
  “systemctl is functional” (e.g. the L24 fallback path reading a
  degraded unit).
- **T54 (env-probe 8s timeout + retry) looks redundant after T56 —
  keep it** — T54 was the v3.63.2 fix that didn’t actually fix the
  five `test_u41_systemd_live_tests.py` failures (T56 did). It looks
  like dead code one could revert as “the wrong fix.” Do not. T54
  still protects against a different failure mode — a genuine timeout
  under heavier `--workers=N` load on an underpowered host — and the
  cost is one extra subprocess call at module import. Reverting it
  brings back a real degradation surface. The contract test
  `test_source_has_no_2_second_subprocess_timeout` prevents accidental
  revert.
- **`--per-check-timeout` defaults to 60s; a check tripping it is a
  real bug to fix in the check, not in the default** — looks like the
  T55 default could be “too short.” It is not. The default is
  calibrated against the rest of the suite’s behaviour. The L34 case
  that surfaced this rule (pre-v3.63.4): L34 was GET-ing `/api/stream`
  (SSE), and `urllib.request.urlopen(timeout=N)` doesn’t bound
  `read()` of a streaming response, so the loop sat for 77s in
  `read()` until the wall fired. The fix was to skip the streaming
  route in L34 (see `_L34_STREAMING_SKIP` above), NOT to raise the
  global default. Raising the default hides hangs in every other
  check; raising one check’s local timeout still hides whatever
  real issue made *that* check slow. Always reach for the diagnostic
  first.

-----

## PLATFORM GOTCHAS

### Windows / `cmd.exe`

(The `.bat` installers still ship even though the live deployment is
now Linux — these rules remain in force.)

- `.bat` files: 0 non-ASCII bytes, CRLF endings. Verify after every
  edit (`LC_ALL=C grep -P '[^\x00-\x7F]'`).
- Inside `cmd for %%P in (...)` loops, unquoted `>=`/`<=`/`<`/`>` are
  parsed as redirection. Use `NEQ`/`GEQ` or quote the pin.
- `python -m pip install --upgrade pip`, never `pip.exe` (locked exe).
- `%~dp0` ends in `\`; strip it before quoting. Robocopy exit codes
  0–7 = success, 8+ = failure (`if !RC! GEQ 8`).
- PowerShell needs `.\install.bat`; cmd does not.

### Linux / systemd

The live deployment is Ubuntu, run as the `bulkdownloader` systemd
service (`install_service.sh` generates the unit). systemd-specific
traps:

- A systemd service inherits **none** of the operator’s shell
  environment and runs with whatever `WorkingDirectory` the unit sets.
  BulkDownloader resolves its SQLite DB and `sites_config.json`
  relative to cwd — a unit with a missing or wrong `WorkingDirectory`
  makes the app create a fresh empty DB elsewhere, which looks exactly
  like total data loss. The unit MUST set `WorkingDirectory` to the
  app directory.
- Before `systemctl start`, kill any hand-started instance — a port
  conflict (`Port 5555 is in use`) is the classic crash-loop cause.
- Read the real failure from `journalctl -u <name>` — never guess from
  the symptom.
- `bash -n` only parses syntax — it does NOT detect undefined-variable
  use under `set -u`. The `tests/test_u45_capture_sh_shipped.py:: test_capture_sh_parses_under_bash_n` contract is a parse check, not
  a static analysis. A future `capture.sh` edit that introduces an
  unbound variable will pass the contract and fail at runtime. If you
  want stronger checking, run the script and read the trace.
- The Ollama installer registers its own service; do not double-start
  it (see the `address already in use` note above).

### Python text I/O on Windows

`open()`, `Path.read_text()`/`write_text()`, `subprocess.run(text=True)`
default to the locale encoding — cp1252 on Windows, UTF-8 on Linux.
Always pass `encoding="utf-8"` for text. Binary mode is exempt.

### Windows file locking

Windows refuses to delete a file held open by any process. The app’s
logging `FileHandler` keeps a log open. Temp-dir helpers must close
logging handlers and pass `ignore_cleanup_errors=True` before teardown.

### Windows exception shapes

Refused connections and resets raise different exception types than
Linux (`ConnectionResetError [WinError 10054]`, varied httpx errors).
Tests asserting exact network-failure exception kinds must tolerate
the Windows variants.

### Subprocess termination

Kill the process group, not just the parent — `ffmpeg` leaves segment
children otherwise (`creationflags=CREATE_NEW_PROCESS_GROUP` on
Windows, `start_new_session=True` POSIX; see `hls_downloader._terminate`).

### ffmpeg argv ordering

Input options must precede `-i URL`; the output path is last.
`[ffmpeg, global, input opts..., -i, URL, output opts..., OUTPUT]`.

### cwd-relative DB path

`DB_PATH` is the bare filename `downloader_history.db`, resolved
against cwd. Code changing cwd between a DB write and read hits two
files. (This is also why a systemd unit must set `WorkingDirectory` —
see Linux / systemd above.)

### `constants.INSTALL_DIR` is captured at module import — `clean_workdir` does not reset it

`bulk_downloader/constants.py` reads `BD_INSTALL_DIR` (or cwd) once
at import and freezes it for the process lifetime. Python caches
module imports, so the *first* test in a run captures `INSTALL_DIR`,
and every later test in the same process uses that same value.
`clean_workdir` chdirs but does NOT change `INSTALL_DIR`. Any module
whose state file path is built from `INSTALL_DIR` — `feature_flags.py`
(`feature_flags.json`), the `maintenance.py` immediate-override
analogue, T42’s `tests/fixtures/golden/` — therefore shares one file
across every test in the run unless explicitly isolated. **Rule for
tests:** either (a) set `BD_INSTALL_DIR` *before* first import and
re-import the modules (the `test_i18n_phase191.py` pattern), or
(b) monkeypatch the module’s `state_path()` function to a `tmp_path`
sibling for the test’s duration (the T40 / T44 `_Isolated` context
manager). `clean_workdir` alone is not sufficient. See
LESSONS_LEARNED A14 for the transferable rule.

### Stash `capture.sh` runs the test suite BEFORE installing the systemd service

The operator’s fresh-machine `capture.sh` on stash runs in this order:
[1] sysinfo, [2] full test suite, [3] CSRF diagnostic, [4]
`install_service.sh`, [5] Ollama status, [6] live_check, [7] live
tests, [8] dev tools, [9] T51 dry-run. **Step [2] runs before step
[4].** So at suite time, the bulkdownloader systemd unit does NOT
exist yet; `_env.HAS_BULKDOWNLOADER_UNIT` is `False` even on a
“correctly provisioned” stash. The script ships in the repo at the
root (`capture.sh`, pinned by `tests/test_u45_capture_sh_shipped.py`)
and the order is deliberate — the suite is
“static” (must work without a running service) and the live tests in
[7] are “dynamic” (need it running). The implication: any
environment-coupled test must tolerate the
`HAS_SYSTEMD_BUS=True` + `HAS_BULKDOWNLOADER_UNIT=False` combination
as a real, supported state, not a sandbox-only edge case. The
`test_u41_systemd_live_tests.py` three-branch truth table (T56) is
the model. A two-branch `if HAS_BULKDOWNLOADER_UNIT: ... else: ...`
conflates sandbox (no bus) with fresh-stash-pre-install (bus + no
unit) and re-creates the v3.62.6→v3.63.2 mis-FAIL pattern.

### Live-test runner output buffering — use `python -u` and pipe through `tee`

A v3.63.1 capture step hung silently: the `live_tests/run.py`
process was alive but `/tmp/v3_63_1_capture/07_live_tests.log` was
empty. Cause: stdout buffering when redirected to a file. Fix:
`python -u -m live_tests.run ... 2>&1 | tee path` — `-u` for
unbuffered Python output, `tee` for a TTY-connected stdout. The
capture script’s own redirect now uses the same pattern. If a
future operator sees “alive but silent” live-test behaviour, this
is the cause.

### Release-zip hygiene: runtime state must not enter the zip

Importing `bulk_downloader.app` triggers the boot-time integrity
check and FTS optimizer, which write `.fts_optimize_last`,
`.integrity_check_last`, and `.integrity_last_run` at the root of the
working dir. Any test run that touches `app.py` creates them. They
are legitimate operator state on a deployment but **must not enter a
release zip** — they leak host state into the artifact and would
overwrite the operator’s real timestamps when the deploy lands. Strip
them
(and other runtime files like `downloader_history.db`,
`app_config.json`, `logs/*`, `live_recordings/*`) before building.
The reliable check is a file-list diff against the previous release
zip — investigate every addition and strip anything that is runtime
state, not source.

-----

## TEST & RUNNER GOTCHAS

- The custom `run_tests.py` is not pytest: no `--filter`; only expands
  `@parametrize` and `autouse` fixtures on class methods; does not
  inject non-autouse fixture parameters by name (it hard-codes the
  three names `clean_workdir`, `fresh_app`, `aiassist_module` plus
  `tmp_path` / `monkeypatch`); and does NOT invoke pytest’s
  module-level `setup_module` / `teardown_module` hooks — a file
  relying on them runs them never (the symptom is a green-but-slow
  file). Set module-level state at import time instead. Test-side
  helpers are plain modules under `tests/` (e.g. `tests/_env.py`),
  imported via the file-relative
  `sys.path.insert(0, os.path.dirname(__file__)); import _env` pattern
  — do NOT add `tests/__init__.py` (it would break the runner’s
  file-by-file loader). New tests use plain helper functions, not
  fixture params, and resolve paths absolutely (from `__file__`) since
  a `chdir`’ing fixture can leak cwd into a later file.
- `parametrize` fans one method into many counted cases — test count
  is not test-function count. Never hard-code a count as a contract.
- 3 `test_extension_live.py` skips are expected (headless Chromium
  can’t register the extension service worker), not a regression.
- `BD_DISABLE_KEEPALIVE=1` is mandatory for any test run.
- Clear `__pycache__` before runs — stale `.pyc` causes false failures.
- Test helpers using the app in an isolated cwd must
  `Path("screenshots").mkdir(exist_ok=True)` after the chdir.
- `macro_recorder._normalize_name` lowercases AND replaces spaces
  with hyphens, so a name like `"INVALID NAME"` is normalized to
  `"invalid-name"` and becomes valid. A test that wants to assert
  rejection must use a name containing a character outside
  `[a-z0-9_-]` (e.g. `!`, `@`, `/`). T45 tripped on this initially.
- Long-lived JSON state files (`feature_flags.json`,
  `maintenance.json` analogue, the perf-lab state file,
  `config.json`, the model-loaded snapshot) must use `.tmp` sibling
- `os.replace()`. A contract test in `tests/test_contracts.py`
  enforces this on the listed files; adding a new long-lived JSON
  state file means adding it to that contract test alongside the
  atomic-write code.
- Environment-coupled tests (`test_u31_deploy_lint.py`,
  `test_u41_systemd_live_tests.py`, `test_v3_62_3_model_dropdown.py`)
  branch on `_env.HAS_SYSTEMD_BUS` / `HAS_BULKDOWNLOADER_UNIT` /
  `HAS_OLLAMA_LOCAL` so they pass on both sandbox and real deployment.
  A new environment-coupled test must use the same pattern.
- **Every new `/api/dev/*` route must have a `test_…_routes_are_dev_gated`
  test** asserting 404 with `BD_DEV_MODE_DISABLE=1`. These tests ARE
  the dev-gate contract — no other check enforces it. Do not delete
  them as redundant.
- Enforced guard / pin tests fail if a fixed bug is reintroduced:
  `test_v3_62_2_guards.py` (Ollama default model tags must be real
  registry tags incl. the frontend `static/app.js` copy; login
  templates must have distinct user/pass selectors and a submit
  selector; the widget JS must poll on a timer; status endpoints must
  be GET + JSON; the v3.62.3 model dropdown and the AI-status-icon
  independence from the toolbar dot are pinned),
  `test_v3_62_2_login_fallback.py`, `test_v3_62_2_login_extractor.py`,
  `test_csv_bulk.py`, `test_v3_62_3_model_dropdown.py`,
  `test_login_submit_robustness.py`, `test_mass_import_jobs_bounded.py`,
  `test_perf_lab.py`, `test_dev_suite.py`, and the v3.62.6–v3.63.3
  dev-tools test files — `test_dev_suite_tier0.py`, `_tier1.py`,
  `_tier1b.py`, `test_dev_metrics.py`, `test_dispatch_chain.py`, the
  v3.62.7 Tier-3 set `test_t1_*` through `test_t17_*` (covering D-37,
  D-38, D-40, D-41, D-43, D-42, D-49, D-45, D-48, D-50, D-112, D-113
  among others), the v3.63.0 Tier-4 set `test_t34_*` through
  `test_t46_*` (covering D-107 dead CSS, D-117 storage tier, D-122
  maintenance override, D-56 token estimator, D-108 i18n coverage,
  D-58 model pull, D-121 feature flags, D-16 window simulator, D-75
  golden files, D-47 TLS, D-46 request replay, D-27 login-flow
  recorder, L-29 VPN kill-switch read-only), the v3.63.1–v3.63.3
  patch-arc set `test_t47_storage_tier_utc_cutoff.py`,
  `test_t49_runner_timing.py`, `test_t50_test_timing.py`,
  `test_t51_regenerate_goldens.py`, `test_t54_env_probe_timeout.py`
  (greps `tests/_env.py` for the literal `timeout=2)` / `timeout=2,`
  to prevent T54 regression), `test_t55_live_test_harness_timeout.py`,
  and `test_t56_systemd_missing_unit.py` (greps
  `live_tests/checks.py` to ensure `_unit_exists` appears before the
  inactive-FAIL branch in `l27_survives_logout`), and the `test_u32`–
  `test_u43` live-test files plus the v3.63.3-rewritten
  `test_u41_systemd_live_tests.py` (now a three-branch truth table
  over `HAS_SYSTEMD_BUS` × `HAS_BULKDOWNLOADER_UNIT`).
  `test_dev_suite.py` and its tier files pin the dev tools and their
  endpoints, config redaction, the invariant audit’s
  `keeper_no_resume` check, and the SQL console rejecting
  writes/PRAGMA/multi-statement while *allowing* keyword-like column
  names; `test_dispatch_chain.py` pins the `_process_one` branch
  order against the `_DISPATCH_CHAIN` mirror; `test_perf_lab.py` pins
  snapshot/audit shape, tracemalloc start/stop, the injector
  rejecting bad profiles and a second concurrent run, and that
  `purge()` frees state and deletes the `__loadtest__` queue rows;
  the v3.62.7 Tier-3 tests pin secret
  redaction in VPN tools, allowlist enforcement on filesystem walks,
  and (T15/T17) the network-calling tools failing open on
  unreachable endpoints; the v3.63.0 Tier-4 tests pin
  capture-time-redaction in `request_replay`, truncated-body replay
  refusal, the `login_flow` tag check in `delete_login_flow`, the
  `_immediate_override_` prefix contract, `feature_flags.set_flag`
  rejecting non-bool values, and `delete_flag` idempotency; the
  v3.63.1–v3.63.3 patch-arc tests pin the `storage_tier.py`
  UTC-vs-localtime fix (T47), the per-test timing 4-tuple +
  `schema_version=2` JSON (T49), the `test_timing` dev route shape
  (T50), the `regenerate_goldens.py` `--apply`-requires-`--reason`
  contract and append-only audit log (T51), the `_env.py` 8s timeout
- retry (T54, with the literal `timeout=2)` / `timeout=2,` grep
  guard), the live-test harness per-check timeout (T55), and the
  `_unit_exists` gate ordering inside `l27_survives_logout` (T56,
  source-grep regression test). At v3.63.4:
  `test_u44_l34_route_smoke.py` pins (a) the `_L34_STREAMING_SKIP`
  set’s existence + that `/api/stream` is in it, (b) a source-grep
  that L34’s target filter references the set
  (`not in _L34_STREAMING_SKIP` — gate can’t be silently deleted by
  leaving only the constant), (c) a source-grep that L34’s loop
  discriminates on `rstatus is not None` (4xx-is-OK fix can’t be
  silently reverted), and (d) functional tests via a fake Context
  that L34 doesn’t GET streaming routes, treats 4xx as wired
  (PASS), still FAILs on 5xx-via-HTTPError, and still FAILs on
  truly-unreachable routes where `rstatus is None`. At v3.63.6, six
  new test files pin the release’s invariants: `test_u45_capture_sh_shipped.py`
  (7 contracts — capture.sh exists at repo root, parses under `bash -n`,
  no executable `create_app`, L3 in `LIVE_IDS`, `--per-check-timeout`
  ≥ 90, with the create_app contract filtering comment lines so the
  header’s history-explaining block is preserved), `test_u46_vpn_kill_switch_probe.py`
  (24 contracts — clean module import with no side effects,
  `--apply` requires `--reason`, modes mutually exclusive, plan = 3
  ufw commands, both-up detection is ambiguous), `test_u47_widgets_data_psutil.py`
  (8 contracts — `psutil` pinned in `requirements.txt`, `cpu_ram`
  renderer null-guards `cpu_pct`/`ram_pct`), `test_u48_widgets_audit.py`
  (10 contracts — no executable `disk_free_gb()` no-arg call, disk-
  free outer except logs to stderr, `gpu` in `VALID_WIDGET_IDS`, GPU
  collector fails open across three missing/error/malformed
  scenarios, `nvidia-smi` query includes all four required fields +
  csv format), `test_u49_workers_total_honest.py` (4 contracts — no
  `total_cap or 16` in executable code, `workers_total` is `None` on
  empty fleet, renderer null-guards), and `test_u50_widget_backfills.py`
  (9 contracts — `cookies_oldest` reuses `doctor.cookie_freshness`
  and picks max age, `eta_clear` says `"now"` on empty queue and is
  absent on zero throughput).

-----

## CODEBASE FACTS THAT WASTE GREP TIME

- `safe_dest` lives in `detect.py`, not `fname.py`.
- `ManualLoginSession` stores its config as `self._config`, not
  `self.config` (rest of the codebase uses `self.config`).
- `score` means different things per module: in `heuristic_scoring`
  it’s a composite (tier + bonuses); in `detect`/`_all_candidates` it
  is pixel height. `_apply_quality_preference` relies on pixel height.
- Dynamic modals use `showOverlayModal(id, html)` /
  `closeOverlayModal(id)` — there is no `showModal()`. The singular
  `#modal` element is the add/edit-site form only.
- The frontend `fetch` wrapper auto-injects `X-CSRF-Token` for unsafe
  methods on `/api/` paths — do not add it manually.
- `db_stats` returns nested `{counts:{status:n}, bytes:N}`; the
  frontend `_flatten()` helper in `loadStats()` depends on that shape.
- Import-format detection precedence is fixed:
  `bitwarden_json → 1pif → lastpass_csv → 1password_csv → chrome_csv → generic_csv`. The LastPass parser needs a distinguishing column
  (`extra`/`grouping`/`fav`/`totp`) to avoid false-matching Chrome CSV.
- `login_templates_data.py::LOGIN_TEMPLATES` is a **list** of 27 login
  templates (not a dict). Lookup goes through `get_login_template()` /
  `list_login_templates()`.

### `do_not_auto_submit` is approval-derived and default fail-closed (FOUND-6)

When `scan_blockers` detects a bot defense, captcha, or (v3.66.46+)
fingerprinting, it sets `do_not_auto_submit: True`. The approval UI
(FOUND-7, `approval_ui.js`) gates on this flag. **The safe default on
ANY detection or error is `True` (do NOT auto-submit).** The base
`deep_detect` shape carries `do_not_auto_submit: False`, but any
detected defense forces it `True` (deep_detect.py — see the
`if best and best.get("do_not_auto_submit")` gate). A future change
must NOT flip this to opt-out. Untagged load-bearing behavior — no new
`# INV` tag required, but do not "clean up" the fail-closed default.
(Provenance: this is the F12 approval-model behavior; recorded here
because DANGER_MAP previously didn't capture it.)

### `scan_blockers` detect-side surfaces —

`scan_blockers` / `deep_detect` now emit `bot_defense_systems` (named
systems via `classify_bot_defenses`) and `fingerprinting` (via
`detect_fingerprinting_signals`). These feed operator warnings and the
`do_not_auto_submit` gate.

**TRUNCATED ENTRY.** The paragraph that followed here was cut off
mid-sentence in the source document ("The live observer
(`runner._install_event_listeners`, config `detect_fingerprinting`,
**"), and it is NOT recoverable from git history -- this repository
has a single initial-import commit, so no earlier revision of this
file exists. Do not read the absence as "no invariant here".
Re-derive from source: the live observer is
`bulk_downloader/runner_telemetry.py::_install_event_listeners`
(called from `runner.py`), gated on the config key
`detect_fingerprinting` (read at `runner_telemetry.py` ~lines 171 and
212), with the classifier in `bulk_downloader/fp_detect.py`.

### Session resilience: `_check_redirect` AUTH_BODY_RE — reused invariant surface (v3.66.46)

`_check_redirect` now also classifies in-place login walls
(`AUTH_BODY_RE`) and feeds the EXISTING `_handle_auth_required`
relogin+re-queue path. `AUTH_BODY_RE` is tuned to NOT fire on
logged-in nav (e.g. "Log out" links). If broadening it further,
preserve that precision or it will loop on working pages. The retry
cap is the existing `_handle_auth_required` accounting — do NOT add a
second uncapped re-drive.

-----

**END OF FILE -- TRUNCATED.** The document originally continued past
this point: the last bytes were a bare `### ` heading opened with a
backtick and no title or body. Whatever invariant was being recorded
is lost, and it is NOT recoverable from git history (this repository
has a single initial-import commit). The empty heading has been
removed so it does not read as a section a reader merely failed to
find. Treat this file's coverage as ending here, not as complete.
