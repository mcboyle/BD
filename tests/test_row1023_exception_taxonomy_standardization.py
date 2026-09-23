"""Row 1023: Unified Error Hierarchy & Enterprise Exception Taxonomy Standardization (ExceptionTaxonomy).

Validates the taxonomy (hierarchy, codes, serialization, classification) and its live
product path: every exception that escapes a worker attempt reaches
``SiteRunner._publish_worker_exception``, which (config-selector SyntaxErrors aside: those
go to needs_review) publishes ``worker error: [<taxonomy code>] <first 100 raw characters>``
through ``friendly_error.format_standardized_error`` -> ``exceptions.classify_exception``.
Retry scheduling stays the base message-based policy; the code is added, never charged
to the 100-character diagnostic window that policy reads. The shared failed-job
translator ``runner._translate_failed_message`` is unchanged from base, and the row 987
remediation advisor still reads the RAW failure: the code never reaches its advice.

RED is semantic (AssertionError on the published text or on the classification), not an
ImportError: the refuted trees ship the same module names. The positive control passes on
every tree; the needs_review selector branch is the negative control.
"""
from __future__ import annotations

import ast
import inspect
import socket
import sqlite3
import textwrap
import threading
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import exceptions
except ImportError:
    exceptions = None

URL = "https://example.test/video/1023"


def test_positive_control_friendly_error_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import friendly_error

    assert hasattr(friendly_error, "friendly_error")
    assert callable(friendly_error.friendly_error)
    assert friendly_error.friendly_error("404 Not Found") != ""


def test_exception_taxonomy_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import friendly_error

    assert exceptions is not None, (
        "Row 1023 capability missing: Unified Error Hierarchy & Enterprise Exception Taxonomy "
        "Standardization (ExceptionTaxonomy) not implemented in bulk_downloader.exceptions"
    )
    assert hasattr(exceptions, "BulkDownloaderError"), (
        "Row 1023 base error missing: bulk_downloader.exceptions.BulkDownloaderError"
    )
    assert hasattr(friendly_error, "format_standardized_error"), (
        "Row 1023 caller missing: bulk_downloader.friendly_error.format_standardized_error"
    )


def test_unified_hierarchy_inheritance():
    """Verify clean inheritance tree from BulkDownloaderError."""
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader.exceptions import (
        AuthenticationError,
        BulkDownloaderError,
        ConfigurationError,
        ConnectionTimeoutError,
        DNSResolutionError,
        ExtractionError,
        IntegrityVerificationError,
        InvalidCredentialsError,
        NetworkError,
        ObjectNotFoundError,
        StorageError,
    )

    assert issubclass(BulkDownloaderError, Exception)
    assert issubclass(NetworkError, BulkDownloaderError)
    assert issubclass(ConnectionTimeoutError, NetworkError)
    assert issubclass(DNSResolutionError, NetworkError)

    assert issubclass(AuthenticationError, BulkDownloaderError)
    assert issubclass(InvalidCredentialsError, AuthenticationError)

    assert issubclass(StorageError, BulkDownloaderError)
    assert issubclass(ObjectNotFoundError, StorageError)
    assert issubclass(IntegrityVerificationError, StorageError)

    assert issubclass(ExtractionError, BulkDownloaderError)
    assert issubclass(ConfigurationError, BulkDownloaderError)


def test_exception_attributes_and_serialization():
    """Verify standard attributes: error_code, retryable, details, http_status, and to_dict()."""
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader.exceptions import ConnectionTimeoutError

    err = ConnectionTimeoutError(
        "Connection timed out to origin host",
        error_code="BD-NET-002",
        retryable=True,
        details={"host": "media.example.com", "timeout_sec": 30},
        http_status=504,
    )

    assert err.error_code == "BD-NET-002"
    assert err.retryable is True
    assert err.http_status == 504
    assert err.details["host"] == "media.example.com"

    d = err.to_dict()
    assert d["error_type"] == "ConnectionTimeoutError"
    assert d["error_code"] == "BD-NET-002"
    assert d["message"] == "Connection timed out to origin host"
    assert d["retryable"] is True
    assert d["http_status"] == 504
    assert d["details"]["timeout_sec"] == 30


def test_classify_exception_adapter():
    """Verify standard library exceptions are accurately mapped into the taxonomy."""
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader.exceptions import (
        ConfigurationError,
        ConnectionTimeoutError,
        NetworkError,
        StorageError,
        classify_exception,
    )

    # Timeout -> ConnectionTimeoutError
    mapped1 = classify_exception(TimeoutError("request timed out"))
    assert isinstance(mapped1, ConnectionTimeoutError)
    assert mapped1.retryable is True

    # ConnectionRefusedError -> NetworkError
    mapped2 = classify_exception(ConnectionRefusedError("port 443 refused"))
    assert isinstance(mapped2, NetworkError)

    # FileNotFoundError -> StorageError
    mapped3 = classify_exception(FileNotFoundError("target disk volume missing"))
    assert isinstance(mapped3, StorageError)

    # ValueError -> ConfigurationError
    mapped4 = classify_exception(ValueError("invalid buffer allocation size"))
    assert isinstance(mapped4, ConfigurationError)


def test_friendly_error_integration():
    """Verify integration with bulk_downloader.friendly_error.format_standardized_error."""
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader import friendly_error
    from bulk_downloader.exceptions import IntegrityVerificationError

    err = IntegrityVerificationError(
        "Remote SHA-256 hash does not match computed local digest",
        error_code="BD-STORE-005",
        details={"expected": "abc", "actual": "def"},
    )

    formatted = friendly_error.format_standardized_error(err)
    assert "[BD-STORE-005]" in formatted
    assert "Remote SHA-256 hash does not match computed local digest" in formatted


# B9-B F2: the exception TYPE decides before any message word does.
@pytest.mark.parametrize("error", [
    ValueError("timeout must be a positive integer"),
    ValueError("connection refused by the config validator"),
    TypeError("socket timeout option has the wrong type"),
], ids=["valueerror-timeout", "valueerror-connection-refused", "typeerror-timeout"])
def test_classification_prefers_exception_type_to_message_words(error):
    from bulk_downloader.exceptions import ConfigurationError, classify_exception

    classified = classify_exception(error)

    assert isinstance(classified, ConfigurationError)
    assert classified.retryable is False


# B9-B F3: what the taxonomy cannot classify is generic and NOT retryable.
def test_unclassified_errors_are_non_retryable():
    from bulk_downloader.exceptions import BulkDownloaderError, classify_exception

    classified = classify_exception(RuntimeError("not covered by taxonomy"))

    assert type(classified) is BulkDownloaderError
    assert classified.error_code == "BD-GEN-000"
    assert classified.retryable is False
    assert classified.http_status == 500
    assert classified.details == {"original_type": "RuntimeError"}


# N6-A E2: the classifier uses its own taxonomy for the failures the row names.
# bd-fixer-B F1: a transient sqlite lock is retryable; a lasting sqlite fault is not, even
# when its text contains "locked" (these errors are built by hand: no sqlite_errorcode).
@pytest.mark.parametrize("error, expected_type, code, retryable", [
    (socket.gaierror(socket.EAI_NONAME, "name lookup failed"), "DNSResolutionError", "BD-NET-003", True),
    (sqlite3.OperationalError("database is locked"), "StorageError", "BD-STORE-001", True),
    (sqlite3.OperationalError("database table is locked: sqlite_master"), "StorageError", "BD-STORE-001", True),
    (sqlite3.OperationalError("no such table: locked_urls"), "StorageError", "BD-STORE-001", False),
    (sqlite3.IntegrityError("UNIQUE constraint failed: queue.url"), "StorageError", "BD-STORE-001", False),
    (PermissionError("connection refused by filesystem permissions"), "StorageError", "BD-STORE-001", False),
    (KeyError("timeout setting is absent"), "ConfigurationError", "BD-CFG-001", False),
], ids=["dns-gaierror", "sqlite-lock", "sqlite-table-lock", "sqlite-locked-name-not-a-lock",
        "sqlite-integrity", "permission", "keyerror"])
def test_classifies_typed_failures_before_message_fallback(error, expected_type, code, retryable):
    assert exceptions is not None, "exceptions capability missing"

    classified = exceptions.classify_exception(error)

    assert type(classified).__name__ == expected_type
    assert classified.error_code == code
    assert classified.retryable is retryable
    assert classified.message == str(error)
    assert classified.details == {"original_type": type(error).__name__}


def _real_sqlite_error(tmp_path, kind):
    """Make sqlite itself raise ``kind``; return the OperationalError it raised."""
    db = str(tmp_path / "queue.db")
    holder = sqlite3.connect(db, timeout=0, isolation_level=None)
    other = sqlite3.connect(db, timeout=0, isolation_level=None)
    reader = None  # kept referenced: a collected cursor would finalize its statement
    try:
        holder.execute("CREATE TABLE queue (url TEXT)")
        holder.executemany("INSERT INTO queue VALUES (?)", [("a",), ("b",)])
        if kind == "busy":  # another connection holds the write lock (SQLITE_BUSY)
            holder.execute("BEGIN EXCLUSIVE")
            conn, statement = other, "INSERT INTO queue VALUES ('c')"
        elif kind == "busy-snapshot":  # WAL: writing from a snapshot another commit moved past
            assert holder.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
            other.execute("BEGIN")
            assert other.execute("SELECT count(*) FROM queue").fetchone() == (2,)
            holder.execute("INSERT INTO queue VALUES ('c')")
            conn, statement = other, "INSERT INTO queue VALUES ('d')"
        elif kind == "locked":  # an open read cursor on the same connection (SQLITE_LOCKED)
            reader = holder.execute("SELECT url FROM queue")
            assert reader.fetchone() == ("a",), "precondition: the read is still in progress"
            conn, statement = holder, "DROP TABLE queue"
        elif kind == "missing-table":  # a lasting fault whose text merely contains "locked"
            conn, statement = other, "SELECT url FROM locked_urls"
        else:  # a lasting fault whose text quotes sqlite's lock wording: only the code tells
            conn, statement = other, 'SELECT url FROM "database is locked"'
        with pytest.raises(sqlite3.OperationalError) as raised:
            conn.execute(statement)
        return raised.value
    finally:
        holder.close()
        other.close()


# bd-fixer-B F1 on errors sqlite really raises (they carry sqlite_errorcode on Python 3.11+;
# an extended code keeps its primary code in the low 8 bits: 517 is SQLITE_BUSY_SNAPSHOT).
@pytest.mark.parametrize("kind, text, code, retryable", [
    ("busy", "database is locked", 5, True),
    ("busy-snapshot", "database is locked", 517, True),
    ("locked", "database table is locked", 6, True),
    ("missing-table", "no such table: locked_urls", 1, False),
    ("missing-table-named-like-a-lock", "no such table: database is locked", 1, False),
], ids=["busy", "busy-snapshot", "locked", "missing-table", "missing-table-named-like-a-lock"])
def test_real_sqlite_lock_is_a_retryable_storage_error(tmp_path, kind, text, code, retryable):
    assert exceptions is not None, "exceptions capability missing"
    error = _real_sqlite_error(tmp_path, kind)
    assert (str(error), error.sqlite_errorcode) == (text, code), (
        "precondition: sqlite raised the failure this case names")

    classified = exceptions.classify_exception(error)

    assert (type(classified).__name__, classified.error_code, classified.retryable) == (
        "StorageError", "BD-STORE-001", retryable)
    assert classified.message == text
    assert classified.details == {"original_type": "OperationalError"}


# N6-A m5: library failures no type branch models reach the taxonomy only through their words.
def test_untyped_network_failures_are_classified_by_their_words():
    assert exceptions is not None, "exceptions capability missing"
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from requests.exceptions import ConnectionError as RequestsConnectionError

    assert not issubclass(PlaywrightTimeoutError, TimeoutError), "precondition: not a builtin timeout"
    assert not issubclass(RequestsConnectionError, ConnectionRefusedError), "precondition: untyped"
    timeout = exceptions.classify_exception(PlaywrightTimeoutError("Timeout 30000ms exceeded."))
    refused = exceptions.classify_exception(RequestsConnectionError(
        "Failed to establish a new connection: [Errno 111] Connection refused"))

    assert (type(timeout).__name__, timeout.error_code, timeout.retryable) == (
        "ConnectionTimeoutError", "BD-NET-002", True)
    assert timeout.details == {"original_type": "TimeoutError"}
    assert (type(refused).__name__, refused.error_code, refused.retryable) == (
        "NetworkError", "BD-NET-001", True)
    assert refused.details == {"original_type": "ConnectionError"}


# N6-A E3: a taxonomy instance keeps its identity, code and overrides through the seam.
def test_domain_error_classification_preserves_instance_and_overrides():
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader.friendly_error import format_standardized_error

    error = exceptions.IntegrityVerificationError(
        "checksum mismatch", error_code="BD-CUSTOM-001", retryable=True,
        http_status=409, details={"expected": "abc", "actual": "def"},
    )

    classified = exceptions.classify_exception(error)

    assert classified is error
    assert classified.to_dict() == {
        "error_type": "IntegrityVerificationError", "error_code": "BD-CUSTOM-001",
        "message": "checksum mismatch", "retryable": True, "http_status": 409,
        "details": {"expected": "abc", "actual": "def"},
    }
    assert format_standardized_error(error) == "[BD-CUSTOM-001] checksum mismatch"
    assert format_standardized_error(classified) == "[BD-CUSTOM-001] checksum mismatch"


# ---- live worker seam (N6-A E1 / B9-B F1) ---------------------------------------------------
def _worker_loop_publish_calls():
    """Calls to _publish_worker_exception inside SiteRunner._worker_loop's except handlers."""
    from bulk_downloader.runner import SiteRunner

    tree = ast.parse(textwrap.dedent(inspect.getsource(SiteRunner._worker_loop)))
    return sum(
        1
        for handler in ast.walk(tree) if isinstance(handler, ast.ExceptHandler)
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_publish_worker_exception"
    )


def _worker(monkeypatch, *, max_retries=0, config=None):
    """The real SiteRunner failure methods bound to a minimal worker surface."""
    from bulk_downloader import hooks, runner, runner_telemetry

    worker = SimpleNamespace(
        site_id="row1023", config={"name": "taxonomy", "max_retries": max_retries, **(config or {})},
        jobs={URL: {"retries": 0, "status": "running"}}, _lock=threading.Lock(),
        _RETRY_DELAYS_BY_KIND=runner.SiteRunner._RETRY_DELAYS_BY_KIND,
        _SELECTOR_SYNTAX_MARKERS=runner.SiteRunner._SELECTOR_SYNTAX_MARKERS,
    )
    for name in ("_classify_error", "_config_selector_syntax_error", "_handle_failure",
                 "_handle_failure_current", "_publish_worker_exception"):
        setattr(worker, name, getattr(runner.SiteRunner, name).__get__(worker))
    seen = {"updates": [], "logs": [], "events": []}
    worker._update_job = lambda *args, **kwargs: seen["updates"].append((args, kwargs))
    monkeypatch.setattr(runner, "db_log", lambda *args, **kwargs: seen["logs"].append(args))
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *args, **kwargs: seen["logs"].append(args))
    monkeypatch.setattr(hooks, "fire_event", lambda *args, **kwargs: seen["events"].append((args, kwargs)))
    return worker, seen


@pytest.mark.parametrize("error, published", [
    (socket.gaierror(socket.EAI_NONAME, "name lookup failed"), "worker error: [BD-NET-003] {}"),
    (sqlite3.OperationalError("database is locked"), "worker error: [BD-STORE-001] {}"),
    (RuntimeError("renderer crashed while saving"), "worker error: [BD-GEN-000] {}"),
    (ValueError("timeout must be a positive integer"), "[network] worker error: [BD-CFG-001] {}"),
], ids=["dns", "sqlite-lock", "unclassified", "config-with-timeout-words"])
def test_worker_exception_publication_uses_taxonomy(monkeypatch, error, published):
    assert _worker_loop_publish_calls() == 1, "precondition: the live worker loop publishes through this seam"
    worker, seen = _worker(monkeypatch)
    expected = published.format(error)

    assert worker._publish_worker_exception(URL, error) == "failure"

    assert [update[0] for update in seen["updates"]] == [(URL, "failed", expected)]
    assert len(seen["logs"]) == 1
    assert seen["logs"][0][2:4] == (URL, "failed")
    assert seen["logs"][0][6] == expected
    assert len(seen["events"]) == 1
    assert seen["events"][0][0][0] == "failed"
    assert seen["events"][0][1]["job"]["message"] == expected


def test_worker_publication_keeps_a_domain_errors_own_code(monkeypatch):
    assert exceptions is not None, "exceptions capability missing"
    worker, seen = _worker(monkeypatch)
    error = exceptions.IntegrityVerificationError("checksum mismatch", error_code="BD-CUSTOM-001")

    assert worker._publish_worker_exception(URL, error) == "failure"

    expected = "worker error: [BD-CUSTOM-001] checksum mismatch"
    assert [update[0] for update in seen["updates"]] == [(URL, "failed", expected)]
    assert len(seen["logs"]) == 1
    assert len(seen["events"]) == 1


# bd-fixer-B F2: the code is added outside the 100 raw characters that the retry kind and the
# friendly_error translation read, never charged to them.
def test_worker_code_is_not_charged_to_the_100_character_window(monkeypatch):
    from bulk_downloader.runner import _translate_failed_message

    # "HTTP Error 404" ends at raw character 100: inside the base window, and cut after its
    # "H" if the 13-character "[BD-GEN-000] " prefix were counted against those 100 characters.
    raw = "fetch " + "a" * 79 + " HTTP Error 404: Not Found"
    assert raw.index("HTTP Error 404") == 86 and raw[:100].endswith("HTTP Error 404"), (
        "precondition: the deciding words end exactly at the window edge")
    worker, seen = _worker(monkeypatch, max_retries=2)

    assert worker._publish_worker_exception(URL, RuntimeError(raw)) == "failure"

    expected = "[permanent] worker error: [BD-GEN-000] " + raw[:100]
    assert [update[0] for update in seen["updates"]] == [(URL, "failed", expected)]
    assert len(seen["logs"]) == 1
    assert seen["logs"][0][6] == expected
    assert len(seen["events"]) == 1
    assert seen["events"][0][1]["job"]["error_kind"] == "permanent"
    # _update_job_current shows a failed job through this translator: the queue row still
    # reads the friendly 404 sentence, as it did before the code was added.
    assert _translate_failed_message(expected) == "Not found (404). URL is dead or moved."


def test_worker_retry_ladder_carries_the_code_without_terminal_side_effects(monkeypatch):
    worker, seen = _worker(monkeypatch, max_retries=2)

    assert worker._publish_worker_exception(URL, RuntimeError("renderer crashed")) == "failure"

    assert len(seen["updates"]) == 1
    args, kwargs = seen["updates"][0]
    assert args == (URL, "pending", "Retry 1/2 in 10m — worker error: [BD-GEN-000] renderer crashed")
    assert kwargs["retries"] == 1
    assert seen["logs"] == []
    assert seen["events"] == []


def test_selector_config_error_keeps_needs_review_without_a_code(monkeypatch):
    """Negative control: the terminal-for-config branch is untouched by the taxonomy."""
    selector = "a.download[data-q=2160p"
    error = Exception(
        "Locator.count: SyntaxError: Failed to execute 'querySelectorAll' on 'Document': "
        f"'{selector}' is not a valid selector.")
    worker, seen = _worker(monkeypatch, max_retries=2, config={"dl_selector": selector})

    assert worker._publish_worker_exception(URL, error) == "needs_review"

    assert len(seen["updates"]) == 1
    url, status, message = seen["updates"][0][0]
    assert (url, status) == (URL, "needs_review")
    assert message.startswith(f"invalid selector in site config: dl_selector='{selector}'")
    assert "[BD-" not in message
    assert len(seen["logs"]) == 1
    assert seen["events"] == []


# bd-fixer-B F3: the shared failed-job translator reads any object as text, as at base; only
# the worker seam adds the taxonomy code.
def test_existing_failure_translation_is_preserved():
    assert exceptions is not None, "exceptions capability missing"
    from bulk_downloader.friendly_error import friendly_error
    from bulk_downloader.runner import _translate_failed_message

    message = "404 Not Found"
    assert _translate_failed_message(message) == friendly_error(message)
    assert _translate_failed_message(Exception("HTTP Error 404: Not Found")) == (
        "Not found (404). URL is dead or moved.")
    assert _translate_failed_message(exceptions.ConfigurationError("bad setting")) == "bad setting"


# ---- R2: the row 987 advisor reads the RAW failure ------------------------------------------
# _update_job_current advises a failed job on the text it was published with (not the queue
# translation). The worker seam's "[<code>] " is metadata: the advice must be exactly the one
# the worker's own text gets -- no "[BD-" in it, and nothing of the advisor's 120-character
# summary (80-character operator message) spent on the code.
_LONG_UNCLASSIFIED = "renderer process gave up while saving the media element " + "z" * 60 + " TAIL-END"


def _advice_oracle(err, **context):
    """What the advisor itself says about ``err``: no bridge, no runner, no code."""
    from bulk_downloader import error_advisor

    return error_advisor.advise_error(err, context=context or None).to_dict()


def _real_worker_runner(clean_workdir, monkeypatch, site_id):
    """A real SiteRunner: the failure reaches the real queue update (translation + advice)."""
    from bulk_downloader import hooks, runner_telemetry
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    seen = {"logs": [], "events": []}
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *args, **kwargs: seen["logs"].append(args))
    monkeypatch.setattr(hooks, "fire_event", lambda *args, **kwargs: seen["events"].append((args, kwargs)))
    worker = SiteRunner(site_id, {"name": site_id, "max_retries": 0})
    worker.jobs[URL] = {"status": "running", "retries": 0}
    return worker, seen


@pytest.mark.parametrize("error, code, category", [
    (RuntimeError(_LONG_UNCLASSIFIED), "BD-GEN-000", "unknown"),
    (RuntimeError("[renderer] crashed while saving"), "BD-GEN-000", "unknown"),
    (sqlite3.OperationalError("database is locked"), "BD-STORE-001", "database_lock"),
    (RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
                  "local issuer certificate (_ssl.c:1006)"), "BD-GEN-000", "tls_certificate"),
], ids=["unclassified-long", "unclassified-own-bracket", "control-sqlite-lock", "control-tls"])
def test_failed_worker_job_is_advised_on_its_raw_text_without_the_code(
        clean_workdir, monkeypatch, error, code, category):
    from bulk_downloader.friendly_error import friendly_error

    worker, seen = _real_worker_runner(clean_workdir, monkeypatch, "row1023_r2")
    window = str(error)[:100]
    published, raw_text = f"worker error: [{code}] {window}", f"worker error: {window}"

    assert worker._publish_worker_exception(URL, error) == "failure"

    job = worker.jobs[URL]
    assert job["status"] == "failed", job
    # the code is still published (log, hook) and shown (queue translation), exactly once
    assert [log[6] for log in seen["logs"]] == [published]
    assert [event[1]["job"]["message"] for event in seen["events"]] == [published]
    assert job["message"] == friendly_error(published)
    advice = job["remediation"]
    assert advice == _advice_oracle(raw_text, site_id="row1023_r2", url=URL)
    assert advice["category"] == category
    assert "[BD-" not in repr(advice)
    if category == "unknown":
        assert job["message"] == published
        # "worker error: " + the 100 raw characters fit the advisor's 120: every one is shown
        assert advice["root_cause_summary"] == raw_text
        assert advice["operator_message"] == raw_text[:80]


def test_advisor_bridge_reads_published_worker_text_as_the_raw_text():
    from bulk_downloader.friendly_error import advise_error

    published_to_raw = (
        ("worker error: [BD-GEN-000] qzx row1023 nothing recognisable",
         "worker error: qzx row1023 nothing recognisable"),
        ("[network] worker error: [BD-CFG-001] timeout must be a positive integer",
         "[network] worker error: timeout must be a positive integer"),
        ("worker error: [BD-CUSTOM-001] [Errno 5] Input/output error",
         "worker error: [Errno 5] Input/output error"),
    )
    for published, raw in published_to_raw:
        assert advise_error(published).to_dict() == _advice_oracle(raw), published
    # Negative controls: text that carries no worker code is advised exactly as the advisor reads it.
    for text in (
        "worker error: [Errno 5] Input/output error",  # the raw text's own bracket, no code
        "qzx [BD-GEN-000] quoted mid-text",
        "download failed: worker error: [BD-GEN-000] not the published shape",
        "",
    ):
        assert advise_error(text).to_dict() == _advice_oracle(text), text
    lock = sqlite3.OperationalError("database is locked")  # an object reaches the advisor as itself
    advice = advise_error(lock)
    assert advice.to_dict() == _advice_oracle(lock)
    assert advice.category == "database_lock"
