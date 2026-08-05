import json
from pathlib import Path

from bulk_downloader import ai_boot_status as status


def test_load_effective_config_uses_app_keys_and_defaults():
    cfg = status.load_effective_config({
        "ai_enabled": True,
        "ai_provider": "OLLAMA",
        "ai_endpoint": "http://user:secret@127.0.0.1:11434/api/",
        "ai_model_text": "text-model",
        "ai_model_vision": "vision-model",
    })
    assert cfg == {
        "enabled": True,
        "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434/api",
        "model_text": "text-model",
        "model_vision": "vision-model",
    }
    assert status.load_effective_config({}) == {
        "enabled": False,
        "provider": "ollama",
        "endpoint": "http://localhost:11434",
        "model_text": "qwen2.5:7b",
        "model_vision": "qwen2.5vl:7b",
    }


def test_write_then_read_current_status(tmp_path):
    path = tmp_path / "state" / "ai_boot_readiness.json"
    written = status.write_status(
        {"state": "ready", "models": {}},
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    loaded = status.read_status(path, now=1_100.0, boot_id="boot-a")
    assert loaded == written
    assert loaded["schema_version"] == 1
    assert loaded["updated_at"] == "1970-01-01T00:16:40Z"


def test_status_from_prior_boot_or_after_keepalive_is_stale(tmp_path):
    path = tmp_path / "state.json"
    status.write_status({"state": "ready"}, path, now=1_000.0, boot_id="boot-a")
    prior = status.read_status(path, now=1_001.0, boot_id="boot-b")
    expired = status.read_status(path, now=1_601.0, boot_id="boot-a")
    assert prior["state"] == "stale"
    assert prior["stale_reason"] == "previous_boot"
    assert expired["state"] == "stale"
    assert expired["stale_reason"] == "expired"


def test_missing_or_malformed_status_is_safe_unknown(tmp_path):
    missing = status.read_status(tmp_path / "missing.json", boot_id="boot-a")
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    malformed = status.read_status(bad, boot_id="boot-a")
    assert missing == {"schema_version": 1, "state": "unknown", "reason": "missing"}
    assert malformed == {"schema_version": 1, "state": "unknown", "reason": "malformed"}


def test_write_is_atomic_and_never_persists_url_credentials(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    replacements = []
    real_replace = status.os.replace

    def capture_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(status.os, "replace", capture_replace)
    status.write_status(
        {
            "state": "degraded",
            "endpoint": status.sanitize_endpoint(
                "http://name:password@localhost:11434/private"
            ),
        },
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    raw = path.read_text(encoding="utf-8")
    assert replacements and replacements[0][0].name.endswith(".tmp")
    assert replacements[0][1] == path
    assert "name" not in raw and "password" not in raw
    assert json.loads(raw)["endpoint"] == "http://localhost:11434/private"


# --------------------------------------------------------------------------- #
# @874 -- finality, and the two ways a marker lies                              #
# --------------------------------------------------------------------------- #

def test_abandoned_in_flight_marker_expires_well_before_the_stale_window(tmp_path):
    """Both halves in ONE test so a later edit cannot satisfy one by breaking
    the other.

    A bare in-flight token is ALREADY a lying marker today: a process SIGKILLed
    mid-run leaves `retrying` readable for the full 600s. So the marker has to
    expire on a timestamp the writer keeps refreshing, and the fresh half is
    what stops the TTL grading a LIVE run abandoned -- 60s in is longer than
    RestartSec=60 and well inside the measured 108s run.

    THE OFFSET IS CHOSEN TO DISCRIMINATE, not just to exceed. 400s is past
    IN_FLIGHT_TTL_SECONDS (300) and still well inside STALE_AFTER_SECONDS
    (600), so a fix that deleted the in-flight branch entirely would return the
    document verbatim as "retrying" and fail here. An offset past 600 would be
    satisfied by the pre-existing expiry check and would prove nothing about
    the new branch at all.

    (The recon plan specified 181, which only discriminates against a 180s TTL
    while its own fix section reasons carefully for 300. The reasoning was
    kept; the leftover literal was not.)
    """
    path = tmp_path / "flight.json"
    status.write_status({"state": "retrying"}, path, now=1000.0,
                        boot_id="boot-a", final=False)

    fresh = status.read_status(path, now=1060.0, boot_id="boot-a")
    assert fresh["state"] == "retrying" and fresh["final"] is False, (
        "a LIVE run 60s in was graded something other than in-flight: %r" % (fresh,))

    dead = status.read_status(path, now=1000.0 + 400, boot_id="boot-a")
    assert dead["state"] == "stale" and dead["stale_reason"] == "abandoned", (
        "an abandoned in-flight marker was still believed at +400s: %r" % (dead,))

    # The two windows must stay distinguishable. If IN_FLIGHT_TTL_SECONDS ever
    # reaches STALE_AFTER_SECONDS the abandoned branch becomes unreachable --
    # the expiry check would fire first -- and this whole test would pass while
    # asserting nothing about the marker.
    assert status.IN_FLIGHT_TTL_SECONDS < status.STALE_AFTER_SECONDS, (
        "the in-flight TTL must be strictly shorter than the stale window, or "
        "the abandoned-marker branch can never be reached")


def test_document_that_cannot_answer_finality_is_unknown_not_terminal(tmp_path):
    """The rolling-deploy case, and the only place "unknown is a third state"
    can be enforced.

    A document written by the pre-fix binary carries no `final` key. Defaulting
    that to True would grade it a terminal verdict -- which is this exact defect
    reintroduced by its own fix, and invisibly. Self-heals on the next write.
    """
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "schema_version": 1, "state": "degraded", "error_code": "ollama_unreachable",
        "boot_id": "boot-a", "updated_at": "1970-01-01T00:16:40Z"}), encoding="utf-8")
    out = status.read_status(path, now=1050.0, boot_id="boot-a")
    assert out["state"] == "unknown" and out["reason"] == "no_finality_marker", (
        "a document that cannot answer whether its run finished was reported "
        "as a terminal verdict: %r" % (out,))


def test_terminal_documents_are_never_downgraded_to_unknown_or_in_flight(tmp_path):
    """THE OVER-SENSITIVITY CLAMP, and it is what keeps the TTL narrow.

    The obvious "improvement" -- expire every document at IN_FLIGHT_TTL_SECONDS
    -- silently halves STALE_AFTER_SECONDS for terminal verdicts and turns a
    real, actionable `degraded: gpu_unavailable` into `stale`. That is the same
    information loss as the original defect, pointed the other way.
    """
    path = tmp_path / "terminal.json"
    for state in ("ready", "degraded", "not_applicable"):
        status.write_status({"state": state}, path, now=1000.0,
                            boot_id="boot-a", final=True)
        out = status.read_status(path, now=1050.0, boot_id="boot-a")
        assert out["state"] == state, (state, out)
        assert out["final"] is True, out

    # a terminal verdict must survive the WHOLE existing stale window unchanged
    status.write_status({"state": "degraded", "error_code": "gpu_unavailable"}, path,
                        now=1000.0, boot_id="boot-a", final=True)
    near = status.read_status(path, now=1000.0 + status.STALE_AFTER_SECONDS - 1,
                              boot_id="boot-a")
    assert near["state"] == "degraded" and near["error_code"] == "gpu_unavailable", (
        "a terminal verdict was expired early by the in-flight TTL: %r" % (near,))
