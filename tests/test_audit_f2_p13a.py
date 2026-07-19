"""Audit fixes F2 (template-store write atomicity) + P13-A (OpenVPN creds
create-private-from-birth). Repro-first: the atomicity/mode contracts below are
proven RED against pristine v3.66.197 and GREEN after the fix.

Runner contract: zero-arg test functions; no pytest builtins; derive paths via
tempfile; restore patched globals in try/finally.
"""
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from bulk_downloader import template_manager as tm
from bulk_downloader import vpn_openvpn as vo


# ───────────────────────── F2 — template-store atomicity ─────────────────────

def _write_reviewed(rd: Path, host: str, status: str = "enabled") -> str:
    name = f"{host}.template.json"
    payload = {"host": host, "status": status, "selectors": {"player": "video"}}
    (rd / name).write_text(json.dumps(payload, indent=2), "utf-8")
    return name


def test_f2_disable_reviewed_survives_commit_failure():
    """If the durable write fails, the pre-existing reviewed template is NOT
    truncated/lost. RED on pristine (bare write_text overwrites in place); GREEN
    after tmp+os.replace (the failed commit leaves the original intact)."""
    rd = Path(tempfile.mkdtemp())
    name = _write_reviewed(rd, "examplehost", status="enabled")

    orig = os.replace
    def _boom(src, dst, *a, **k):
        raise OSError("simulated commit failure")
    os.replace = _boom
    try:
        try:
            tm.disable_reviewed(name, reviewed_dir=str(rd))
        except OSError:
            pass  # fixed code may surface the failure; that's fine
    finally:
        os.replace = orig

    # The original enabled template must still be parseable and unchanged.
    data = json.loads((rd / name).read_text("utf-8"))
    assert data.get("status") == "enabled", (
        "reviewed template was mutated/corrupted despite a failed commit "
        f"(status={data.get('status')!r})"
    )


def test_f2_promote_draft_no_partial_on_commit_failure():
    """A failed commit during promote must not leave a half-written reviewed
    file in place. RED on pristine; GREEN after atomic write (garbage stays in
    the tmp sibling, the live name never appears)."""
    rd = Path(tempfile.mkdtemp())
    dd = Path(tempfile.mkdtemp())
    draft = {"host": "freshhost", "status": "enabled",
             "selectors": {"player": "video"}}
    (dd / "freshhost.template-draft.json").write_text(
        json.dumps(draft, indent=2), "utf-8")

    orig = os.replace
    os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    try:
        try:
            tm.promote_draft("freshhost.template-draft.json",
                             reviewed_dir=str(rd), drafts_dir=str(dd))
        except OSError:
            pass
    finally:
        os.replace = orig

    live = rd / "freshhost.template.json"
    assert not live.exists(), (
        "a partial reviewed template appeared at the live name after a failed "
        "commit (non-atomic write)"
    )


def test_f2_disable_reviewed_happy_path():
    """Functional regression guard: a normal disable flips status and the file
    stays valid JSON."""
    rd = Path(tempfile.mkdtemp())
    name = _write_reviewed(rd, "happyhost", status="enabled")
    res = tm.disable_reviewed(name, reviewed_dir=str(rd))
    assert res.get("ok") is True
    data = json.loads((rd / name).read_text("utf-8"))
    assert data.get("status") == "disabled"
    assert "disabled_at" in data


# ───────────────────────── P13-A — OpenVPN creds perms ───────────────────────

def test_p13a_conf_dir_is_owner_only():
    """The temp config dir holding .ovpn/.auth must be 0700 (owner-only). RED on
    pristine (mkdir defaults to umask, ~0755); GREEN after _ensure_conf_dir()
    forces 0700."""
    d = vo._ensure_conf_dir()
    mode = stat.S_IMODE(os.stat(d).st_mode)
    assert mode == 0o700, f"CONF_DIR mode is {oct(mode)}, expected 0o700"


def test_p13a_private_write_never_group_or_other_readable():
    """Credential files are created private-from-birth via os.open(...,0o600), so
    there is no create-then-chmod window where group/other can read them. RED on
    pristine (no _write_private helper); GREEN after the fix. Forced under a
    permissive umask to prove os.open masking, not a later chmod, does the work."""
    old_umask = os.umask(0o022)
    try:
        p = Path(tempfile.mkdtemp()) / "secret.auth"
        vo._write_private(p, "user\npass\n")
        mode = stat.S_IMODE(os.stat(p).st_mode)
        assert mode & 0o077 == 0, (
            f"credential file mode {oct(mode)} exposes group/other bits"
        )
        assert p.read_text("utf-8") == "user\npass\n"
    finally:
        os.umask(old_umask)
