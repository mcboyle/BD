"""Row 777: repeated rendered-page shape failures must not enter the retry ladder."""
from __future__ import annotations

import ast
from collections import Counter
import threading
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"


# This is the complete literal page-shape failure population emitted through
# _handle_failure in bulk_downloader/ on the row-777 base census.
PAGE_SHAPE_FAILURES = ("No download button found",)
_REPO = Path(__file__).resolve().parents[1]


def _emitted_page_shape_failures():
    """Derive literal _handle_failure page-shape messages from product code."""
    messages = set()
    for source in (_REPO / "bulk_downloader").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_handle_failure" and len(node.args) >= 2):
                continue
            message = node.args[1]
            if isinstance(message, ast.Constant) and isinstance(message.value, str):
                if message.value.lower().startswith("no download") and message.value.lower().endswith("found"):
                    messages.add(message.value)
    return tuple(sorted(messages))


def test_runner_telemetry_import_transform_control():
    """The mutation transform control imports its subject but drives no seam."""
    from bulk_downloader.runner import SiteRunner

    assert SiteRunner.__name__ == "SiteRunner"


def _failure_surface():
    """Use the real telemetry methods with only their side effects recorded."""
    from bulk_downloader.runner import SiteRunner

    runner = type("FailureSurface", (), {})()
    runner.site_id = "row777"
    runner.config = {"name": "Row 777", "max_retries": 2}
    runner.jobs = {"https://example.test/page": {"retries": 0}}
    runner._lock = threading.Lock()
    runner._RETRY_DELAYS_BY_KIND = SiteRunner._RETRY_DELAYS_BY_KIND
    runner._classify_error = SiteRunner._classify_error.__get__(runner)
    updates = []

    def update_job(*args, **kwargs):
        updates.append((args, kwargs))

    runner._update_job = update_job
    return runner, updates


def _run_failure(monkeypatch, message, *, classify=None):
    """Drive the schedule consumer and return its one observable update."""
    from bulk_downloader import hooks, runner_telemetry
    from bulk_downloader.runner import SiteRunner

    runner, updates = _failure_surface()
    if classify is not None:
        runner._classify_error = classify
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks, "fire_event", lambda *args, **kwargs: None)
    SiteRunner._handle_failure_current(runner, "https://example.test/page", message)
    assert len(updates) == 1, "the schedule seam must publish exactly one update"
    return updates[0]


def test_page_shape_failure_population_is_terminal_at_the_schedule_seam(monkeypatch):
    """Deleting the page-shape classifier branch reintroduces the 10m/1h ladder."""
    assert PAGE_SHAPE_FAILURES == _emitted_page_shape_failures()
    assert len(PAGE_SHAPE_FAILURES) == 1
    kinds = Counter()
    for message in PAGE_SHAPE_FAILURES:
        args, kwargs = _run_failure(monkeypatch, message)
        kinds[args[1]] += 1
        assert args[1] == "failed"
        assert args[2] == "[page_shape] " + message
        assert kwargs == {"screenshot": "", "_run_generation": None}
    assert kinds == {"failed": 1}


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("Page not found", "permanent"),
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("Page load timeout", "network"),
        ("ordinary unclassified failure", "transient"),
    ],
)
def test_existing_classifier_categories_keep_their_exact_population(message, kind):
    """The new page-shape branch must not reclassify the established categories."""
    from bulk_downloader.runner import SiteRunner

    runner, _updates = _failure_surface()
    assert SiteRunner._classify_error(runner, message) == kind


def test_unmapped_kind_keeps_the_legacy_default_ladder_at_the_schedule_seam(monkeypatch):
    """Negative control: removing the .get default must fail this real consumer."""
    args, kwargs = _run_failure(
        monkeypatch, "unmapped kind", classify=lambda _message: "unmapped")
    assert args[1] == "pending"
    assert args[2].startswith("[unmapped] Retry 1/2 in 10m")
    assert kwargs["retries"] == 1
    assert kwargs["retry_after"] > 0
