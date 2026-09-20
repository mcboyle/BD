"""Row 920: offline template canaries report DOM drift without login."""

BD_GATE_SCOPE = "module"

import importlib.util
import json
from pathlib import Path
from importlib.machinery import SourceFileLoader


TOOL = Path(__file__).parents[1] / "toolchain" / "bin" / "bd-template-verify"


def _tool():
    spec = importlib.util.spec_from_loader(
        "row920_template_verify", SourceFileLoader("row920_template_verify", str(TOOL))
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(count, status="HIT"):
    return {
        "template_id": "canary",
        "verdict": status,
        "selectors": [{"path": "canary.download.row_selectors.[0]", "selector": ".download", "status": status, "count": count}],
    }


def test_matrix_detects_modified_dom_and_emits_structured_digest(tmp_path, monkeypatch):
    tool = _tool()
    baseline = tmp_path / "baseline.html"
    current = tmp_path / "current.html"
    baseline.write_text("<a class=download></a>", encoding="utf-8")
    current.write_text("<main>changed</main>", encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"canaries": [{
        "template_id": "canary", "baseline": str(baseline), "subject": str(current),
    }]}), encoding="utf-8")
    calls = []

    def fake_verify(template, subject, **kwargs):
        calls.append((template, Path(subject)))
        return _report(1 if Path(subject) == baseline else 0, "HIT" if Path(subject) == baseline else "MISS")

    monkeypatch.setattr(tool, "_resolve_template_argument", lambda name: (name, ""))
    report = tool.run_canary_matrix(matrix, verifier=fake_verify)

    assert [path for _template, path in calls] == [baseline, current]
    assert report["verdict"] == "DRIFT"
    assert report["changed_count"] == 1
    assert report["canaries"][0]["changes"] == [{
        "path": "canary.download.row_selectors.[0]", "selector": ".download",
        "baseline": {"status": "HIT", "count": 1},
        "current": {"status": "MISS", "count": 0},
    }]
    assert report["daily_digest"] == {
        "template_canaries_healthy": 0,
        "template_canaries_drifted": 1,
        "template_canaries_unknown": 0,
    }


def test_matrix_refuses_live_subjects_and_never_calls_verifier(tmp_path):
    tool = _tool()
    fixture = tmp_path / "baseline.html"
    fixture.write_text("<p>offline</p>", encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"canaries": [{
        "template_id": "canary", "baseline": str(fixture), "subject": "https://example.test/page",
    }]}), encoding="utf-8")

    report = tool.run_canary_matrix(
        matrix,
        verifier=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live verifier call")),
    )

    assert report["verdict"] == "UNKNOWN"
    assert report["canaries"][0]["error"] == "canary subject must be a saved HTML file, not a URL"
