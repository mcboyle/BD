"""Opt-in M21 candidate checks; all registry/cache/ledger state is fixture-local."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_MOD_M21_SAY_PREVALIDATOR_CANDIDATE")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="M21 candidate opt-in required")


@pytest.fixture
def cli(tmp_path, monkeypatch):
    root = Path(CANDIDATE)
    monkeypatch.setenv("BD_SAY_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("BD_SAY_LOG", str(tmp_path / "say.log"))
    monkeypatch.setenv("BD_SEAT", "sender")
    monkeypatch.delenv("BD_SAY_KIND", raising=False)
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop("server", None)
    spec = importlib.util.spec_from_file_location("m21_cli", root / "cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys.modules["server"], "_targets", lambda: {"seat-A", "seat-B", "seat-C", "seat-D", "sender"})
    yield module
    sys.modules.pop("server", None)


def payload(message, target="seat-D", tool="mcp__bd__say"):
    args = {"target": target, "text": message} if tool != "Bash" else {
        "command": f"/home/mboyle/bd-say.sh {target} '{message}'"
    }
    return json.dumps({"tool_name": tool, "tool_input": args})


def ledger(tmp_path, message, age=10, targets=("seat-A", "seat-B", "seat-C"), pointer=False):
    stamped = message if message.startswith(("[from sender] ", '{"from":', '{"status":')) else f"[from sender] {message}"
    digest = hashlib.sha1(stamped.encode()).hexdigest()[:8]
    stamp = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat()
    rows = [f"{stamp}\tsender\t{target}\tsent\t20\t{'pointer' if pointer else digest}\tbody\tmode=file\tpayload-sha={digest}\n" for target in targets]
    (tmp_path / "say.log").write_text("".join(rows))


@pytest.mark.parametrize("tool", ["Bash", "mcp__bd__say"])
def test_fourth_broadcast_is_denied(cli, tmp_path, tool):
    ledger(tmp_path, "READY")
    with pytest.raises(ValueError, match="broadcast limiter: this text already went to 3 targets in 5 min"):
        cli.hook(payload("READY", tool=tool))


def test_pointer_payload_sha_is_counted(cli, tmp_path):
    ledger(tmp_path, "READY", pointer=True)
    with pytest.raises(ValueError, match="broadcast limiter"):
        cli.hook(payload("READY"))


@pytest.mark.parametrize("message,age,kind", [("STOP now", 10, ""), ("LIMIT now", 10, ""), ("READY", 301, ""), ("READY", 10, "bootstrap")])
def test_broadcast_exemptions_and_expiry(cli, tmp_path, monkeypatch, message, age, kind):
    ledger(tmp_path, message, age=age)
    monkeypatch.setenv("BD_SAY_KIND", kind)
    assert all(result["valid"] for result in cli.hook(payload(message)))


def test_existing_recipient_is_excluded_like_live_bcast_limit(cli, tmp_path):
    ledger(tmp_path, "READY")
    assert cli.hook(payload("READY", target="seat-C"))[0]["valid"]


def test_repeated_ledger_rows_do_not_count_as_distinct_targets(cli, tmp_path):
    ledger(tmp_path, "READY", targets=("seat-A", "seat-A", "seat-B"))
    assert cli.hook(payload("READY"))[0]["valid"]


def test_atomic_batch_cannot_add_a_fourth_target(cli, tmp_path):
    ledger(tmp_path, "READY", targets=("seat-A", "seat-B"))
    with pytest.raises(ValueError, match="broadcast limiter"):
        cli.hook("bd-say seat-C READY; bd-say seat-D READY")
    assert not (tmp_path / "cache.json").exists()


def test_long_prose_is_rejected_with_existing_rule(cli):
    result = cli.hook(payload("x" * 31))[0]
    assert not result["valid"]
    assert "length_exceeded: >30 chars without path" in result["violations"]


def test_twenty_char_message_with_path_is_allowed(cli):
    assert cli.hook(payload("READY /tmp/result.md"))[0]["valid"]


def test_dynamic_say_is_rejected(cli):
    with pytest.raises(ValueError, match="dynamic argument"):
        cli.hook('bd-say "$TARGET" "$MESSAGE"')


def test_same_target_third_repeat_remains_suppressed(cli):
    assert cli.hook(payload("repeat"))[0]["valid"]
    assert cli.hook(payload("repeat"))[0]["valid"]
    assert cli.hook(payload("repeat"))[0]["violations"] == ["duplicate_repeat_suppressed"]


def test_non_say_has_no_reservations(cli, tmp_path):
    assert cli.hook("echo bd-say") == []
    assert not (tmp_path / "cache.json").exists()


def test_exact_three_hundred_second_boundary_denies(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    ledger(tmp_path, "READY", age=300)
    with pytest.raises(ValueError, match="broadcast limiter"):
        cli.hook(payload("READY"))


def test_stamped_message_matches_live_ledger_identity(cli, tmp_path):
    ledger(tmp_path, "[from sender] READY")
    with pytest.raises(ValueError, match="broadcast limiter"):
        cli.hook(payload("[from sender] READY"))


def test_literal_quoted_command_is_still_validated(cli):
    result = cli.hook("'/home/mboyle/bd-say.sh' seat-D '" + "x" * 31 + "'")[0]
    assert result["violations"] == ["length_exceeded: >30 chars without path"]


def test_cli_process_exit_two_carries_exact_rule(cli, tmp_path, monkeypatch):
    helper = tmp_path / "roles.sh"
    helper.write_text("#!/bin/sh\nprintf 'worker\\tseat-D\\n'\n")
    monkeypatch.setenv("BD_ROLE_CLAIM_HELPER", str(helper))
    result = subprocess.run([sys.executable, str(Path(CANDIDATE) / "cli.py"), "hook"],
                            input=payload("x" * 31), text=True, capture_output=True, timeout=20)
    assert result.returncode == 2
    assert "length_exceeded: >30 chars without path" in result.stderr
    assert result.stdout == ""


def test_native_plugin_checks():
    result = subprocess.run(["claude", "plugin", "test", str(Path(CANDIDATE) / "bd-guard")],
                            text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
