"""Cut 7 (7.4) — LLM failure-triage extension over the static reason dict.

`failure_reasons` today: `reason_for(message, status_code)` always coerces to one
of four buckets (transient|rate_limited|auth|permanent) because the underlying
`retry_policy.classify_failure` has an OPTIMISTIC default — a genuinely
unrecognized error and a matched "transient" both return "transient", so the
"uncategorized tail" is invisible.

7.4 adds, WITHOUT touching the retry hot path:
  * `is_uncategorized(message, status_code) -> bool`
      True ONLY when classify would fall through to its default (no status code,
      no positive pattern match). False for any status or positive match.
  * `triage_unknown(message, context="", *, llm=None) -> dict | None`
      Advisory only. Invoked on an uncategorized tail; calls the
      dependency-injected `llm` callable, VALIDATES its proposed bucket against
      the known reason set, and returns
      `{reason_code, suggested_action, advisory: True}` or None
      (no llm / invalid / out-of-vocabulary). Never mutates retry behavior and
      never lets a free-form category leak downstream.

`reason_for` itself must stay byte-stable (regression guard).

RED on pristine 376: `is_uncategorized` / `triage_unknown` do not exist; the
back-compat asserts on `reason_for` still pass.
"""


def test_reason_for_known_messages_unchanged():
    # Regression guard: the deterministic core is untouched.
    from bulk_downloader import failure_reasons as fr
    assert fr.reason_for("401 Unauthorized", status_code=401)["category"] == "auth"
    assert fr.reason_for("429 Too Many Requests", status_code=429)["category"] == "rate_limited"
    assert fr.reason_for("404 Not Found", status_code=404)["category"] == "permanent"
    assert fr.reason_for("connection reset by peer")["category"] == "transient"


def test_is_uncategorized_true_only_on_genuine_tail():
    from bulk_downloader import failure_reasons as fr
    # A status code is always a positive signal -> not the tail.
    assert fr.is_uncategorized("whatever", status_code=503) is False
    assert fr.is_uncategorized("whatever", status_code=404) is False
    # A positively-matched message pattern -> not the tail.
    assert fr.is_uncategorized("connection reset by peer") is False
    assert fr.is_uncategorized("401 unauthorized") is False
    # No status, no recognized pattern -> the uncategorized tail.
    assert fr.is_uncategorized("flibbertigibbet exploded mid-frobnicate") is True
    assert fr.is_uncategorized("") is True


def test_triage_unknown_maps_into_valid_bucket():
    from bulk_downloader import failure_reasons as fr

    def fake_llm(_prompt):
        # Model proposes a bucket from the known vocabulary.
        return {"reason_code": "auth", "suggested_action": "rotate the token"}

    out = fr.triage_unknown("token rejected by upstream", llm=fake_llm)
    assert out is not None
    assert out["reason_code"] in fr._REASONS
    assert out["reason_code"] == "auth"
    assert out["advisory"] is True
    assert out["suggested_action"]


def test_triage_unknown_rejects_out_of_vocabulary():
    from bulk_downloader import failure_reasons as fr

    def fake_llm(_prompt):
        return {"reason_code": "cosmic_rays", "suggested_action": "pray"}

    # An out-of-vocab category must NOT leak downstream.
    assert fr.triage_unknown("weird failure", llm=fake_llm) is None


def test_triage_unknown_none_without_llm():
    from bulk_downloader import failure_reasons as fr
    # No model injected -> advisory unavailable, never guesses.
    assert fr.triage_unknown("weird failure", llm=None) is None


def test_triage_unknown_survives_llm_error():
    from bulk_downloader import failure_reasons as fr

    def boom(_prompt):
        raise RuntimeError("ollama down")

    # Advisory path is best-effort; a model failure is never fatal.
    assert fr.triage_unknown("weird failure", llm=boom) is None
