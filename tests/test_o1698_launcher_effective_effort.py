import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_LAUNCHER_EFFECTIVE_EFFORT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_LAUNCHER_EFFECTIVE_EFFORT") != "1" or not CANDIDATE,
    reason="candidate opt-in required",
)
BASE_SHA256 = "2b85b382e514587440d023e75c678809869f4d77918efb86951b9efa09cde444"


@pytest.fixture
def scripts():
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file(), f"candidate missing: {CANDIDATE}"
    assert os.access(candidate, os.X_OK), f"candidate not executable: {CANDIDATE}"
    base = candidate.parent / "bd-launch-role.sh.pre"
    assert hashlib.sha256(base.read_bytes()).hexdigest() == BASE_SHA256
    return base, candidate


def table_effort(script, role, pool="A"):
    definitions = re.findall(r"(?ms)^effort_for\(\) \{.*?^\}", script.read_text())
    assert len(definitions) == 1, f"O1698-TABLE-CENSUS: {len(definitions)}"
    result = subprocess.run(
        ["bash", "-c", 'POOL=$2\n' + definitions[0] + '\neffort_for "$1"', "table", role, pool],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def launch(tmp_path, scripts):
    base, _ = scripts
    for directory in ("prompts", "roles", "settings", "work", "state", "cache", "temp"):
        (tmp_path / directory).mkdir()
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}\n')
    (tmp_path / "settings" / "settings.json").write_text(
        '{"autoCompactWindow": 500000, "autoCompactEnabled": true}\n'
    )
    halt = tmp_path / "halt.sh"
    halt.write_text("#!/bin/bash\nexit 0\n")
    claim = tmp_path / "claim.sh"
    claim.write_text("#!/bin/bash\n[ \"$1\" = who ] || exit 92\n")
    claim.chmod(0o755)
    remote = tmp_path / "remote.sh"
    remote.write_text('BD_HOST=""\nbd_take_host_arg() { BD_ARGV=("$@"); }\n')
    bash_env = tmp_path / "bash-env.sh"
    bash_env.write_text(r'''
_fixture_path() {
  case "$1" in
    */role-systemprompts) printf '%s' "$FIXTURE_ROOT/prompts" ;;
    */role-systemprompts/*) printf '%s' "$FIXTURE_ROOT/prompts/${1##*/}" ;;
    */config/*.frozen.systemprompt) printf '%s' "$FIXTURE_ROOT/absent/${1##*/}" ;;
    */mcp-seat-*.json) printf '%s' "$FIXTURE_ROOT/mcp.json" ;;
    *) printf '%s' "$1" ;;
  esac
}
[() {
  local value; local -a mapped=()
  for value in "$@"; do mapped+=("$(_fixture_path "$value")"); done
  builtin [ "${mapped[@]}"
}
mkdir() {
  local value; local -a mapped=()
  for value in "$@"; do mapped+=("$(_fixture_path "$value")"); done
  printf 'mkdir\n' >> "$FIXTURE_ROOT/boundaries.log"
  command mkdir "${mapped[@]}"
}
mktemp() {
  local value; local -a mapped=()
  for value in "$@"; do mapped+=("$(_fixture_path "$value")"); done
  printf 'mktemp\n' >> "$FIXTURE_ROOT/boundaries.log"
  command mktemp "${mapped[@]}"
}
mv() {
  local value; local -a mapped=()
  for value in "$@"; do mapped+=("$(_fixture_path "$value")"); done
  printf 'mv\n' >> "$FIXTURE_ROOT/boundaries.log"
  command mv "${mapped[@]}"
}
tmux() { printf 'O1698-REAL-SEAT-FORBIDDEN\n' >&2; exit 91; }
claude() { printf 'O1698-REAL-SEAT-FORBIDDEN\n' >&2; exit 91; }
''')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BD_", "CLAUDE_", "ANTHROPIC_"))
           and key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")
           and not key.startswith("BASH_FUNC_")}
    env.update({
        "HOME": str(tmp_path / "state"), "TMPDIR": str(tmp_path / "temp"),
        "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
        "XDG_CACHE_HOME": str(tmp_path / "cache"), "BASH_ENV": str(bash_env),
        "FIXTURE_ROOT": str(tmp_path), "BD_LIMITS_LIB": str(base.parent / "lib_limits.sh.fixture"),
        "BD_CODEX_MODEL_LIB": str(base.parent / "lib_codex_model.sh.fixture"),
        "BD_REMOTE_SEAT_LIB": str(remote), "BD_HALT_CHECK": str(halt),
        "BD_LAUNCH_ROLE_CLAIM": str(claim), "BD_LAUNCH_SAY": str(claim),
        "BD_LAUNCH_CODEX": str(claim),
        "BD_ROLE_CARDINALITY": str(tmp_path / "cardinality.tsv"),
        "BD_LAUNCH_ROLE_DIR": str(tmp_path / "roles"), "BD_LAUNCH_WORKDIR": str(tmp_path / "work"),
        "BD_LAUNCH_SETTINGS_DIR": str(tmp_path / "settings"),
        "BD_LAUNCH_OVERRIDE_LOG": str(tmp_path / "override.log"),
        "BD_LAUNCH_HEADLESS": "1",
    })

    def run(script, role, override="", pool="A"):
        usage_role = "usage" if pool == "codex" else f"usage-{pool.lower()}"
        fixture_role = (usage_role if role == "usage" or role.startswith("usage-") or role == "login"
                        else "usage-b" if role == "b-login" else role)
        (tmp_path / "roles" / f"{fixture_role}.prompt").write_text("isolated launcher fixture\n")
        (tmp_path / "cardinality.tsv").write_text(f"{fixture_role}\tMULTI\n")
        (tmp_path / "boundaries.log").write_text("")
        run_env = dict(env)
        if override:
            run_env["BD_LAUNCH_EFFORT"] = override
        result = subprocess.run(
            ["bash", str(script), role, pool, "o1698-fixture", "--dry-run"],
            cwd=tmp_path / "work", env=run_env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 0, (
            f"O1698-DRY-RUN: rc={result.returncode}; {result.stdout}; {result.stderr}"
        )
        assert "O1698-REAL-SEAT-FORBIDDEN" not in result.stderr
        seat = "o1698-fixture" if pool == "codex" else f"o1698-fixture-{pool}"
        lines = [line for line in result.stdout.splitlines() if line.startswith(f"DRY {seat} ")]
        assert len(lines) == 1, f"O1698-DRY-CENSUS: {result.stdout}"
        effort = re.search(r"\beffort=(\w+)", lines[0])
        assert effort is not None, f"O1698-DRY-EFFORT-MISSING: {lines[0]}"
        commands = [line.removeprefix("DRY cmd: ") for line in result.stdout.splitlines()
                    if line.startswith("DRY cmd: ")]
        if pool == "codex":
            assert not commands
        else:
            assert len(commands) == 1, f"O1698-CMD-CENSUS: {result.stdout}"
            arguments = shlex.split(commands[0])
            assert arguments.count("--effort") == 1, f"O1698-EFFORT-FLAG: {commands[0]}"
            assert arguments[arguments.index("--effort") + 1] == effort[1]
        assert (tmp_path / "boundaries.log").read_text().splitlines() == ["mkdir", "mktemp", "mv"]
        assert (tmp_path / "prompts" / f"{fixture_role}.frozen.systemprompt").read_text() == "isolated launcher fixture\n"
        print(f"O1698-DRY: script={script.name} pool={pool} role={role} override={override or 'none'} effort={effort[1]}")
        return effort[1]

    return run


@pytest.mark.parametrize("role", ["lensrouter", "dispatch"])
def test_effective_effort_matches_table(scripts, launch, role):
    _, candidate = scripts
    expected = table_effort(candidate, role)
    assert expected, f"O1698-TABLE-EMPTY: {role}"
    effective = launch(candidate, role)
    assert effective == expected, f"O1698-EFFECTIVE-EFFORT: role={role} expected={expected} actual={effective}"


@pytest.mark.timeout(120)
@pytest.mark.parametrize("pool", ["A", "codex"])
def test_every_effort_role_preserves_baseline_except_dispatch_and_lensrouter(scripts, launch, pool):
    base, candidate = scripts
    definition = re.findall(r"(?ms)^effort_for\(\) \{.*?^\}", base.read_text())
    assert len(definition) == 1
    labels = re.findall(r"(?m)^\s+([a-z0-9_-]+(?:\|[a-z0-9_-]+)*)\)\s+echo\b", definition[0])
    roles = sorted({role for label in labels for role in label.split("|")})
    assert roles and {"lensrouter", "dispatch", "worker", "adjudicator", "status", "pm"} <= set(roles)
    measured = []
    for role in roles:
        before = launch(base, role, pool=pool)
        expected = "high" if pool != "codex" and role in ("lensrouter", "dispatch") else before
        after = launch(candidate, role, pool=pool)
        assert after == expected, f"O1698-EFFECTIVE-EFFORT: role={role} expected={expected} actual={after}"
        measured.append((role, before, after))
    assert len(measured) == len(roles)
    changed = set() if pool == "codex" else {"lensrouter", "dispatch"}
    assert {role for role, before, after in measured if before != after} == changed
    for role, _, after in measured:
        assert table_effort(candidate, role, pool=pool) == after, f"O1698-TABLE-ALIGNMENT: role={role} effective={after}"
    print(f"O1698-ROLE-CENSUS: pool={pool} expected={len(roles)} executed={len(measured)} changed={len(changed)}")


@pytest.mark.parametrize("script_index", [0, 1], ids=["base", "candidate"])
@pytest.mark.parametrize("role", ["lensrouter", "worker", "adjudicator", "codex-tender"])
@pytest.mark.parametrize("pool", ["A", "codex"])
def test_explicit_low_override_wins(scripts, launch, script_index, role, pool):
    effective = launch(scripts[script_index], role, "low", pool=pool)
    assert effective == "low", f"O1698-OVERRIDE-EFFORT: expected=low actual={effective}"


@pytest.mark.parametrize("script_index", [0, 1], ids=["base", "candidate"])
def test_empty_table_effort_gets_fallback(scripts, launch, script_index):
    script = scripts[script_index]
    assert table_effort(script, "codex-tender") == ""
    effective = launch(script, "codex-tender")
    assert effective == "medium", f"O1698-EMPTY-FALLBACK: expected=medium actual={effective}"


def test_base_negative_control(scripts, launch):
    base, _ = scripts
    assert table_effort(base, "lensrouter") == "high"
    with pytest.raises(AssertionError, match="O1698-EFFECTIVE-EFFORT: role=lensrouter expected=high actual=medium"):
        effective = launch(base, "lensrouter")
        assert effective == "high", f"O1698-EFFECTIVE-EFFORT: role=lensrouter expected=high actual={effective}"
