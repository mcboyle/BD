"""H667: a stale register marker fails its gates WITH the exact replacement line.

The canonical-task-register marker in project-knowledge/IMPROVEMENT_BACKLOG.md is
checked against the table by two gates. Each failure used to print counts only
(or nothing), so every stale marker cost a CI cycle plus a hand derivation of the
sha256. Both gates now print the derived marker verbatim; fixing it is one paste.

Each gate is run against a copy of the real register whose marker digest is
corrupted. The uncorrupted copy is run first as a control: it must pass, which
proves the redirect reached the gate and the copy is a valid register.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest

BD_GATE_SCOPE = "module"
ROOT =Path(__file__).resolve().parents[1]
REGISTER = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
_MARKER = re.compile(
    r"^<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=([0-9a-f]{64}) -->$",
    re.MULTILINE,
)

# (gate module, module attribute holding the register path, gate function)
GATES = [
    ("test_v3_66_1164_one_task_authority", "BACKLOG",
     "test_the_backlog_publishes_and_matches_its_exact_denominator"),
    ("test_register_archive_holds_the_moved_rows", "REGISTER",
     "test_the_register_marker_counts_the_live_rows_only"),
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_h667_{name}", ROOT / "tests" / f"{name}.py")
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("module_name", "attr", "gate"), GATES, ids=[g[0] for g in GATES])
def test_stale_marker_failure_prints_the_derived_marker(tmp_path, monkeypatch, module_name, attr, gate):
    text = REGISTER.read_text(encoding="ascii")
    markers = _MARKER.findall(text)
    assert len(markers) == 1, f"expected one canonical marker in the register, found {len(markers)}"
    true_line = _MARKER.search(text).group(0)

    module = _load(module_name)
    assert getattr(module, attr) == REGISTER, f"{module_name}.{attr} no longer names the register"
    copy = tmp_path / "IMPROVEMENT_BACKLOG.md"
    with monkeypatch.context() as m:
        m.setattr(module, attr, copy)

        copy.write_text(text, encoding="ascii")
        getattr(module, gate)()  # control: the true register passes this gate

        digest = markers[0]
        wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
        copy.write_text(text.replace(digest, wrong, 1), encoding="ascii")
        with pytest.raises(AssertionError) as caught:
            getattr(module, gate)()
    assert true_line in str(caught.value), (
        f"{module_name} rejected the stale marker without printing the replacement line: "
        f"{str(caught.value)[:300]}"
    )
