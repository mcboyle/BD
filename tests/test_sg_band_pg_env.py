import base64
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_SG_BAND_PG_ENV_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
DSN = "postgresql://fixture:fixture0@127.0.0.1:5432/legacy_fixture"
PERF = "tests/test_inmemory_sqlite_fixture.py::test_memory_database_is_faster_than_disk_baseline"


def candidate_source(name):
    supplied = Path(CANDIDATE)
    assert supplied.is_absolute() and supplied.is_file() and os.access(supplied, os.X_OK), "supplied candidate must exist and be executable"
    path = Path(CANDIDATE).with_name(name)
    assert path.is_absolute() and path.is_file() and os.access(path, os.X_OK), "candidate must exist and be executable"
    return path.read_text()


def test_explicit_dry_run_never_launches_a_band():
    candidate_source("bd-band-remote.sh")
    result = subprocess.run(["bash", CANDIDATE], env=dict(os.environ, BD_SG_BAND_PG_ENV_DRY_RUN="1"), capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 64, "PG-DRY-RUN-FALSE-GREEN"
    assert "PG-ENV DRY-RUN:" in result.stdout
    assert "no SSH or DB commands executed" in result.stdout


@pytest.fixture
def band(tmp_path):
    source = candidate_source("bd-band-remote.sh")
    remote = source.split("<<'REMOTE_SCRIPT'", 1)[1].split("\n", 1)[1].split("\nREMOTE_SCRIPT", 1)[0]
    # Keep the same defect probes executable against the retained r1 source.
    helpers = source[source.index("_bd_pg_ram_prepare() {"):source.index("PG_RAM_NAME=''\ntrap _bd_pg_ram_on_exit EXIT")] if "_bd_pg_ram_prepare() {" in source else ""
    cleanup = remote[remote.index("cleanup() {"):remote.index('cd "$wt" || exit 82')]
    body = remote.split('cd "$wt" || exit 82\n', 1)[1]
    config = tmp_path / ".config/bd"
    config.mkdir(parents=True)
    pg = config / "pg-test.env"
    pg.write_text(f"export MOD3_PG_TEST_DSN={shlex.quote(DSN)}\n")
    pg.chmod(0o600)
    (tmp_path / "BulkDownloader").mkdir()
    (tmp_path / "BulkDownloader/.env").write_text("MOD3_PG_DSN=legacy-host-product\n")
    (tmp_path / "tests").mkdir()
    for name in ("test_plain.py", "test_row875_pgvector_hnsw.py", "test_v3_66_804_mod3_cutover.py"):
        (tmp_path / "tests" / name).write_text("def test_fixture(): pass\n")
    envfile_source = Path(os.environ.get("BD_SG_BAND_ENVFILE_SOURCE", "bulk_downloader/_envfile.py")).resolve()
    assert envfile_source.is_file(), "real envfile loader fixture required"
    (tmp_path / "venv/bin").mkdir(parents=True)
    python = tmp_path / "venv/bin/python"
    python.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
if sys.argv[1:2] == ['-c'] and 'urlsplit' in sys.argv[2] and os.environ.get('STUB_REDACTOR_FAIL'):
    sys.exit(23)
if sys.argv[1:3] == ['-m', 'pytest']:
    import runpy
    runpy.run_path(os.environ['ENVFILE_SOURCE'])['load_envfile']()
    with Path(os.environ['STUB_LOG']).open('a') as f:
        f.write(json.dumps({'pytest': sys.argv[3:], 'mod3': os.environ.get('MOD3_PG_TEST_DSN'), 'vector': os.environ.get('PGVECTOR_DSN'), 'product': os.environ.get('MOD3_PG_DSN')}) + '\\n')
    # Isolate filter failure from a child's incidental stdout BrokenPipe.
    if not os.environ.get('STUB_REDACTOR_FAIL'): print('stub pytest ran')
    if os.environ.get('STUB_ECHO_DSN'): print(os.environ.get('MOD3_PG_TEST_DSN', ''))
    sys.exit(int(os.environ.get('STUB_TEST_RC', '0')))
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
    python.chmod(0o755)
    package = tmp_path / "psycopg"
    package.mkdir()
    (package / "__init__.py").write_text('''import json, os
from pathlib import Path
def record(item):
    with Path(os.environ['STUB_LOG']).open('a') as f:
        f.write(json.dumps(item) + '\\n')
class Query(str):
    def format(self, *args): return Query(str(self).format(*args))
class sql:
    SQL = Query
    @staticmethod
    def Identifier(name): return '"' + name + '"'
class conninfo:
    @staticmethod
    def conninfo_to_dict(dsn): return {'dbname': 'legacy_fixture'}
    @staticmethod
    def make_conninfo(dsn='', **kwargs): return 'dbname=' + kwargs['dbname']
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, params=None):
        record({'sql': str(query), 'params': params})
        if str(query).startswith('CREATE DATABASE') and os.environ.get('STUB_DENY_CREATE'):
            raise PermissionError('fixture denies CREATE DATABASE')
        if str(query).startswith('CREATE EXTENSION') and os.environ.get('STUB_DENY_EXTENSION'):
            raise PermissionError('fixture denies CREATE EXTENSION')
        if str(query).startswith('DROP DATABASE') and os.environ.get('STUB_DENY_DROP'):
            raise PermissionError('fixture denies DROP DATABASE')
        return self
    def fetchone(self): return None if os.environ.get('STUB_NO_VECTOR') else (1,)
    def close(self): pass
def connect(dsn, **kwargs):
    record({'connect': dsn})
    if os.environ.get('STUB_CONNECT_FAIL'): raise OSError('fixture network unreachable')
    return Connection()
''')
    commands = tmp_path / "commands"
    commands.mkdir()
    ssh = commands / "ssh"
    ssh.write_text("#!/bin/bash\n[ \"${STUB_SSH_FAIL:-0}\" = 1 ] && exit 255\nif [[ \"$*\" == *'bash -s -- '* ]]; then\n  args=(\"$@\"); n=${#args[@]}; exec bash -s -- \"${args[n-2]}\" \"${args[n-1]}\"\nfi\nexec bash -c \"${!#}\"\n")
    ssh.chmod(0o755)
    docker = commands / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['STUB_LOG']).open('a') as f:
    f.write(json.dumps({'docker': args}) + '\\n')
if args[:2] == ['image', 'inspect']:
    sys.exit(1 if os.environ.get('STUB_NO_IMAGE') else 0)
if args[0] == 'run':
    assert '--tmpfs' in args and '--pull=never' in args
    assert os.environ.get('POSTGRES_PASSWORD')
    print('fixture-container')
elif args[0] == 'inspect':
    if 'Ports' in args[-1] or any('Ports' in a for a in args):
        print(json.dumps({'5432/tcp': [{'HostIp': '10.0.70.83', 'HostPort': '25432'}]}))
    else:
        print('unhealthy' if os.environ.get('STUB_UNHEALTHY') else ('starting' if os.environ.get('STUB_STARTING') else 'healthy'))
        sys.exit(0)
elif args[0] == 'events':
    assert args[-1] == '{{.Action}}', 'Docker events exposes Action, not Status'
    print('health_status: healthy', flush=True)
elif args[0] == 'exec' and os.environ.get('STUB_DENY_EXTENSION'):
    sys.exit(1)
elif args[0] == 'rm' and os.environ.get('STUB_DENY_DROP'):
    sys.exit(1)
''')
    docker.chmod(0o755)
    log = tmp_path / "calls.jsonl"

    def run(selectors=("tests/test_v3_66_804_mod3_cutover.py",), **overrides):
        env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(tmp_path), STUB_LOG=str(log),
                   MOD3_PG_DSN="ambient-product", MOD3_PG_TEST_DSN="ambient-test", PGVECTOR_DSN="ambient-vector",
                   ENVFILE_SOURCE=str(envfile_source), PATH=str(commands) + os.pathsep + os.environ["PATH"])
        env.update({key: str(value) for key, value in overrides.items()})
        prefix = ("set -uo pipefail\nMODE=band; ip=fixture; slot=1; SLOTS=1; BAND_N=12; BAND_NPROC=32; "
                  "RETAIN_WORKTREES=" + str(overrides.pop("RETAIN_WORKTREES", "1")) + "; SHA=" + "a" * 40 + "\nwt=" + shlex.quote(str(tmp_path)) + "\nselectors=(" + shlex.join(selectors) + ")\n")
        prefix += "repo=" + shlex.quote(str(tmp_path)) + "; REPO=" + shlex.quote(str(tmp_path)) + "\ngit(){ return 0; }; rm(){ return 0; }; export -f git rm\n"
        # A private lock path is a fixture resource, never a fleet lock.
        executable = prefix + helpers
        if helpers:
            executable += 'trap _bd_pg_ram_on_exit EXIT\ntrap "exit 124" TERM INT HUP\n_bd_pg_ram_prepare "${selectors[@]}"\n'
        if overrides.get("STUB_ABORT_AFTER_READY"):
            executable += "kill -TERM $$\n"
        executable += '(\n' + cleanup + body.replace("/tmp/bd-battery.$slot.lock", str(tmp_path / "battery.lock")) + '\n)\nexit $?\n'
        result = subprocess.run(["bash", "-c", executable], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=False)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return run, pg


def test_fresh_database_reaches_both_consumers_and_is_dropped(band):
    run, _ = band
    result, calls = run(("tests/test_row875_pgvector_hnsw.py",))
    assert result.returncode == 0, result.stdout + result.stderr
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1
    assert children[0]["mod3"] != DSN, "PG-BAND-PERSISTENT-DB: child inherited the legacy database"
    assert "10.0.70.83:25432/bd_band_" in children[0]["mod3"]
    assert children[0]["vector"] == children[0]["mod3"]
    assert children[0]["product"] == "", "PG-HOST-ENVFILE-BLEED"
    commands = [call["docker"] for call in calls if "docker" in call]
    runs = [args for args in commands if args[0] == "run"]
    drops = [args for args in commands if args[0] == "rm"]
    assert len(runs) == len(drops) == 1, "PG-CONTAINER-LIFECYCLE"
    assert "--tmpfs" in runs[0] and "--pull=never" in runs[0]
    assert drops[0][-1] == runs[0][runs[0].index("--name") + 1]
    assert any(args[0] == "exec" and "CREATE EXTENSION vector" in args for args in commands)
    assert DSN not in result.stdout + result.stderr


def test_two_invocations_never_share_database(band):
    run, _ = band
    first, calls = run()
    second, all_calls = run()
    assert first.returncode == second.returncode == 0
    names = [urlsplit(call["mod3"]).path for call in all_calls if "pytest" in call]
    assert len(names) == 2 and names[0] != names[1], "PG-BAND-REUSED-DB"
    assert len([call for call in calls if "pytest" in call]) == 1


@pytest.mark.parametrize("failure", ["STUB_NO_IMAGE", "STUB_UNHEALTHY", "STUB_DENY_EXTENSION", "STUB_SSH_FAIL"])
def test_unprovisioned_pg_stays_unknown_but_other_tests_run(band, failure):
    run, _ = band
    result, calls = run(("tests/test_row875_pgvector_hnsw.py", "tests/test_plain.py"), **{failure: 1})
    assert result.returncode == 89, "PG-NIGHTLY-BLIND: missing capability blocked unrelated tests"
    assert "PG-ENV UNKNOWN tests/test_row875_pgvector_hnsw.py" in result.stdout
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1, "PG-NIGHTLY-BLIND: unrelated gate never executed"
    assert "tests/test_plain.py" in children[0]["pytest"]
    assert "tests/test_row875_pgvector_hnsw.py" not in children[0]["pytest"]
    assert children[0]["mod3"] == children[0]["product"] == ""



@pytest.mark.parametrize("selectors,child_rc,expected", [
    (("tests/test_row875_pgvector_hnsw.py", "tests/test_plain.py"), 0, 89),
    (("tests",), 0, 89),
    (("tests/test_row875_pgvector_hnsw.py", "tests/test_plain.py"), 1, 1),
])
def test_admission_failure_keeps_unrelated_coverage(band, selectors, child_rc, expected):
    run, _ = band
    result, calls = run(selectors, STUB_CONNECT_FAIL=1, STUB_TEST_RC=child_rc)
    assert result.returncode == expected, "PG-ADMISSION-BLIND: failed query blocked unrelated tests"
    assert "PG-ENV UNKNOWN tests/test_row875_pgvector_hnsw.py" in result.stdout
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1, "PG-ADMISSION-BLIND: unrelated gate never executed"
    if selectors == ("tests",):
        assert "--ignore=tests/test_row875_pgvector_hnsw.py" in children[0]["pytest"]
    else:
        assert "tests/test_plain.py" in children[0]["pytest"]
        assert "tests/test_row875_pgvector_hnsw.py" not in children[0]["pytest"]
    assert children[0]["mod3"] == children[0]["vector"] == children[0]["product"] == ""
    assert any("connect" in call for call in calls), "PG-ADMISSION-NOT-PROBED"
    assert any(call.get("docker", [None])[0] == "rm" for call in calls)


def test_new_postgres_readiness_event_precedes_tests(band):
    run, _ = band
    result, calls = run(STUB_STARTING=1)
    assert result.returncode == 0, result.stdout + result.stderr
    events = [index for index, call in enumerate(calls) if call.get("docker", [""])[0] == "events"]
    children = [index for index, call in enumerate(calls) if "pytest" in call]
    assert len(events) == len(children) == 1 and events[0] < children[0]


def test_pytest_output_cannot_disclose_fresh_database_credentials(band):
    run, _ = band
    result, calls = run(STUB_ECHO_DSN=1)
    assert result.returncode == 0
    child = next(call for call in calls if "pytest" in call)
    assert child["mod3"] not in result.stdout + result.stderr, "PG-DSN-LOG-LEAK"
    assert "<redacted-PG-DSN>" in result.stdout


def test_redaction_failure_cannot_report_green(band):
    run, _ = band
    result, _ = run(STUB_REDACTOR_FAIL=1)
    assert result.returncode == 90, "PG-REDACTION-FALSE-GREEN"


def test_controller_cancellation_drops_only_its_fresh_container(band):
    run, _ = band
    result, calls = run(STUB_ABORT_AFTER_READY=1)
    assert result.returncode == 124, "PG-CANCEL-FALSE-GREEN"
    commands = [call["docker"] for call in calls if "docker" in call]
    created = next(args[args.index("--name") + 1] for args in commands if args[0] == "run")
    assert [args[-1] for args in commands if args[0] == "rm"] == [created], "PG-CANCEL-LEAK"
    assert not [call for call in calls if "pytest" in call]


def test_directory_band_keeps_non_pg_tests_and_names_unknown_files(band):
    run, _ = band
    result, calls = run(("tests",), STUB_NO_IMAGE=1)
    assert result.returncode == 89
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1
    assert "tests" in children[0]["pytest"]
    for name in ("test_row875_pgvector_hnsw.py", "test_v3_66_804_mod3_cutover.py"):
        assert f"PG-ENV UNKNOWN tests/{name}" in result.stdout
        assert f"--ignore=tests/{name}" in children[0]["pytest"]
    assert "--ignore=tests/test_plain.py" not in children[0]["pytest"]


def test_pg_capability_unknown_cannot_hide_genuine_failure(band):
    run, _ = band
    result, calls = run(("tests/test_row875_pgvector_hnsw.py", "tests/test_plain.py"), STUB_NO_IMAGE=1, STUB_TEST_RC=1)
    assert result.returncode == 1, "PG-UNKNOWN-HID-GENUINE-RED"
    assert len([call for call in calls if "pytest" in call]) == 1


def test_missing_pg_config_still_provisions_ram_database(band):
    run, pg = band
    pg.unlink()  # owned scratch fixture, not a worktree
    result, calls = run()
    assert result.returncode == 0, "PG-HOST-CONFIG-DEPENDENCY"
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1
    assert children[0]["product"] == "", "PG-HOST-ENVFILE-BLEED"
    assert "bd_band_" in children[0]["mod3"]


def test_missing_host_config_and_service_still_execute_non_pg_gate(band):
    run, pg = band
    pg.unlink()
    result, calls = run(("tests/test_v3_66_804_mod3_cutover.py", "tests/test_plain.py"), STUB_NO_IMAGE=1)
    assert result.returncode == 89, "PG-NIGHTLY-BLIND: missing host config blocked unrelated gate"
    assert len([call for call in calls if "pytest" in call]) == 1, "PG-NIGHTLY-BLIND: zero unrelated gates ran"


def test_real_host_envfile_cannot_rearm_product_database(band):
    run, _ = band
    result, calls = run(("tests/test_plain.py",))
    assert result.returncode == 0
    children = [call for call in calls if "pytest" in call]
    assert len(children) == 1
    assert children[0]["product"] == "", "PG-HOST-ENVFILE-BLEED"


def test_failed_pytest_keeps_failure_and_still_drops_own_database(band):
    run, _ = band
    result, calls = run(STUB_TEST_RC=19)
    assert result.returncode == 19
    assert sum(call.get("docker", [""])[0] == "rm" for call in calls) == 1
    assert "BAND-ENV workers=12" in result.stdout


@pytest.mark.parametrize("retained", [0, 1])
def test_cleanup_failure_cannot_report_green(band, retained):
    run, _ = band
    result, calls = run(STUB_DENY_DROP=1, RETAIN_WORKTREES=retained)
    assert result.returncode == 90, "PG-CLEANUP-FALSE-GREEN"
    assert "fresh database cleanup failed" in result.stderr
    assert len([call for call in calls if "pytest" in call]) == 1


@pytest.mark.parametrize("workers,reason,expected", [(12, "in-memory speedup ratio 1.42x failed 4x-6x threshold", 4), (1, "in-memory speedup ratio 1.42x failed 4x-6x threshold", 1), (12, "unrelated genuine assertion", 1)])
def test_perf_env_classification_requires_own_cause_and_contention(tmp_path, workers, reason, expected):
    source = candidate_source("bd-stale-gate-cron.sh")
    parser = source[source.index("failure_causes(){"):source.index("live_services_present(){")]
    classifier = source[source.index("  local genuine_failed=0"):source.index('  cat > "$REPORT" <<EOF')]
    full = tmp_path / "band.log"
    full.write_text(f"BAND-ENV workers={workers}\n___ test_memory_database_is_faster_than_disk_baseline ___\n{PERF.split('::')[0]}:210: AssertionError\nE AssertionError: {reason}\nFAILED {PERF}\n")
    report = tmp_path / "report"
    prefix = "set -uo pipefail\nfull=" + shlex.quote(str(full)) + "\nREPORT=" + shlex.quote(str(report)) + "\nhead=" + "a" * 40 + "\ndeclare -A pg_unavailable=(); ts=fixture; wt=" + shlex.quote(str(tmp_path)) + "\nsha12(){ printf %.12s \"$1\"; }; live_services_present(){ return 0; }\n"
    executable = prefix + parser + "classify(){\n" + classifier + '\n[ "$genuine_failed" = 1 ] && return 1; return 99; }\nclassify\n'
    result = subprocess.run(["bash", "-c", executable], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 4:
        assert "UNKNOWN-env-contended-band" in Path(str(report) + ".log").read_text()


@pytest.mark.parametrize("raw_rc,pytest_rc,expected,history_plain", [(89, 0, 4, True), (1, 1, 1, False), (89, None, 4, False), (90, 0, 4, False)])
def test_nightly_preserves_non_pg_visibility_and_unknown_history(tmp_path, raw_rc, pytest_rc, expected, history_plain):
    source = candidate_source("bd-stale-gate-cron.sh")
    body = source[source.index("run(){"):source.index('if [ "$MODE" = selftest ]; then')]
    parser = source[source.index("failure_causes(){"):source.index("live_services_present(){")]
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for name in ("test_plain.py", "test_row875_pgvector_hnsw.py"):
        (repo / "tests" / name).write_text("def test_fixture(): pass\n")
    full = "PG-ENV UNKNOWN tests/test_row875_pgvector_hnsw.py (fixture unavailable)\n"
    if pytest_rc is not None:
        full += f"BAND-ENV pytest-rc={pytest_rc}\n"
    if raw_rc == 1:
        full += "FAILED tests/test_plain.py::test_fixture - AssertionError: genuine regression\n"
    remote = tmp_path / "remote"
    remote.write_text("#!/bin/bash\ncat <<'FIXTURE_LOG'\n" + full + f"FIXTURE_LOG\nexit {raw_rc}\n")
    remote.chmod(0o755)
    hist = tmp_path / "hist"
    report = tmp_path / "report"
    save = source[source.index("save_hist(){"):source.index("# Fold every landed train")]
    prefix = "set -u\nMODE=nightly; STALE_TRAINS=3; REPO=" + shlex.quote(str(repo)) + "\nHIST=" + shlex.quote(str(hist)) + "\nREPORT=" + shlex.quote(str(report)) + "\nREMOTE=" + shlex.quote(str(remote)) + "\nSAY=true; PM_SEAT_FILE=/dev/null\n"
    prefix += "load_hist(){ declare -gA LAST=(); }; absorb_trains(){ return 0; }; pin_worktree(){ echo \"$REPO\"; }; git(){ printf '%s\\n' " + "a" * 40 + "; }; sha12(){ printf %.12s \"$1\"; }; live_services_present(){ return 0; }\n"
    result = subprocess.run(["bash", "-c", prefix + save + parser + body + "run\n"], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == expected, "PG-NIGHTLY-HISTORY: " + result.stdout + result.stderr
    history = hist.read_text()
    assert ("tests/test_plain.py" in history) == history_plain, "PG-NIGHTLY-HISTORY: covered gate did not advance"
    assert "tests/test_row875_pgvector_hnsw.py" not in history, "PG-NIGHTLY-FALSE-COVERAGE"
    if raw_rc == 1:
        assert "FAILED tests/test_plain.py::test_fixture" in report.read_text(), "PG-NIGHTLY-HID-GENUINE-RED"


def test_stdin_tokens_preserve_dsn_and_selector_without_argv_injection():
    source = candidate_source("bd-band-remote.sh")
    encode = source[source.index("  encoded=()\n"):source.index("  # O1647: THE TOKENS")]
    remote = source.split("<<'REMOTE_SCRIPT'", 1)[1].split("\n", 1)[1].split("\nREMOTE_SCRIPT", 1)[0]
    decode = remote[:remote.index("# H469: slots")]
    secret = "postgresql://fixture:fake-secret@10.0.70.83:25432/bd_band_fixture"
    selector = "tests/test_plain.py::test_literal; touch MUST_NOT_EXIST"
    prefix = "set -uo pipefail\nSHA=" + "a" * 40 + "; BAND_REF=refs/heads/bd-band/" + "a" * 40 + "; ip=fixture; BAND_SLOTS=1; MODE=band; SHIPPED=none; RETAIN_WORKTREES=1; BD_PRECUT_FAST=1; PG_RAM_STATE=ready; PG_RAM_DSN=" + shlex.quote(secret) + "\nset -- " + shlex.quote(selector) + "\n"
    result = subprocess.run(["bash", "-c", prefix + encode + 'set -- "${encoded[@]}"\n' + decode + 'printf "%s\\n%s\\n" "$PG_RAM_DSN" "$1"\n'], capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0, "PG-STDIN-TOKEN-DRIFT: " + result.stderr
    assert result.stdout.splitlines() == [secret, "b" + base64.b64encode(selector.encode()).decode()]
