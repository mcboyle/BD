"""RED-first repro for F-RUN03-03.

``runner_util._resolve_safe`` logged ``%r`` of the raw credential value in its
"resolve_password raised" branch, so a plaintext password lands in the log.
After the fix a log-safe typed indicator is logged instead of the value.

Uses a manually-attached handler (not pytest's caplog fixture) so it runs under
the sandbox band harness as well as pytest.

Pristine RED: the raw secret appears in a captured log record.
"""
import logging


def test_resolve_password_error_does_not_log_the_secret(monkeypatch):
    import bulk_downloader.secrets_store as ss

    def _boom(v):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ss, "resolve_password", _boom, raising=False)

    logger = logging.getLogger("bulk_downloader.runner")
    records = []

    class _Cap(logging.Handler):
        def emit(self, r):
            records.append(r.getMessage())

    h = _Cap()
    h.setLevel(logging.DEBUG)
    logger.addHandler(h)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        from bulk_downloader import runner_util as ru
        secret = "hunter2-SUPER-SECRET-pw"
        out = ru._resolve_safe(secret)
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)

    assert out == "", out
    joined = " ".join(records)
    assert secret not in joined, joined
