# BulkDownloader CLI Reference

Authoritative command-line interface specification for BulkDownloader core entry points, analysis frontends, and developer utilities.

---

## 1. Primary Entry Points

### 1.1 Web Daemon: `downloader_ui.py`
The primary application entry point is `downloader_ui.py`, which initializes the core backend and serves the web UI:

```bash
./venv/bin/python downloader_ui.py
```

- **Default Port:** `5555` (configurable via `sites_config.json` -> `general` -> `port`)
- **Environment Controls:**
  - `BULK_DOWNLOADER_DEBUG`: Enable verbose debug logging and Werkzeug interactive debugger.
  - `BULK_DOWNLOADER_LOG_DIR`: Target directory for application logs (defaults to `./logs`).
  - `BD_HEADLESS`: Suppress terminal banner for background daemon mode.
  - `BD_EXIT_WHEN_IDLE`: Terminate automatically when download queues drain.

*Architectural Note: BulkDownloader does not provide a `python -m bulk_downloader` module runner or a root-level `cli.py`.*

---

### 1.2 Terminal Dashboard: `bulk_downloader/cli_dashboard.py`
Headless ANSI terminal dashboard for real-time monitoring of queue status, workers, and active downloads:

```bash
./venv/bin/python -m bulk_downloader.cli_dashboard [options]
```

#### Documented Flags (`cli_dashboard`)
- `-h`, `--help`: Show CLI help and exit.
- `--api`: URL of running BulkDownloader instance (default: `http://127.0.0.1:5000`; production daemon runs on port `5555`).
- `--once`: Print a single status snapshot and exit (default: interactive live refresh).
- `--refresh`: Refresh interval in seconds (default: `2.0`).

---

## 2. Code Intelligence Frontends

All analysis frontends adhere to standard exit contracts:
- `0`: Completed with pass, advisory, or non-gating findings.
- `1`: Blocking failure, policy violation, or timeout when `--gate` is enabled.
- `2`: CLI argument or input validation error.

### 2.1 `tools/semantic_diff.py`
Abstract syntax tree and semantic diff comparison between code trees or snapshots.

```bash
python3 tools/semantic_diff.py --before-tree <old> --after-tree <new> --out SEMANTIC_DIFF.json --gate --json
```

#### Documented Flags (`semantic_diff.py`)
- `-h`, `--help`: Show help and exit.
- `--before-tree`: Path to prior source tree root.
- `--after-tree`: Path to candidate source tree root.
- `--before-snapshot`: Path to frozen snapshot file for before-state.
- `--after-snapshot`: Path to frozen snapshot file for after-state.
- `--out`: Destination artifact path (default: `SEMANTIC_DIFF.json`).
- `--check`: Assert against existing canonical artifact path.
- `--gate`: Enforce gating exit code (exit 1 on policy differences).
- `--cst-adapter`: Optional CST adapter (`none` or `libcst`, default: `none`).
- `--json`: Emit JSON payload to stdout.

---

### 2.2 `tools/reachability.py`
Static call-graph reachability analysis for routes, handlers, and security surfaces.

```bash
python3 tools/reachability.py --root . --app bulk_downloader.app:app --security-surface SURFACE.json --call-graph GRAPH.json --gate --json
```

#### Documented Flags (`reachability.py`)
- `-h`, `--help`: Show help and exit.
- `--root`: Path to repository root.
- `--app`: Flask application specifier (`module:attribute`, required).
- `--authenticated-fixture`: Test fixture specifier (`module:function`).
- `--security-surface`: Path to security surface lock JSON (required).
- `--call-graph`: Path to call graph JSON (required).
- `--deferrals`: Path to approved reachability deferrals JSON.
- `--timeout`: Maximum evaluation budget in seconds (default: `15.0`).
- `--out`: Destination artifact path (default: `REACHABILITY.json`).
- `--check`: Expected baseline artifact to assert against.
- `--gate`: Exit 1 on unreachable endpoints or surface mismatches.
- `--json`: Print JSON report to stdout.

---

### 2.3 `tools/differential_oracle.py`
Differential fuzzing and agreement oracle comparing adapters across edge test cases.

```bash
python3 tools/differential_oracle.py --root . --adapter consumer-agreement --timeout 30.0 --max-cases 1000 --gate --json
```

#### Documented Flags (`differential_oracle.py`)
- `-h`, `--help`: Show help and exit.
- `--root`: Repository root path.
- `--adapter`: Target adapter identifier (repeatable for multiple adapters).
- `--list-adapters`: List registered differential adapters and exit.
- `--corpus`: Directory containing evaluation input corpus.
- `--seed`: Deterministic pseudo-random seed (default: `0`).
- `--timeout`: Per-adapter evaluation timeout in seconds (default: `30.0`).
- `--max-cases`: Maximum test cases executed per adapter (default: `1000`).
- `--max-output-bytes`: Ceiling on generated output bytes (default: `1048576`).
- `--out`: Output artifact path (default: `DIFFERENTIAL_ORACLE.json`).
- `--check`: Validate against pre-recorded canonical artifact.
- `--gate`: Exit 1 on unallowed divergences.
- `--json`: Emit canonical JSON to stdout.

---

### 2.4 `tools/fuzz_harness.py`
Fuzzing harness for parser boundaries, redaction, and path safety.

```bash
python3 tools/fuzz_harness.py --root . --adapter redaction --seed 42 --gate --json
```

#### Documented Flags (`fuzz_harness.py`)
- `-h`, `--help`: Show help and exit.
- `--root`: Repository root path.
- `--adapter`: Target fuzzing adapter (repeatable).
- `--list-adapters`: List registered fuzzing adapters and exit.
- `--corpus`: External input corpus directory.
- `--seed`: Fuzzing seed (default: `0`).
- `--timeout`: Adapter timeout in seconds (default: `10.0`).
- `--max-cases`: Maximum fuzzing iterations (default: `1000`).
- `--max-output-bytes`: Output byte ceiling (default: `1048576`).
- `--generator`: Fuzz generator engine (`none` or `hypothesis`, default: `none`).
- `--reproducer-dir`: Directory for crash reproducers (default: `regression_corpus/reproducers`).
- `--out`: Output artifact path (default: `FUZZ_RESULTS.json`).
- `--check`: Compare against known clean fuzz baseline.
- `--gate`: Exit 1 on uncovered crashes or policy failures.
- `--json`: Emit JSON envelope to stdout.

---

## 3. Capture & Site Learning Utilities

### 3.1 `tools/capture_batch.py`
Interactive browser session capture runner for media recording and selector learning:

```bash
python tools/capture_batch.py --pairs --job site https://example.com --profile-root ./profiles
```

#### Documented Flags (`capture_batch.py`)
- `--job`: Capture job specification (`NAME URL` pair, repeatable).
- `--jobs`: Path to text file containing `name|url` lines.
- `--out-dir`: Destination directory for `.wacz` archives (default: `./captures`).
- `--profile-root`: Directory holding persistent browser profile trees.
- `--seed-profile`: Master template profile to clone for authentication state.
- `--autofill`: Enable profile credential autofill.
- `--system-chrome`: Drive system Google Chrome rather than bundled Chromium.
- `--stagger-secs`: Delay in seconds between opening browser windows (default: `1.5`).
- `--wait-timeout`: Maximum wait time in seconds for capture completion (default: `3600.0`).
- `--url-memory-file`: Shared URL and title memory database (default: `capture_url_memory.json`).
- `--sequential`: Execute jobs serially rather than in parallel.
- `--pairs`: Collect run1/run2 pairs for drift detection.

---

## 4. Framework & Intelligence Layers

### 4.1 `tools/validation_harness.py`
Phases 32, 33, 35, 37 validation suite.

```bash
python tools/validation_harness.py {consistency,benchmark,acquire,release} [subcommand-options]
```

- **Subcommands:**
  - `consistency`: Evaluate decision thresholds and consistency (`--portfolio-root`, `--epsilon`, `--out-dir`).
  - `benchmark`: Benchmark runtime and accuracy vs baseline (`--portfolio-root`, `--baseline`, `--out-dir`).
  - `acquire`: Compute evidence acquisition plan (`--portfolio-root`, `--budget`, `--out-dir`).
  - `release`: Release readiness scorecard roll-up (`--out-dir`, `--framework-scorecard`, etc.).
  - *Note: `capquality` is located in `tools/trust_intelligence.py`.*

---

### 4.2 `tools/trust_intelligence.py`
Calibration, regression, capture quality, and site family intelligence.

```bash
python tools/trust_intelligence.py {calibrate,regress,capquality,forecast,simulate,freshness,failures,impact,family} [subcommand-options]
```

- **Subcommands:**
  - `capquality`: Score capture quality and DOM completeness (`--capture`, `--out-dir`).
  - `family`: Analyze site family coverage and clustering (`--site-family-membership`, `--sites-root`, `--out-dir`).
  - `calibrate`: Assess confidence calibration reliability (`--site`, `--selector-confidence`, `--outcomes`, `--out-dir`).
  - `regress`: Verdict regression analysis (`--site`, `--old-snapshot`, `--new-snapshot`, `--out-dir`).
  - `forecast`: Drift and confidence forecasting (`--site`, `--drift-history`, etc.).
  - `simulate`: Change impact simulation (`--site`, `--automation-decision-report`, etc.).
  - `freshness`: Evidence freshness evaluation (`--site`, `--out-dir`, etc.).
  - `failures`: Failure mode taxonomy and tracking (`--site`, `--drift-history`, `--out-dir`).
  - `impact`: Downstream impact forecasting (`--site`, `--out-dir`).

---

### 4.3 `tools/ops_intelligence.py`
Operational health, trace diagnostics, and capture queues.

```bash
python tools/ops_intelligence.py {health,trace,capqueue,login,workflow,patterns} [subcommand-options]
```

- **Subcommands:**
  - `health`: Site operational health check (`--site`, `--out-dir`).
  - `trace`: Decision trace extraction (`--site`, `--out-dir`).
  - `capqueue`: Capture queue prioritization (`--sites-root`, `--out-dir`).
  - `login`: Login flow health analysis (`--site`, `--login-flow`, `--out-dir`).
  - `workflow`: Workflow flow verification (`--site`, `--workflow-flow`, `--out-dir`).
  - `patterns`: Common failure pattern synthesis (`--sites-root`, `--out-dir`).
  - *Note: `family` is located in `tools/trust_intelligence.py`.*

---

### 4.4 `tools/meta_intelligence.py`
Framework self-audit, assumptions challenge, and sustainability.

```bash
python tools/meta_intelligence.py {assumptions,explain,audit,risk,graph,maturity,blindspots,concentration,sustainability} [subcommand-options]
```

- **Subcommands:**
  - `sustainability`: Workload, queue, and maintenance sustainability (`--sites-root`, `--out-dir`).
  - `assumptions`: Challenge implicit model assumptions (`--assumption-registry`, `--out-dir`).
  - `explain`: Generate human-readable decision explanations (`--site`, `--decision-trace`, `--out-dir`).
  - `audit`: Framework self-audit and gap detection (`--decision-trace`, `--out-dir`).
  - `risk`: Cross-cutting risk register analysis (`--drift-forecasts`, `--out-dir`).
  - `graph`: Knowledge graph generation (`--out-dir`).
  - `maturity`: Subsystem maturity matrix (`--out-dir`).
  - `blindspots`: Unstudied site and selector blindspots (`--out-dir`).
  - `concentration`: Single-evidence dependency concentration (`--out-dir`).

---

### 4.5 `tools/operator_layer.py`
Operational cockpits, capacity planning, and resource bottlenecks.

```bash
python tools/operator_layer.py {autopilot,cockpit,portfolio,capacity,governance,memory,exec,resources,reviewroi,bottlenecks} [subcommand-options]
```

- **Subcommands:**
  - `bottlenecks`: Pipeline bottleneck identification (`--portfolio-root`, `--out-dir`).
  - `autopilot`: End-to-end autonomous capture chain execution (`captures`, `--axis`, `--out-dir`).
  - `cockpit`: Primary operator cockpit overview (`--portfolio-root`, `--out-dir`).
  - `portfolio`: Portfolio-wide rankings and priority (`--portfolio-root`, `--out-dir`).
  - `capacity`: Analyst review capacity modeling (`--portfolio-root`, `--minutes-per-review`, `--weekly-capacity-minutes`, `--out-dir`).
  - `governance`: Evidence policy governance check (`--artifacts-root`, `--out-dir`).
  - `memory`: URL and pattern memory inspection (`--portfolio-root`, `--out-dir`).
  - `exec`: Executive portfolio summary (`--portfolio-root`, `--out-dir`).
  - `resources`: Host resource usage profiling (`--portfolio-root`, `--out-dir`).
  - `reviewroi`: Return on review investment tracking (`--portfolio-root`, `--out-dir`).
  - *Note: `sustainability` is located in `tools/meta_intelligence.py`.*

---

## 5. Development & Verification Utilities

### 5.1 `tools/plugin_lint.py`
Validates BulkDownloader plugins against security policies, API signatures, and event contracts:

```bash
python3 tools/plugin_lint.py [--smoke] [--full-access] <target>
```

- **Arguments:**
  - `target`: Path to a plugin file (`.py`) or directory of plugins.
  - `--smoke`: Fire synthetic test events through declared hooks and processors.
  - `--full-access`: Validate permissions as if `allow_full_access` gate is active.

### 5.2 `tools/framework_dashboard.py`
Read-only web dashboard for viewing generated intelligence artifacts:

```bash
BD_FRAMEWORK_PORT=8770 BD_FRAMEWORK_REPORTS=./reports python3 tools/framework_dashboard.py
```

- **Environment Configuration:**
  - `BD_FRAMEWORK_PORT`: Port to bind HTTP service (default: `8770`).
  - `BD_FRAMEWORK_REPORTS`: Path to directory holding reports (default: `./reports`).

### 5.3 Retired Tools & Historical Subsystems
- **`tools/tasktracker_gen.py` (RETIRED):** Retired in commit `f34dc74b5` (#128). Backlog row tracking is handled by `project-knowledge/build_current_overlay.py` and toolchain scripts `bd-register-*`.
- **`bulk_downloader/__main__.py` & `cli.py` (ABSENT):** Never existed in the codebase. Use `downloader_ui.py` for server startup or `python -m bulk_downloader.cli_dashboard` for terminal monitoring.
