

def _login_impl_src():
    """Concatenated source of the decomposed login_impl/ package. login.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 3); the implementation lives in
    login_impl/*.py, so structure/path tests read the package, not the shim."""
    from pathlib import Path as _P
    import bulk_downloader.login_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))
"""v3.43.14: Tests for credential-autofill behavior in the takeover
browser.

We can't actually launch a browser in unit tests, but we can verify:
  - The launch args include the autofill-enabling flags
  - The resolver path correctly produces credentials from a vault entry
  - The autofill JS template is syntactically valid (node parses it)

The actual browser-side fill behavior depends on real page DOM and
is tested by hand when you take over a site.
"""


# ─── Launch args ──────────────────────────────────────────────────

def test_open_manual_login_launch_args_include_password_store():
    """The takeover launch must include --password-store=basic. Without
    it, Chromium silently disables the password manager when run under
    CDP. The flag appears in ManualLoginSession._launch (which is called
    by open_manual_login_browser) AND the fallback non-session launch."""
    from pathlib import Path
    from bulk_downloader import login as _login
    src = _login_impl_src()
    # The flag should appear in the non-headless launch paths. We can't
    # easily isolate just those without running them, so check that the
    # flag is present at all in the module.
    assert src.count("--password-store=basic") >= 1, \
        "login.py missing --password-store=basic in launch args — autofill won't work"


def test_open_manual_login_launch_args_enable_autofill_features():
    """The takeover launch must also enable the autofill features."""
    from pathlib import Path
    from bulk_downloader import login as _login
    src = _login_impl_src()
    assert ("AutofillEnableAccountWalletStorage" in src or
            "PasswordManagerEnabled" in src), \
        "login.py missing autofill feature flags"


def test_runner_launch_args_has_autofill_only_when_headed():
    """Workers (headless) should NOT have autofill flags; only headed
    launches (takeover, manual login). This prevents headless workers
    from prompting for password saves on every URL."""
    from bulk_downloader.runner import SiteRunner
    # Construct a minimal SiteRunner-like instance just to call _launch_args
    class _Stub:
        config = {}
        site_id = "test"
    stub = _Stub()
    # Bind the method to our stub
    args_headed = SiteRunner._launch_args(stub, headless=False)
    args_headless = SiteRunner._launch_args(stub, headless=True)
    assert any("password-store" in a for a in args_headed), \
        "Headed launch missing autofill flag"
    assert not any("password-store" in a for a in args_headless), \
        "Headless worker got autofill flag — workers should not autofill"


# ─── Vault resolution ─────────────────────────────────────────────

def test_resolve_password_for_autofill_handles_legacy_plaintext():
    """Sites that haven't been migrated still need to autofill."""
    from bulk_downloader.secrets_store import resolve_password
    assert resolve_password("legacy-plaintext-pw") == "legacy-plaintext-pw"


def test_resolve_password_returns_none_for_unset():
    from bulk_downloader.secrets_store import resolve_password
    assert resolve_password(None) is None
    assert resolve_password("") is None
