"""Failed safety measurements never reuse a successful measurement value.

This is one module-scoped contract across five operator/toolchain entry points.
Each failure injection is counted exactly, and each site has a successful
control whose result cannot be mistaken for the injected failure state.
"""
from __future__ import annotations

import builtins
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]


def _load_python(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _golden_tree(root: Path) -> Path:
    (root / "bulk_downloader").mkdir()
    (root / "CHANGELOG.md").write_text("fixture\n", encoding="ascii")
    golden_dir = root / "tests" / "fixtures" / "golden"
    golden_dir.mkdir(parents=True)
    (golden_dir / "sample.golden").write_bytes(b"operator edit\n")
    (golden_dir / "sample.current").write_bytes(b"new baseline\n")
    return golden_dir


def test_failed_git_measurement_refuses_golden_apply_once(tmp_path, monkeypatch, capsys):
    module = _load_python("regenerate_goldens_failure_contract", "tools/regenerate_goldens.py")
    golden_dir = _golden_tree(tmp_path)
    status_calls = 0

    def failed_status(args, **kwargs):
        nonlocal status_calls
        if "status" in args:
            status_calls += 1
            return subprocess.CompletedProcess(args, 128, "", "fixture git failure")
        return subprocess.CompletedProcess(args, 0, "abc123\n", "")

    monkeypatch.setattr(module.subprocess, "run", failed_status)
    rc = module.main([
        "--apply", "--reason", "failure contract", "--repo-root", str(tmp_path)
    ])

    assert rc == 2
    assert status_calls == 1
    assert (golden_dir / "sample.golden").read_bytes() == b"operator edit\n"
    assert "git status" in capsys.readouterr().err.lower()


def test_clean_git_measurement_still_allows_golden_apply_once(tmp_path, monkeypatch):
    module = _load_python("regenerate_goldens_clean_control", "tools/regenerate_goldens.py")
    golden_dir = _golden_tree(tmp_path)
    status_calls = 0

    def clean_status(args, **kwargs):
        nonlocal status_calls
        if "status" in args:
            status_calls += 1
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "abc123\n", "")

    monkeypatch.setattr(module.subprocess, "run", clean_status)
    rc = module.main([
        "--apply", "--reason", "clean control", "--repo-root", str(tmp_path)
    ])

    assert rc == 0
    assert status_calls == 1
    assert (golden_dir / "sample.golden").read_bytes() == b"new baseline\n"


def test_strict_python_scan_exits_on_exactly_one_unreadable_validated_file(
    tmp_path, monkeypatch, capsys
):
    module = _load_python("bdtools_sec_failure_contract", "toolchain/bin/bdtools_sec.py")
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    good = package / "good.py"
    bad = package / "bad.py"
    good.write_text("GOOD = 1\n", encoding="ascii")
    bad.write_text("BAD = 1\n", encoding="ascii")
    real_open = builtins.open
    denied_reads = 0

    def deny_one_file(file, *args, **kwargs):
        nonlocal denied_reads
        if Path(file) == bad:
            denied_reads += 1
            raise PermissionError("fixture denies bad.py")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_one_file)
    with pytest.raises(SystemExit) as stopped:
        list(module.iter_py(
            str(tmp_path), strict=True, include_tests=True, label="fixture-corpus"
        ))

    assert stopped.value.code == module.EXIT_CANNOT_EVALUATE
    assert denied_reads == 1
    assert "reason=UNREADABLE" in capsys.readouterr().err


def test_strict_python_scan_exits_on_exactly_one_failed_file_stat(
    tmp_path, monkeypatch, capsys
):
    module = _load_python("bdtools_sec_stat_contract", "toolchain/bin/bdtools_sec.py")
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    good = package / "good.py"
    denied = package / "denied.py"
    good.write_text("GOOD = True\n", encoding="ascii")
    denied.write_text("DENIED = True\n", encoding="ascii")
    real_stat = module.os.stat
    denied_stats = 0

    def fail_one_stat(path, *args, **kwargs):
        nonlocal denied_stats
        if Path(path) == denied:
            denied_stats += 1
            raise PermissionError("fixture denies denied.py stat")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "stat", fail_one_stat)
    with pytest.raises(SystemExit) as stopped:
        list(module.iter_py(
            str(tmp_path), strict=True, include_tests=True, label="fixture-corpus"
        ))

    assert stopped.value.code == module.EXIT_CANNOT_EVALUATE
    assert denied_stats == 1
    assert "reason=UNREADABLE" in capsys.readouterr().err


def test_strict_python_scan_yields_the_exact_readable_validated_set(tmp_path):
    module = _load_python("bdtools_sec_clean_control", "toolchain/bin/bdtools_sec.py")
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    for name in ("a.py", "b.py"):
        (package / name).write_text(f"NAME = {name!r}\n", encoding="ascii")

    rows = list(module.iter_py(
        str(tmp_path), strict=True, include_tests=True, label="fixture-corpus"
    ))

    assert [row[1] for row in rows] == [
        "bulk_downloader/a.py",
        "bulk_downloader/b.py",
    ]
    assert len(rows) == 2


def test_strict_python_scan_does_not_refilter_a_validated_parent_name(tmp_path):
    module = _load_python("bdtools_sec_parent_control", "toolchain/bin/bdtools_sec.py")
    work = tmp_path / "venv-parent-name"
    package = work / "bulk_downloader"
    package.mkdir(parents=True)
    (package / "kept.py").write_text("KEPT = True\n", encoding="ascii")

    rows = list(module.iter_py(
        str(work), strict=True, include_tests=True, label="fixture-corpus"
    ))

    assert [row[1] for row in rows] == ["bulk_downloader/kept.py"]
    assert len(rows) == 1


def test_strict_python_scan_canonicalizes_one_symlinked_work_root(tmp_path):
    module = _load_python("bdtools_sec_symlink_control", "toolchain/bin/bdtools_sec.py")
    real_work = tmp_path / "real-work"
    package = real_work / "bulk_downloader"
    package.mkdir(parents=True)
    (package / "kept.py").write_text("KEPT = True\n", encoding="ascii")
    alias = tmp_path / "work-alias"
    alias.symlink_to(real_work, target_is_directory=True)

    rows = list(module.iter_py(
        str(alias), strict=True, include_tests=True, label="fixture-corpus"
    ))

    assert [row[1] for row in rows] == ["bulk_downloader/kept.py"]
    assert len(rows) == 1


def test_strict_python_scan_reports_one_walk_error_as_unreadable(
    tmp_path, monkeypatch, capsys
):
    module = _load_python("bdtools_sec_walk_contract", "toolchain/bin/bdtools_sec.py")
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    (package / "visible.py").write_text("VISIBLE = True\n", encoding="ascii")
    walk_calls = 0

    def denied_walk(root, **kwargs):
        nonlocal walk_calls
        walk_calls += 1
        if onerror := kwargs.get("onerror"):
            onerror(PermissionError("fixture denies a descendant"))
        return iter(())

    monkeypatch.setattr(module.os, "walk", denied_walk)
    with pytest.raises(SystemExit) as stopped:
        module.require_corpus(package, patterns=("*.py",), label="fixture-corpus")

    assert stopped.value.code == module.EXIT_CANNOT_EVALUATE
    assert walk_calls == 1
    assert "reason=UNREADABLE" in capsys.readouterr().err


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="ascii")
    path.chmod(0o755)


def _sast_fixture(root: Path) -> tuple[Path, Path]:
    (root / "tools").mkdir(parents=True)
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "bulk_downloader").mkdir()
    (root / "node_modules" / "eslint").mkdir(parents=True)
    (root / "requirements.txt").write_text("fixture==1\n", encoding="ascii")
    (root / "preflight.py").write_text(
        "print('NO CRITICAL FINDINGS')\n", encoding="ascii"
    )
    (root / "tools" / "sast.sh").write_bytes((_REPO / "tools" / "sast.sh").read_bytes())
    (root / "tools" / "sast.sh").chmod(0o755)
    (root / "scripts" / "lib" / "python_resolve.sh").write_text(
        "bd_resolve_python() {\n"
        "  BD_PYTHON_RESOLVED=\"$1/fake-bin/python\"\n"
        "  return 0\n"
        "}\n",
        encoding="ascii",
    )
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    scanner_log = root / "scanner.log"
    py = shlex.quote(sys.executable)
    _write_executable(
        fake_bin / "python",
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "if [ \"${1:-}\" = \"-m\" ]; then\n"
        "  mod=${2:-}; shift 2\n"
        "  case \"$mod\" in\n"
        "    pip) exit 0 ;;\n"
        "    bandit)\n"
        "      printf 'bandit\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "      out=\"\"; prev=\"\"\n"
        "      for arg in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=\"$arg\"; prev=\"$arg\"; done\n"
        "      if [ -n \"$out\" ]; then\n"
        "        if [ \"${SAST_INCOMPLETE_SCANNER:-}\" = bandit ]; then\n"
        "          printf '{\"results\":[]}' > \"$out\"\n"
        "        elif [ \"${SAST_FINDING_SCANNER:-}\" = bandit ]; then\n"
        "          printf '{\"results\":[{}],\"errors\":[]}' > \"$out\"\n"
        "        else\n"
        "          printf '{\"results\":[],\"errors\":[]}' > \"$out\"\n"
        "        fi\n"
        "      fi\n"
        "      if [ \"${SAST_FINDING_SCANNER:-}\" = bandit ] || "
        "[ \"${SAST_STATUS_MISMATCH:-}\" = bandit ]; then exit 1; fi\n"
        "      exit 0 ;;\n"
        "    semgrep)\n"
        "      printf 'semgrep\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "      [ \"${SAST_FAIL_SEMGREP:-0}\" = 0 ] || exit 7\n"
        "      out=\"\"; prev=\"\"\n"
        "      for arg in \"$@\"; do [ \"$prev\" = \"--output\" ] && out=\"$arg\"; prev=\"$arg\"; done\n"
        "      if [ -n \"$out\" ]; then\n"
        "        if [ \"${SAST_INCOMPLETE_SCANNER:-}\" = semgrep ]; then\n"
        "          printf '{\"results\":[]}' > \"$out\"\n"
        "        elif [ \"${SAST_INVALID_SEMGREP:-0}\" = 1 ]; then\n"
        "          printf '{\"results\":{},\"errors\":[]}' > \"$out\"\n"
        "        elif [ \"${SAST_FINDING_SCANNER:-}\" = semgrep ]; then\n"
        "          printf '{\"results\":[{}],\"errors\":[]}' > \"$out\"\n"
        "        else\n"
        "          printf '{\"results\":[],\"errors\":[]}' > \"$out\"\n"
        "        fi\n"
        "      fi\n"
        "      if [ \"${SAST_FINDING_SCANNER:-}\" = semgrep ] || "
        "[ \"${SAST_STATUS_MISMATCH:-}\" = semgrep ]; then exit 1; fi\n"
        "      exit 0 ;;\n"
        "    pip_audit)\n"
        "      printf 'audit\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "      out=\"\"; prev=\"\"\n"
        "      for arg in \"$@\"; do [ \"$prev\" = \"--output\" ] && out=\"$arg\"; prev=\"$arg\"; done\n"
        "      if [ -n \"$out\" ]; then\n"
        "        if [ \"${SAST_INCOMPLETE_SCANNER:-}\" = audit ]; then\n"
        "          printf '[{\"name\":\"fixture\"}]' > \"$out\"\n"
        "        elif [ \"${SAST_FINDING_SCANNER:-}\" = audit ]; then\n"
        "          printf '[{\"vulns\":[{}]}]' > \"$out\"\n"
        "        else printf '[]' > \"$out\"; fi\n"
        "        if [ \"${SAST_BLOCK_RUFF_OUTPUT:-0}\" = 1 ]; then\n"
        "          result_dir=${SAST_RESULTS_DIR:?}\n"
        "          : > \"$result_dir/pip-audit.txt\"\n"
        "          printf '[]' > \"$result_dir/ruff.json\"\n"
        "          : > \"$result_dir/ruff.txt\"\n"
        "          printf '[]' > \"$result_dir/eslint.json\"\n"
        "          : > \"$result_dir/preflight.txt\"\n"
        "          printf '{\"results\":{}}' > \"$result_dir/detect-secrets.json\"\n"
        "          : > \"$result_dir/SUMMARY.txt\"\n"
        "          chmod 444 \"$result_dir/ruff.json\"\n"
        "          chmod 555 \"$result_dir\"\n"
        "          printf 'redirect-block\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "        fi\n"
        "      fi\n"
        "      if [ \"${SAST_FINDING_SCANNER:-}\" = audit ] || "
        "[ \"${SAST_STATUS_MISMATCH:-}\" = audit ]; then exit 1; fi\n"
        "      exit 0 ;;\n"
        "    ruff)\n"
        "      printf 'ruff\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "      case \" $* \" in\n"
        "        *' --output-format json '*)\n"
        "          if [ \"${SAST_FINDING_SCANNER:-}\" = ruff ]; then\n"
        "            printf '[{\"code\":\"B001\"}]'\n"
        "          else printf '[]'; fi ;;\n"
        "      esac\n"
        "      exit 0 ;;\n"
        "    detect_secrets)\n"
        "      if [ \"${SAST_BLOCK_PREFLIGHT_OUTPUT:-0}\" = 1 ]; then\n"
        "        chmod 755 \"${SAST_RESULTS_DIR:?}\"\n"
        "      fi\n"
        "      printf 'secrets\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "      if [ \"${SAST_BLOCK_SUMMARY_WRITE:-0}\" = 1 ]; then\n"
        "        mkdir \"${SAST_RESULTS_DIR:?}/SUMMARY.txt\"\n"
        "      fi\n"
        "      printf '{\"results\":{}}'; exit 0 ;;\n"
        f"    json.tool) exec {py} -m json.tool \"$@\" ;;\n"
        "  esac\n"
        "fi\n"
        "case \"${1:-}\" in\n"
        "  preflight.py)\n"
        "    printf 'preflight\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "    if [ \"${SAST_UNREADABLE_PREFLIGHT:-0}\" = 1 ]; then\n"
        "      printf 'fixture preflight finding\\n'\n"
        "      chmod 000 \"${SAST_RESULTS_DIR:?}/preflight.txt\"\n"
        "      exit 1\n"
        "    fi\n"
        "    if [ \"${SAST_FINDING_SCANNER:-}\" = preflight ]; then\n"
        "      printf 'fixture preflight finding\\n'; exit 1\n"
        "    fi\n"
        "    printf 'NO CRITICAL FINDINGS\\n'; exit 0 ;;\n"
        "esac\n"
        f"exec {py} \"$@\"\n",
    )
    _write_executable(
        fake_bin / "npx",
        "#!/usr/bin/env bash\n"
        "if [ \"${SAST_BLOCK_RUFF_OUTPUT:-0}\" = 1 ]; then\n"
        "  chmod 755 \"${SAST_RESULTS_DIR:?}\"\n"
        "fi\n"
        "printf 'eslint\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "out=\"\"; prev=\"\"\n"
        "for arg in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=\"$arg\"; prev=\"$arg\"; done\n"
        "if [ -n \"$out\" ]; then\n"
        "  if [ \"${SAST_FINDING_SCANNER:-}\" = eslint ]; then\n"
        "    printf '[{\"messages\":[{\"ruleId\":\"fixture\"}]}]' > \"$out\"\n"
        "  else printf '[]' > \"$out\"; fi\n"
        "fi\n"
        "if [ \"${SAST_BLOCK_PREFLIGHT_OUTPUT:-0}\" = 1 ]; then\n"
        "  result_dir=${SAST_RESULTS_DIR:?}\n"
        "  : > \"$result_dir/eslint.txt\"\n"
        "  : > \"$result_dir/preflight.txt\"\n"
        "  printf '{\"results\":{}}' > \"$result_dir/detect-secrets.json\"\n"
        "  : > \"$result_dir/detect-secrets.txt\"\n"
        "  : > \"$result_dir/SUMMARY.txt\"\n"
        "  chmod 444 \"$result_dir/preflight.txt\"\n"
        "  chmod 555 \"$result_dir\"\n"
        "  printf 'preflight-redirect-block\\n' >> \"$SAST_SCANNER_LOG\"\n"
        "fi\n"
        "if [ \"${SAST_FINDING_SCANNER:-}\" = eslint ] || "
        "[ \"${SAST_STATUS_MISMATCH:-}\" = eslint ]; then exit 1; fi\n"
        "exit 0\n",
    )
    return fake_bin, scanner_log


def _run_sast(
    root: Path,
    *,
    fail_semgrep: bool,
    extra_env: dict[str, str] | None = None,
    block_summary_write: bool = False,
    block_cleanup: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Counter[str]]:
    fake_bin, scanner_log = _sast_fixture(root)
    if block_cleanup:
        (root / "tools" / "sast_results" / "stale.sarif").mkdir(parents=True)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        "SAST_FAIL_SEMGREP": "1" if fail_semgrep else "0",
        "SAST_SCANNER_LOG": str(scanner_log),
        "SAST_RESULTS_DIR": str(root / "tools" / "sast_results"),
        "SAST_BLOCK_SUMMARY_WRITE": "1" if block_summary_write else "0",
    })
    env.update(extra_env or {})
    run = subprocess.run(
        ["bash", "tools/sast.sh"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = scanner_log.read_text(encoding="ascii").splitlines()
    return run, Counter(calls)


def test_sast_scanner_failure_has_setup_exit_and_exact_invocation_count(tmp_path):
    run, calls = _run_sast(tmp_path, fail_semgrep=True)

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["semgrep"] == 1
    assert "scanner error" in (run.stdout + run.stderr).lower()
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


def test_sast_clean_control_runs_each_authoritative_scan_once(tmp_path):
    run, calls = _run_sast(tmp_path, fail_semgrep=False)

    assert run.returncode == 0, run.stdout + run.stderr
    assert calls == Counter({
        "bandit": 1,
        "semgrep": 1,
        "audit": 1,
        "ruff": 1,
        "eslint": 1,
        "preflight": 1,
        "secrets": 1,
    })
    assert "Clean" in run.stdout


def test_sast_optimized_python_rejects_one_invalid_semgrep_report(tmp_path):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"PYTHONOPTIMIZE": "1", "SAST_INVALID_SEMGREP": "1"},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["semgrep"] == 1
    assert "complete JSON report" in (run.stdout + run.stderr)
    assert "Clean" not in run.stdout


@pytest.mark.parametrize(
    "scanner", ["bandit", "semgrep", "audit", "ruff", "eslint"]
)
def test_sast_json_finding_is_nonzero_after_one_authoritative_scan(tmp_path, scanner):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_FINDING_SCANNER": scanner},
    )

    assert run.returncode == 1, run.stdout + run.stderr
    assert calls[scanner] == 1
    assert "[sast] 1 finding categories." in run.stdout
    assert "Clean" not in run.stdout


def test_sast_summary_write_failure_is_one_reachable_setup_error(tmp_path):
    run, calls = _run_sast(
        tmp_path, fail_semgrep=False, block_summary_write=True
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["semgrep"] == 1
    assert "summary" in (run.stdout + run.stderr).lower()
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


@pytest.mark.parametrize("scanner", ["bandit", "semgrep", "audit"])
def test_sast_incomplete_json_schema_is_one_setup_error(tmp_path, scanner):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"PYTHONOPTIMIZE": "1", "SAST_INCOMPLETE_SCANNER": scanner},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls[scanner] == 1
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


@pytest.mark.parametrize("scanner", ["bandit", "semgrep", "audit", "eslint"])
def test_sast_empty_report_disagrees_with_one_finding_status(tmp_path, scanner):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_STATUS_MISMATCH": scanner},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls[scanner] == 1
    assert "status/report mismatch" in (run.stdout + run.stderr)
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


def test_sast_failed_cleanup_is_one_setup_error(tmp_path):
    run, calls = _run_sast(tmp_path, fail_semgrep=False, block_cleanup=True)

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["semgrep"] == 1
    assert "stale reports" in (run.stdout + run.stderr).lower()
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


def test_sast_ruff_output_redirection_failure_is_counted_once(tmp_path):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_BLOCK_RUFF_OUTPUT": "1"},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["redirect-block"] == 1
    assert calls["ruff"] == 0
    assert "Ruff JSON scan exited" in (run.stdout + run.stderr)
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "Clean" not in run.stdout


def test_sast_preflight_output_failure_is_not_a_finding_status(tmp_path):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_BLOCK_PREFLIGHT_OUTPUT": "1"},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["preflight-redirect-block"] == 1
    assert calls["preflight"] == 0
    assert "project preflight exited" in (run.stdout + run.stderr)
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "findings present" not in run.stdout
    assert "Clean" not in run.stdout


def test_sast_preflight_finding_status_remains_distinct_and_nonzero(tmp_path):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_FINDING_SCANNER": "preflight"},
    )

    assert run.returncode == 1, run.stdout + run.stderr
    assert calls["preflight"] == 1
    assert "preflight: findings present" in run.stdout
    assert "[sast] 1 finding categories." in run.stdout
    assert "scanner error" not in (run.stdout + run.stderr).lower()
    assert "Clean" not in run.stdout


def test_sast_unreadable_preflight_report_is_one_setup_error(tmp_path):
    run, calls = _run_sast(
        tmp_path,
        fail_semgrep=False,
        extra_env={"SAST_UNREADABLE_PREFLIGHT": "1"},
    )

    assert run.returncode == 2, run.stdout + run.stderr
    assert calls["preflight"] == 1
    assert "preflight report could not be read" in (run.stdout + run.stderr)
    assert "[sast] 1 scanner error(s)." in run.stdout
    assert "findings present" not in run.stdout
    assert "Clean" not in run.stdout


def test_governance_missing_root_is_cannot_evaluate(tmp_path):
    module = _load_python("operator_layer_absent_contract", "tools/operator_layer.py")
    out = tmp_path / "out"
    rc = module.cmd_governance(SimpleNamespace(
        artifacts_root=str(tmp_path / "missing"), out_dir=str(out)
    ))

    report = json.loads((out / "governance_findings.json").read_text(encoding="utf-8"))
    assert rc == 2
    assert report["measurement_status"] == "CANNOT_EVALUATE"
    assert report["artifacts_scanned"] == 0
    assert report["compliant"] is False
    assert len(report["measurement_errors"]) == 1


def test_governance_unreadable_artifact_is_counted_once(tmp_path, monkeypatch):
    module = _load_python("operator_layer_unreadable_contract", "tools/operator_layer.py")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    denied = artifacts / "denied.json"
    denied.write_text("{}\n", encoding="ascii")
    out = tmp_path / "out"
    real_read_text = Path.read_text
    denied_reads = 0

    def fail_one_read(path, *args, **kwargs):
        nonlocal denied_reads
        if path == denied:
            denied_reads += 1
            raise PermissionError("fixture denies artifact")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_one_read)
    rc = module.cmd_governance(SimpleNamespace(
        artifacts_root=str(artifacts), out_dir=str(out)
    ))

    report = json.loads(real_read_text(out / "governance_findings.json", encoding="utf-8"))
    assert rc == 2
    assert denied_reads == 1
    assert report["measurement_status"] == "CANNOT_EVALUATE"
    assert report["artifacts_scanned"] == 0
    assert len(report["measurement_errors"]) == 1


def test_governance_failed_artifact_stat_is_counted_once(tmp_path, monkeypatch):
    module = _load_python("operator_layer_stat_contract", "tools/operator_layer.py")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "visible.json").write_text(
        '{"status":"review-only"}\n', encoding="ascii"
    )
    denied = artifacts / "denied.json"
    denied.write_text("{}\n", encoding="ascii")
    out = tmp_path / "out"
    real_stat = Path.stat
    denied_stats = 0

    def fail_one_stat(path, *args, **kwargs):
        nonlocal denied_stats
        if path == denied:
            denied_stats += 1
            raise PermissionError("fixture denies denied.json stat")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_one_stat)
    rc = module.cmd_governance(SimpleNamespace(
        artifacts_root=str(artifacts), out_dir=str(out)
    ))

    report = json.loads(
        (out / "governance_findings.json").read_text(encoding="utf-8")
    )
    assert rc == 2
    assert denied_stats == 1
    assert report["measurement_status"] == "CANNOT_EVALUATE"
    assert report["artifacts_scanned"] == 1
    assert len(report["measurement_errors"]) == 1


def test_governance_unreadable_descendant_is_counted_once_after_partial_walk(tmp_path):
    module = _load_python("operator_layer_walk_contract", "tools/operator_layer.py")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "visible.json").write_text(
        '{"status":"review-only"}\n', encoding="ascii"
    )
    denied = artifacts / "denied"
    denied.mkdir()
    (denied / "hidden.json").write_text("{}\n", encoding="ascii")
    denied.chmod(0)
    out = tmp_path / "out"
    try:
        rc = module.cmd_governance(SimpleNamespace(
            artifacts_root=str(artifacts), out_dir=str(out)
        ))
    finally:
        denied.chmod(0o700)

    report = json.loads((out / "governance_findings.json").read_text(encoding="utf-8"))
    assert rc == 2
    assert report["measurement_status"] == "CANNOT_EVALUATE"
    assert report["artifacts_scanned"] == 1
    assert len(report["measurement_errors"]) == 1
    assert report["measurement_errors"][0]["reason"] == "UNREADABLE"


def test_governance_clean_control_scans_exactly_one_artifact(tmp_path):
    module = _load_python("operator_layer_clean_contract", "tools/operator_layer.py")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "clean.json").write_text('{"status":"review-only"}\n', encoding="ascii")
    out = tmp_path / "out"
    rc = module.cmd_governance(SimpleNamespace(
        artifacts_root=str(artifacts), out_dir=str(out)
    ))

    report = json.loads((out / "governance_findings.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert report["measurement_status"] == "COMPLETE"
    assert report["artifacts_scanned"] == 1
    assert report["measurement_errors"] == []
    assert report["compliant"] is True


def _opv_fixture(root: Path, body: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    (root / "scripts").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "bulk_downloader").mkdir()
    (root / "bulk_downloader" / "__init__.py").write_text("\n", encoding="ascii")
    for name in ("bd-opv-check.sh", "bd-stash-report.sh"):
        _write_executable(root / "scripts" / name, "#!/usr/bin/env bash\nexit 0\n")
    (root / "tools" / "opv_guide_lint.py").write_text(
        "print('OK fixture')\n", encoding="ascii"
    )

    out = root / "opv-out"
    jar = root / "opv.jar"
    source = (_REPO / "scripts" / "bd-opv-run.sh").read_text(encoding="utf-8")
    owned = "OUT=/tmp/bd_opv_run; JAR=/tmp/bd_opv_run.jar; LEDGER=\"$OUT/LEDGER.txt\""
    replacement = (
        f"OUT={shlex.quote(str(out))}; JAR={shlex.quote(str(jar))}; "
        'LEDGER="$OUT/LEDGER.txt"'
    )
    assert source.count(owned) == 1
    source = source.replace(owned, replacement)
    script = root / "scripts" / "bd-opv-run.sh"
    _write_executable(script, source)

    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    py_log = root / "python.log"
    real_py = shlex.quote(sys.executable)
    _write_executable(
        fake_bin / "python3",
        "#!/usr/bin/env bash\n"
        "case \" $* \" in *f2a.json*) printf 'f2a\\n' >> \"$OPV_PY_LOG\" ;; esac\n"
        f"exec {real_py} \"$@\"\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/usr/bin/env bash\n"
        "args=\" $* \"\n"
        "case \"$args\" in\n"
        "  *'/api/data/site_health'*)\n"
        "    out=\"\"; prev=\"\"\n"
        "    for arg in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=\"$arg\"; prev=\"$arg\"; done\n"
        "    if [ -n \"$out\" ]; then printf '%s' \"$OPV_SITE_HEALTH_BODY\" > \"$out\"; fi\n"
        "    case \"$args\" in *' -w '*) printf '200' ;; *) printf '%s' \"$OPV_SITE_HEALTH_BODY\" ;; esac ;;\n"
        "  *'/api/csrf'*) printf '{\"csrf_token\":\"fixture\"}' ;;\n"
        "  *' -w '*) printf '200' ;;\n"
        "  *) printf '{}' ;;\n"
        "esac\n",
    )
    _write_executable(fake_bin / "ip", "#!/usr/bin/env bash\nexit 1\n")
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        "OPV_PY_LOG": str(py_log),
        "OPV_SITE_HEALTH_BODY": body,
    })
    run = subprocess.run(
        ["/bin/bash", str(script), str(root), "http://fixture.invalid"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return run, out / "LEDGER.txt", py_log


def _f2a_row(ledger: Path) -> list[str]:
    rows = [line.split() for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.split()[:1] == ["F2a"]]
    assert len(rows) == 1
    return rows[0]


def test_opv_malformed_site_health_is_fail_after_one_parse(tmp_path):
    run, ledger, py_log = _opv_fixture(tmp_path, "{malformed")

    assert run.returncode == 0, run.stdout + run.stderr
    assert py_log.read_text(encoding="ascii").splitlines().count("f2a") == 1
    assert _f2a_row(ledger)[1] == "FAIL"


def test_opv_valid_site_health_control_is_pass_after_one_parse(tmp_path):
    run, ledger, py_log = _opv_fixture(
        tmp_path, '{"ok":true,"data":{"clusters":[{}],"sites":[]}}'
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert py_log.read_text(encoding="ascii").splitlines().count("f2a") == 1
    assert _f2a_row(ledger)[1] == "PASS"


def test_opv_parseable_but_invalid_count_fields_fail_after_one_parse(tmp_path):
    run, ledger, py_log = _opv_fixture(
        tmp_path, '{"data":{"clusters":"not-a-list","sites":[]}}'
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert py_log.read_text(encoding="ascii").splitlines().count("f2a") == 1
    assert _f2a_row(ledger)[1] == "FAIL"


@pytest.mark.parametrize(
    "body",
    [
        '{"ok":false,"data":{"clusters":[{}],"sites":[]}}',
        '{"ok":true,"data":{"clusters":[{}]}}',
    ],
)
def test_opv_incomplete_success_envelope_fails_after_one_parse(tmp_path, body):
    run, ledger, py_log = _opv_fixture(tmp_path, body)

    assert run.returncode == 0, run.stdout + run.stderr
    assert py_log.read_text(encoding="ascii").splitlines().count("f2a") == 1
    assert _f2a_row(ledger)[1] == "FAIL"
