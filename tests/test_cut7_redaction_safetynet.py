"""Cut 7 (7.6) — redaction safety-net: an LLM SECOND PASS over the
already-redacted artifact that FLAGS secret-shaped values the deterministic
rules missed (a novel token format, a secret in an unexpected field). Suspects
go to human review.

Hard boundaries (enforced by these tests):
  * The deterministic redactor (`redact_artifact` / `scan_artifact_secrets`) is
    the source of truth and is UNCHANGED — same output with or without the LLM.
  * The LLM pass is ADDITIVE and READ-ONLY: it returns extra suspect findings;
    it never un-redacts, never mutates the artifact, and CANNOT clear a
    deterministic finding (even a hostile model that says "this is fine").
  * No model -> no guessing (returns []). A clean artifact -> no new flags
    (no false-positive storm; the model controls what is flagged).

New surface: `llm_residual_suspects(redacted_obj, *, llm=None)
-> [(json_path, "llm_suspect"), …]`, where `llm` is an injected
`Callable[[str], bool]` (production wires the local model; tests pass a fake).

RED on pristine 376: `llm_residual_suspects` does not exist.
"""


def test_deterministic_scan_unchanged_is_regression_guard():
    from bulk_downloader import capture_artifact_redact as r
    # A redacted artifact still scans clean; a raw secret still scans dirty.
    red = r.redact_artifact({"auth": "Bearer abcdefabcdefabcdefabcdefabcdef1234567890"})
    assert r.scan_artifact_secrets(red) == []
    dirty = r.scan_artifact_secrets({"x": "user:pass@host.example.com/path"})
    assert dirty  # non-empty: deterministic detector still fires


def test_no_llm_means_no_suspects():
    from bulk_downloader import capture_artifact_redact as r
    red = r.redact_artifact({"note": "nothing-secret-here"})
    assert r.llm_residual_suspects(red, llm=None) == []


def test_clean_artifact_no_false_positive_storm():
    from bulk_downloader import capture_artifact_redact as r

    def conservative_llm(_value):
        return False  # model flags nothing

    red = r.redact_artifact({"title": "My Holiday Video", "n": "12 of 30"})
    assert r.llm_residual_suspects(red, llm=conservative_llm) == []


def test_secret_in_unusual_field_gets_flagged():
    from bulk_downloader import capture_artifact_redact as r
    # A secret-shaped value the deterministic rules don't catch (spaces break
    # the opaque-token charset; no kv keyword, no email/jwt/userinfo/query), so
    # scan_artifact_secrets misses it — exactly the gap the LLM pass covers.
    novel = "session passphrase quanta 8841"
    artifact = {"metadata": {"vendor_blob": novel}}
    assert r.scan_artifact_secrets(artifact) == []  # deterministic miss

    def flagging_llm(value):
        return value == novel

    suspects = r.llm_residual_suspects(artifact, llm=flagging_llm)
    paths = [p for p, _ in suspects]
    kinds = {k for _, k in suspects}
    assert any("vendor_blob" in p for p in paths)
    assert kinds == {"llm_suspect"}


def test_llm_pass_cannot_clear_a_deterministic_finding():
    from bulk_downloader import capture_artifact_redact as r
    artifact = {"creds": "user:secretpw@host.example.com"}
    before = r.scan_artifact_secrets(artifact)
    assert before  # deterministic finding present

    # A hostile model that tries to "clear" everything.
    def hostile_llm(_value):
        return False

    _ = r.llm_residual_suspects(artifact, llm=hostile_llm)
    after = r.scan_artifact_secrets(artifact)
    # The deterministic finding is untouched; the artifact is not mutated.
    assert after == before


def test_llm_pass_does_not_mutate_or_unredact():
    from bulk_downloader import capture_artifact_redact as r
    red = r.redact_artifact({"auth": "Bearer abcdefabcdefabcdefabcdefabcdef1234567890"})
    import copy
    snapshot = copy.deepcopy(red)

    def yes_llm(_value):
        return True  # flags everything

    r.llm_residual_suspects(red, llm=yes_llm)
    assert red == snapshot  # input artifact unchanged (no un-redaction)


def test_llm_pass_survives_model_error():
    from bulk_downloader import capture_artifact_redact as r

    def boom(_value):
        raise RuntimeError("ollama down")

    red = r.redact_artifact({"x": "some-value-here-that-is-long-enough"})
    # Best-effort: a model error degrades to "no suspects", never fatal.
    assert r.llm_residual_suspects(red, llm=boom) == []
