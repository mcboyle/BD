"""Phase 9.7 -- local LLM eval harness (RED-first, mock-first).

Fixture-driven harness run before trusting LLM slices. All model outputs are
mocked (no live Ollama needed). Pass criteria: deterministic fallback always
preserved; schema validation catches malformed output; no raw secret reaches the
model; known bucket enums enforced; timeout/offline fall back cleanly; malformed
output cannot mutate state.
"""

from bulk_downloader import llm_eval


def test_categories_present():
    cats = llm_eval.categories()
    for c in ("known-good", "malformed", "timeout-offline",
              "redaction-sensitive", "schema-invalid", "low-confidence"):
        assert c in cats, c


def test_min_tasks_covered():
    tasks = {f["task_id"] for f in llm_eval.FIXTURES}
    for t in ("failure_triage", "redaction_secret_scan", "template_review_summary",
              "opv_evidence_summary", "ui_screenshot_review",
              "filename_metadata_parse", "nl_saved_search"):
        assert t in tasks, t


def test_run_all_pass_mock_only():
    res = llm_eval.run()
    assert res["total"] > 0
    failing = [c for c in res["cases"] if not c["ok"]]
    assert res["failed"] == 0, failing


def test_summary_counts_consistent():
    res = llm_eval.run()
    assert res["passed"] + res["failed"] == res["total"]


def test_redaction_fixtures_block_the_model():
    res = llm_eval.run()
    red = [c for c in res["cases"] if c.get("category") == "redaction-sensitive"]
    assert red
    assert all(c["ok"] for c in red)
    assert all(c.get("model_called") is False for c in red)


def test_malformed_fixtures_do_not_mutate_state():
    res = llm_eval.run()
    mal = [c for c in res["cases"] if c.get("category") in ("malformed", "schema-invalid")]
    assert mal
    assert all(c["ok"] for c in mal)


def test_timeout_offline_fall_back_cleanly():
    res = llm_eval.run()
    to = [c for c in res["cases"] if c.get("category") == "timeout-offline"]
    assert to
    assert all(c["ok"] for c in to)
    assert all(c.get("via") == "fallback" for c in to)
