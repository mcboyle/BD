"""D3 U8 contract — polish: completion sound, swipe gesture, a11y, e2e.

Covers:
  - useCompletionSound hook exists, AppShell calls it
  - WaitingJobRow implements swipe-to-cancel (pointer events present)
  - A11y: AttentionBanner has role=alert + aria-live
  - A11y: JobErrorModal event log has role=log + aria-live
  - A11y: ThroughputSparkline has role=img with aria-label
  - A11y: Skeleton has aria-hidden
  - A11y: error cards in all 5 routes have role=alert
  - frontend/package.json declares @playwright/test
  - frontend/playwright.config.ts and frontend/e2e/d3_smoke.spec.ts exist
  - frontend/BUNDLE_AUDIT.md exists

U8 makes NO backend changes — all polish is frontend.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / Path(*parts)


# ── Completion sound ────────────────────────────────────────────────


def test_d3_u8_completion_sound_hook_present():
    f = _frontend_file("src", "hooks", "useCompletionSound.ts")
    assert f.is_file(), f"missing {f}"


def test_d3_u8_completion_sound_reuses_queue_query():
    """The hook must use the same queryKey as AppShell's queue badge
    ('queue-v2-badge') — otherwise it opens a second pipe at 15s
    cadence for the same data."""
    src = _frontend_file("src", "hooks", "useCompletionSound.ts").read_text(
        encoding="utf-8",
    )
    assert '"queue-v2-badge"' in src or "'queue-v2-badge'" in src, (
        "useCompletionSound must reuse the queue-v2-badge query key "
        "so we don't open a second poll for the same data"
    )


def test_d3_u8_app_shell_mounts_completion_sound():
    """AppShell must call useCompletionSound so the ping fires
    regardless of which tab is active."""
    src = _frontend_file("src", "components", "AppShell.tsx").read_text(
        encoding="utf-8",
    )
    assert "useCompletionSound" in src, (
        "AppShell must mount useCompletionSound so the ping is global"
    )


def test_d3_u8_completion_sound_reads_localstorage_pref():
    """The U7 toggle wrote bd-sound-on-complete. The U8 hook must
    read THAT exact key — drift would silently break the toggle."""
    src = _frontend_file("src", "hooks", "useCompletionSound.ts").read_text(
        encoding="utf-8",
    )
    assert "bd-sound-on-complete" in src, (
        "useCompletionSound must read localStorage['bd-sound-on-complete'] "
        "— the same key Settings writes"
    )


def test_d3_u8_completion_sound_uses_web_audio():
    """Web Audio (oscillator/gain envelope), not a sound file. Sound
    files would balloon the bundle."""
    src = _frontend_file("src", "hooks", "useCompletionSound.ts").read_text(
        encoding="utf-8",
    )
    assert "AudioContext" in src
    assert "createOscillator" in src or "OscillatorNode" in src
    # Must NOT reference a sound file.
    assert ".mp3" not in src and ".wav" not in src and ".ogg" not in src


# ── Swipe-to-cancel ─────────────────────────────────────────────────


def test_d3_u8_waiting_row_has_pointer_handlers():
    """WaitingJobRow must implement pointer events for swipe gesture.
    Without these, swipe-to-cancel is a no-op."""
    src = _frontend_file("src", "components", "WaitingJobRow.tsx").read_text(
        encoding="utf-8",
    )
    for handler in ("onPointerDown", "onPointerMove", "onPointerUp"):
        assert handler in src, f"WaitingJobRow missing {handler}"


def test_d3_u8_waiting_row_swipe_no_extra_library():
    """The narrative explicitly avoids adding dnd-kit or framer-motion
    for this. Confirm no such imports landed in WaitingJobRow.
    (Mentions in comments are fine — we explain WHY we don't use them.)"""
    src = _frontend_file("src", "components", "WaitingJobRow.tsx").read_text(
        encoding="utf-8",
    )
    import re
    for forbidden in ("dnd-kit", "framer-motion", "use-gesture"):
        # Match only real import statements, not comments.
        pattern = re.compile(
            r'(?:^|\n)\s*import\b[^;\n]*["\']\S*' + re.escape(forbidden) + r'\S*["\']',
        )
        assert not pattern.search(src), (
            f"WaitingJobRow imports {forbidden} — U8 swipe should be "
            "pure pointer events to keep the bundle small"
        )


def test_d3_u8_waiting_row_tap_still_opens_modal():
    """A tap (drag < tolerance) must still fire onOpen. Without this
    the gesture eats normal taps on touch devices."""
    src = _frontend_file("src", "components", "WaitingJobRow.tsx").read_text(
        encoding="utf-8",
    )
    assert "onOpen(job)" in src, (
        "WaitingJobRow must still call onOpen on a non-drag tap"
    )


# ── A11y fixes ──────────────────────────────────────────────────────


def test_d3_u8_attention_banner_announces_via_role_alert():
    """The attention card is the load-bearing announce-able surface
    — captcha pops up while user is elsewhere, SR must alert."""
    src = _frontend_file("src", "components", "AttentionBanner.tsx").read_text(
        encoding="utf-8",
    )
    assert 'role="alert"' in src
    assert "aria-live" in src


def test_d3_u8_job_error_modal_log_announces():
    """The recent-events log inside the modal must be role=log with
    aria-live so SR users hear new events as they arrive."""
    src = _frontend_file("src", "components", "JobErrorModal.tsx").read_text(
        encoding="utf-8",
    )
    assert 'role="log"' in src
    assert "aria-live" in src


def test_d3_u8_sparkline_has_role_img():
    """Recharts SVG must be wrapped with role=img + a descriptive
    aria-label, or screen readers ignore it entirely."""
    src = _frontend_file("src", "components", "ThroughputSparkline.tsx").read_text(
        encoding="utf-8",
    )
    assert 'role="img"' in src
    assert "aria-label" in src


def test_d3_u8_skeleton_is_aria_hidden():
    """Skeleton placeholders must be aria-hidden — they're visual
    only, SR users get the surrounding container's aria-busy."""
    src = _frontend_file("src", "components", "ui", "skeleton.tsx").read_text(
        encoding="utf-8",
    )
    assert 'aria-hidden' in src


def test_d3_u8_error_cards_in_routes_have_role_alert():
    """Every 'Couldn't load X' error card across the 5 routes must
    be role=alert so the SR announces the failure."""
    routes = ["Home.tsx", "Sites.tsx", "Queue.tsx", "Activity.tsx", "Advanced.tsx"]
    for r in routes:
        src = _frontend_file("src", "routes", r).read_text(encoding="utf-8")
        # If the route has a red-soft error card, it must have role=alert.
        if "border-red bg-red-soft" in src:
            # The role=alert may be on the same Card or on a parent.
            # Look for either: role="alert" within 200 chars of the
            # error styling.
            import re
            m = re.search(
                r"(?s)border-red bg-red-soft.{0,400}?role=\"alert\"|role=\"alert\".{0,400}?border-red bg-red-soft",
                src,
            )
            assert m, (
                f"routes/{r}: error card has no role=alert nearby — "
                f"SR users won't hear the failure"
            )


# ── Bundle audit + e2e wiring ──────────────────────────────────────


def test_d3_u8_bundle_audit_doc_present():
    f = _frontend_file("BUNDLE_AUDIT.md")
    assert f.is_file(), f"missing {f}"


def test_d3_u8_bundle_audit_doc_has_results():
    """The doc must have actual numbers, not just a 'TODO'."""
    src = _frontend_file("BUNDLE_AUDIT.md").read_text(encoding="utf-8")
    assert "Unused runtime deps" in src
    # Must include the explicit zero-unused result.
    assert "0" in src
    # Must reference real packages that were audited.
    assert "react" in src
    assert "recharts" in src


def test_d3_u8_playwright_declared():
    """@playwright/test must be in devDependencies."""
    pkg = json.loads(
        _frontend_file("package.json").read_text(encoding="utf-8")
    )
    devs = pkg.get("devDependencies", {})
    assert "@playwright/test" in devs, (
        "package.json missing @playwright/test devDep"
    )


def test_d3_u8_playwright_scripts_present():
    """e2e and e2e:install scripts wired."""
    pkg = json.loads(
        _frontend_file("package.json").read_text(encoding="utf-8")
    )
    scripts = pkg.get("scripts", {})
    assert "e2e" in scripts
    assert "playwright test" in scripts["e2e"]


def test_d3_u8_playwright_config_present():
    f = _frontend_file("playwright.config.ts")
    assert f.is_file(), f"missing {f}"


def test_d3_u8_e2e_spec_present():
    f = _frontend_file("e2e", "d3_smoke.spec.ts")
    assert f.is_file(), f"missing {f}"


def test_d3_u8_e2e_spec_covers_load_bearing_flows():
    """The smoke spec must cover the load-bearing flows so a future
    regression is caught at the e2e layer. Pin the names so a refactor
    can't silently drop one."""
    src = _frontend_file("e2e", "d3_smoke.spec.ts").read_text(encoding="utf-8")
    # Each load-bearing flow must appear by name.
    for flow in (
        "tab navigation",
        "command palette",      # ⌘K
        "theme",                # dark mode
        "Add",                  # Add site wizard
        "Advanced",             # Settings sub-page
        "additive",             # D3 must not break / and /m
    ):
        assert flow.lower() in src.lower(), (
            f"e2e smoke spec missing coverage of: {flow!r}"
        )


def test_d3_u8_e2e_spec_targets_m2_path():
    """RE-EXPRESSED at v3.66.203 (Phase 1 root flip): the SPA lives at
    the site root. e2e must target `${BASE}/...` root paths and must
    NOT navigate to /m2 (now a 302 shim — testing through the shim
    would mask a broken root mount)."""
    src = _frontend_file("e2e", "d3_smoke.spec.ts").read_text(encoding="utf-8")
    assert "${BASE}/`" in src, "e2e spec must target the root SPA mount"
    assert "${BASE}/m2" not in src, \
        "e2e spec must not navigate via the /m2 shim post-root-flip"
