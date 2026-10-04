"""O1807 R5: a "nothing-ran" suite must not kill bd-parband before RESULTS.

run_one maps pytest exit 5 (no tests collected) to status "nothing-ran", but
main()'s status->tag table had no such key, so the band died with
KeyError('nothing-ran') and never wrote the band-results file bd-retest reads.
The REAL run_one runs real pytest on fixture files, so the test binds the
pairing of the status run_one returns with the tag table that consumes it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
PARBAND = ROOT / "toolchain" / "bin" / "bd-parband"


def _load_parband():
    name = "o1807_r5_bd_parband"
    loader = importlib.machinery.SourceFileLoader(name, str(PARBAND))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    (path / "bulk_downloader").mkdir(parents=True)
    (path / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n', encoding="utf-8"
    )
    (path / "tests").mkdir()
    # Empty file: real pytest collects nothing and exits 5.
    (path / "tests" / "test_empty.py").write_text("", encoding="utf-8")
    (path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )
    return path


# test_ok.py is the positive control: the same real run reaches RESULTS with
# rc 0, so a crash on test_empty.py is the defect, not the fixture.
@pytest.mark.parametrize(
    "suite, status, rc, tag",
    [
        ("tests/test_empty.py", "nothing-ran", 1, "NOTHING-RAN"),
        ("tests/test_ok.py", "pass", 0, "PASS"),
    ],
)
def test_real_run_one_status_reaches_results(
    work, tmp_path, monkeypatch, capsys, suite, status, rc, tag
):
    module = _load_parband()
    results = tmp_path / "band.json"
    monkeypatch.setattr(module, "RESULTS", str(results))
    monkeypatch.setattr(module.cut_quality, "enforce", lambda *a, **k: True)
    # run_one makes a BD_HOME mkdtemp per call; keep it under tmp_path.
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))

    got = module.main([suite, "--work", str(work)])

    assert got == rc
    assert tag in capsys.readouterr().out
    written = json.loads(results.read_text(encoding="utf-8"))
    assert [r["status"] for r in written["results"]] == [status]
