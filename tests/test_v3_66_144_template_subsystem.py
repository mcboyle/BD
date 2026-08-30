"""v3.66.144 — reviewed-template subsystem regression tests.

Covers the five Goal-4 guarantees for the reviewed/approved template path
(template_registry + template_assist + tools/onboard_site_template) plus the
runner merge integration:

  1. onboarding detects an approved (enabled) template,
  2. auto_teach_first_run is disabled once an approved template exists,
  3. SiteRunner backward-compat wrappers still exist,
  4. navigation/homepage URLs are rejected as downloads (no download.bin),
  5. manual-auth profile sync copies login storage safely (LOCK-skip + backup),
  6. the reviewed reptyle template's modal trigger + modal rows reach learned_dl.

These exercise only reviewed-template selectors/patterns and the safe-copy of
login-continuity storage. No signed URLs, tokens, cookies, or challenge
artifacts are stored or asserted on.
"""
from __future__ import annotations

from pathlib import Path

from tools.onboard_site_template import plan_site, enabled_template_exists
from bulk_downloader import profile_sync
from bulk_downloader.template_assist import merge_template_download_hints
import bulk_downloader.candidate_filter as cf


REPTYLE_HOME = "https://app.reptyle.com/"
REPTYLE_MOVIE = "https://app.reptyle.com/movie/123"


class _FakePage:
    """Minimal Playwright-page stand-in: template_assist only reads .url."""
    def __init__(self, url):
        self.url = url


# --- Goal 4.1: onboarding detects approved templates -----------------------

def test_onboarding_detects_approved_template():
    # The reviewed app.reptyle.com template ships enabled in templates/reviewed.
    assert enabled_template_exists(REPTYLE_HOME) is True
    plan = plan_site({"login_url": REPTYLE_HOME})
    assert plan["template_onboarding"] == "approved_template_found"
    assert plan["template_auto_detect_mode"] == "reviewed"


def test_onboarding_marks_capture_required_without_template():
    # A host with no reviewed template must fall to capture_required.
    assert enabled_template_exists("https://no-such-host.example/") is False
    plan = plan_site({"login_url": "https://no-such-host.example/"})
    assert plan["template_onboarding"] == "capture_required"
    assert plan["template_auto_detect_mode"] == "capture_then_review"


# --- Goal 4.2: auto_teach_first_run disabled when approved template exists --

def test_auto_teach_disabled_when_approved_template_exists():
    plan = plan_site({"login_url": REPTYLE_MOVIE})
    assert plan["template_onboarding"] == "approved_template_found"
    assert plan["auto_teach_first_run"] is False


def test_auto_teach_disabled_for_capture_required_too():
    # auto_teach is forced off in both branches so the teach window never pops
    # for an onboarded site.
    plan = plan_site({"login_url": "https://no-such-host.example/"})
    assert plan["auto_teach_first_run"] is False


# --- Goal 4.3: SiteRunner compatibility wrappers exist ---------------------

def test_siterunner_compat_wrappers_exist():
    from bulk_downloader.runner import SiteRunner
    # Documented backward-compat alias retained for Phase 7.2 / 15.10 callsites.
    assert callable(getattr(SiteRunner, "_try_turnstile_solve", None))
    assert callable(getattr(SiteRunner, "_try_captcha_solve", None))
    # Core lifecycle / manual-login surface the template + handoff flow relies on.
    for name in ("start", "pause", "stop",
                 "start_manual_login", "finish_manual_login"):
        assert callable(getattr(SiteRunner, name, None)), f"missing SiteRunner.{name}"


# --- Goal 4.4: navigation URLs rejected as downloads -----------------------

def test_navigation_homepage_url_rejected_as_download():
    v = cf.classify(
        url=REPTYLE_HOME, text="Home", classes="",
        ancestor_text="", selector="a[href]", tag="a",
        page_host="app.reptyle.com",
    )
    assert v.accepted is False
    assert v.kind == "rejected"
    assert "homepage" in v.reason.lower()


def test_real_download_resolution_url_accepted():
    # Contrast: the reviewed download-resolution endpoint is a valid download.
    v = cf.classify(
        url="https://api2.reptyle.com/api/v1/movie/123/download-resolution/2160",
        text="2160p", classes="", ancestor_text="Download",
        selector='[role="dialog"] a[href*="download-resolution"]',
        tag="a", page_host="app.reptyle.com",
    )
    assert v.accepted is True
    assert v.kind == "download"


# --- Goal 4.5: manual auth sync copies profile storage safely --------------

def test_manual_auth_sync_copies_storage_safely(tmp_path):
    root = tmp_path / "profiles"
    site = "sitex"

    # Source: a freshly-logged-in manual profile with a cookie DB and a
    # leveldb store that includes the volatile LOCK file.
    man = root / site / "manual" / "Default"
    (man / "Local Storage" / "leveldb").mkdir(parents=True)
    (man / "Cookies").write_bytes(b"new-cookie-db")
    (man / "Local Storage" / "leveldb" / "000003.ldb").write_bytes(b"ls-data")
    (man / "Local Storage" / "leveldb" / "LOCK").write_bytes(b"")

    # Existing main runtime profile with an OLD cookie — must be backed up.
    main = root / site / "main" / "Default"
    main.mkdir(parents=True)
    (main / "Cookies").write_bytes(b"old-cookie-db")

    summ = profile_sync.sync_manual_to_runtime(
        site, profiles_root=str(root), ensure=("main",))

    # New cookie copied over the old one.
    assert (main / "Cookies").read_bytes() == b"new-cookie-db"
    # Local Storage leveldb copied...
    assert (main / "Local Storage" / "leveldb" / "000003.ldb").exists()
    # ...but the leveldb LOCK file was skipped.
    assert not (main / "Local Storage" / "leveldb" / "LOCK").exists()
    # Old cookie preserved in a timestamped backup at the profile root.
    backups = sorted((root / site / "main").glob(".sync_backups/*/Cookies"))
    assert backups, "expected a .sync_backups copy of the replaced Cookies"
    assert backups[-1].read_bytes() == b"old-cookie-db"
    # Summary reports main as synced (at least one item copied).
    assert "main" in summ.get("synced", {})


# --- Goal 3 integration: reviewed template reaches the download path -------

def test_reviewed_template_feeds_modal_flow_into_learned_dl():
    merged, tmpl = merge_template_download_hints(_FakePage(REPTYLE_MOVIE), {})
    assert tmpl is not None and tmpl.get("host") == "app.reptyle.com"
    trig = merged.get("trigger_selectors") or []
    rows = merged.get("row_selectors") or []
    # "Download Full Movie" is a first-class trigger (opens the modal)...
    assert any("Download Full Movie" in s for s in trig)
    # ...and the merged row selectors preserve the recorded modal-button
    # contract: one selector per observed quality plus a modal-scoped fallback.
    assert rows
    assert all(".ant-modal.download-modal" in s for s in rows)
    assert all("button.modal-download-button" in s for s in rows)
    for resolution in ("720p", "1080p", "2160p"):
        assert any(f'div:text-is("{resolution}")' in s for s in rows)
    assert ".ant-modal.download-modal button.modal-download-button" in rows
    # The reviewed selectors are tried before any generic learned ones.
    assert merged.get("_template_host") == "app.reptyle.com"


def test_merge_is_noop_without_a_template():
    merged, tmpl = merge_template_download_hints(
        _FakePage("https://no-such-host.example/"), {"row_selectors": ["x"]})
    assert tmpl is None
    assert merged == {"row_selectors": ["x"]}
