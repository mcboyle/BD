"""D1: credential-bearing login URLs never escape as status or evidence."""

import ast
import json
from pathlib import Path


BD_GATE_SCOPE = "module"


def test_credential_query_is_redacted_from_login_evidence(tmp_path):
    from bulk_downloader.login_impl.replay import member_state_check

    secret = "d1-password-not-for-evidence"
    final_url = (
        "https://login.example.invalid/complete?username=alice&password="
        f"{secret}&continue=members")

    class Page:
        url = final_url

        def content(self):
            return f'<html><a href="{final_url}">member page</a></html>'

    matched, why, evidence_path = member_state_check(
        Page(), {"login_evidence_dir": str(tmp_path)}, tag="login-d1")

    assert matched is False
    evidence = Path(evidence_path).read_text(encoding="utf-8")
    assert secret not in why
    assert secret not in evidence
    assert "username=<REDACTED>" in evidence
    assert "password=<REDACTED>" in evidence
    assert "continue=members" in evidence  # noncredential positive control


def test_credential_query_is_redacted_from_api_status(fresh_app):
    from bulk_downloader.app_state import runners

    secret = "d1-password-not-for-status"
    response = fresh_app.post("/api/sites", json={
        "name": "d1-status",
        "username": "alice",
        "password": "stored-password",
    })
    sid = response.get_json()["id"]
    runners[sid]._login_status = (
        "Expected URL contains '/members', got "
        f"https://login.example.invalid/complete?username=alice&password={secret}&next=home")

    payload = fresh_app.get("/api/status").get_json()[sid]
    rendered = json.dumps(payload)
    assert secret not in rendered
    assert "username=<REDACTED>" in payload["login_status"]
    assert "password=<REDACTED>" in payload["login_status"]
    assert "next=home" in payload["login_status"]  # noncredential positive control


# Row 815: the two tests above leave the runner_auth.py leg unproven. The first
# reaches login_impl/replay.py only, and the second assigns _login_status
# DIRECTLY, bypassing the AuthMixin._set_login_status wrapper that is the whole
# point of the D1 conversion -- so deleting the redaction call in that wrapper,
# or reverting any of the 12 converted call sites to a raw assignment, kept both
# of them green. The three tests below close exactly that gap.


class _AuthCarrier:
    """A bare host for the mixin: _set_login_status touches nothing else."""

    def __init__(self):
        self._login_status = ""


def test_set_login_status_redacts_before_storing():
    """FAILS when the redact_url_credentials call in AuthMixin._set_login_status
    is deleted, PASSES when it is restored -- the wrapper's own body, which the
    credleak-d1 self-mutation reported ESCAPED at runner_auth.py:214."""
    from bulk_downloader.runner_auth import AuthMixin

    class Carrier(_AuthCarrier, AuthMixin):
        pass

    secret = "d1-password-not-for-runner-status"
    carrier = Carrier()
    carrier._set_login_status(
        "Expected URL contains '/members', got "
        f"https://login.example.invalid/complete?username=alice&password={secret}&next=home")

    assert secret not in carrier._login_status
    assert "username=<REDACTED>" in carrier._login_status
    assert "password=<REDACTED>" in carrier._login_status
    assert "next=home" in carrier._login_status  # noncredential positive control


def test_no_converted_site_writes_login_status_raw():
    """Kills a raw `self._login_status = ...` reintroduced at any of the sites
    the D1 cut converted: the wrapper is the single writer, and every other site
    reaches it by call. AST, not text, so a comment or a string cannot fail an
    unchanged subject and cannot hide a real assignment either."""
    from bulk_downloader import runner_auth

    source = Path(runner_auth.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    def is_login_status_target(node):
        return (isinstance(node, ast.Attribute)
                and node.attr == "_login_status"
                and isinstance(node.value, ast.Name)
                and node.value.id == "self")

    wrapper_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set_login_status":
            wrapper_lines.update(range(node.lineno, node.end_lineno + 1))
    assert wrapper_lines, "_set_login_status is gone from runner_auth.py"

    raw_writes = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if is_login_status_target(target) and node.lineno not in wrapper_lines:
                raw_writes.append(node.lineno)

    call_sites = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "_set_login_status"]

    assert raw_writes == [], (
        "these runner_auth.py lines write _login_status without redacting it: "
        f"{raw_writes}")
    # Exact-count assertion: the conversion's own population, nonzero, so that
    # deleting the call sites cannot make this test vacuously pass.
    assert len(call_sites) >= 12, (
        f"expected the 12 converted call sites, found {len(call_sites)}")
