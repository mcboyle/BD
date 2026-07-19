"""v3.66.209 — confirm-tier model + error-toast detail.

RED-provable against the pristine v3.66.208 zip (every test below FAILS
there) and GREEN on 209.

The model (operator decision, 2026-06-12):
  Tier A (destructive / irreversible / security posture): yes/no dialog,
    **No is the default** (Cancel autofocused), Confirm styled destructive.
    NO typed token entry anywhere — the typing requirement is retired.
  Tier B (reversible / additive / low blast radius): single-tap confirm
    dialog. Includes RESUME ALL, PAUSE ALL, REGEN NFOS, START/CANCEL SCAN,
    CREATE BACKUP, SMOKE, DRY-RUN restore, bulk tag ops, payload imports,
    drift/budget reset.
  Invariant retained: NOTHING is one-click — every write still arms a
    Pending and dispatches from a confirm dialog.

Plus: ApiError.message now carries the backend's descriptive `error`
field so toasts show WHY (e.g. the scan/start 400 reason), not just
"POST /x → 400".
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

ROUTES = SRC / "routes"


def _read(name: str) -> str:
    return (ROUTES / name).read_text(encoding="utf-8")


# The 209 defect: the original pin enumerated the converted file list, so it
# passed while 19 typed dialogs survived in 5 unswept route files. A sweep pin
# must sweep the TREE, never a hardcoded list (SANDBOX_OPS_NOTES learning).
_TYPED_PATTERNS = (
    re.compile(r"set\w*Typed\b"),
    re.compile(r"type to confirm", re.IGNORECASE),
    re.compile(r"to confirm:"),
    re.compile(r"typed !=="),
    re.compile(r"Type the confirmation"),
)


def _all_src_files():
    for p in SRC.rglob("*"):
        if p.suffix in (".tsx", ".ts") and "node_modules" not in p.parts:
            yield p


def test_no_typed_confirm_input_remains():
    """Typed-confirm entry is retired across the ENTIRE SPA, not a curated
    list. Sweep every .ts/.tsx under frontend/src for any typed-confirm
    machinery. RED on pristine 209 (19 hits across Queue, SitePayloadActions,
    Settings, MoreActions, BatchOps); GREEN once all are converted."""
    offenders = {}
    for p in _all_src_files():
        s = p.read_text(encoding="utf-8")
        hits = [pat.pattern for pat in _TYPED_PATTERNS if pat.search(s)]
        if hits:
            offenders[p.relative_to(SRC).as_posix()] = hits
    assert not offenders, f"typed-confirm machinery still present: {offenders}"


def test_v210_five_files_converted_to_tier_model():
    """The five 210 conversions land the tier seam (No-default footer or a
    single-tap branch) and drop their typed inputs. RED on pristine 209."""
    for name in ("Queue.tsx", "SitePayloadActions.tsx", "Settings.tsx",
                 "MoreActions.tsx", "BatchOps.tsx"):
        s = _read(name)
        assert not re.search(r"set\w*Typed\b", s), (
            f"{name}: a *Typed setter survived")
    # A-tier files keep the No-default control + amber token label.
    for name in ("SitePayloadActions.tsx", "MoreActions.tsx", "BatchOps.tsx"):
        s = _read(name)
        assert re.search(r"<Button\s+autoFocus\s+variant=\"default\"", s), (
            f"{name}: Tier A 'No, cancel' default control missing")
        assert "text-amber-300" in s, f"{name}: amber token label missing"


def test_v210_a_tier_literals_retained():
    """The six 210 A-tier tokens must NOT have been demoted to single-tap;
    their literals stay as amber labels. RED if any were emptied."""
    sp = _read("SitePayloadActions.tsx")
    assert '"PRUNE SELECTORS"' in sp
    assert '"DELETE URLS"' in sp
    assert '"REPLACE QUEUE"' in sp
    more = _read("MoreActions.tsx")
    assert "token: `REVOKE ${" in more
    assert "token: `DELETE TAG ${" in more
    batch = _read("BatchOps.tsx")
    assert '"DELETE HISTORY"' in batch


def test_v210_b_tier_demotions_single_tap():
    """The 210 B-tier set is single-tap: their tokens are gone (demoted), so
    they hit the plain Cancel/Confirm branch. RED on 209 (typed)."""
    # MoreActions B-tier kinds carry token: "" now.
    more = _read("MoreActions.tsx")
    assert '"BLOCK URL"' not in more
    assert '"BLOCK HASH"' not in more
    assert '"START IMPORT"' not in more
    assert '"CANCEL IMPORT"' not in more
    # Queue START ALL fully single-tap (no token const, no Typed state).
    q = _read("Queue.tsx")
    assert "START_ALL_TOKEN" not in q
    # SCHEDULE RELOGINS demoted (the live-hit) — no amber label for it.
    batch = _read("BatchOps.tsx")
    assert '"SCHEDULE RELOGINS"' not in batch
    assert '"IMPORT SITE"' not in batch


def test_destructive_tier_is_yes_no_with_no_default():
    """Tier A dialogs present an explicit choice with the cancel path
    autofocused (No is the default). Pin the autofocused cancel control in
    each route that retains a destructive tier. RED on 208 (autoFocus sat
    on the typing Input instead)."""
    for name in ("Maintenance.tsx", "Library.tsx", "History.tsx", "Vpn.tsx",
                 "Backup.tsx", "ImportViews.tsx", "SiteActions.tsx",
                 "PoolsMacros.tsx", "RebalanceCenter.tsx"):
        s = _read(name)
        # The No-default tier is satisfied EITHER by an inline autofocused
        # cancel (legacy bespoke dialog) OR by adopting the shared
        # <ConfirmDialog>, which pins "Cancel is the default focus (Tier A)"
        # in ConfirmDialog.test.tsx. Migrated pages delegate the guarantee to
        # that tested component; un-migrated pages keep the inline control.
        # (v3.66.post-365 Cut 3: RebalanceCenter migrated to <ConfirmDialog>.)
        has_inline = re.search(r"<Button\s+autoFocus", s)
        uses_confirm_dialog = "<ConfirmDialog" in s
        assert has_inline or uses_confirm_dialog, (
            f"{name}: no No-default destructive confirm "
            f"(neither an inline autoFocus cancel nor <ConfirmDialog>)")
        assert 'variant="destructive"' in s, (
            f"{name}: destructive confirm styling missing")


def test_destructive_tier_tokens_retained_as_labels():
    """Tier A actions keep their action tokens (now rendered as labels,
    not typed): file-deleting, row-deleting, and posture actions must NOT
    have been demoted to single-tap."""
    maint = _read("Maintenance.tsx")
    assert 'token: "APPLY RETENTION"' in maint
    assert 'token: "DELETE PART"' in maint
    assert "token: `PRUNE ${d}`" in maint
    assert "token: `DELETE ${id}`" in maint
    hist = _read("History.tsx")
    assert 'token: "VACUUM HISTORY"' in hist
    assert 'token: "CLEAR LOGS"' in hist
    lib = _read("Library.tsx")
    assert 'token: "ROTATE STREAM"' in lib
    assert "token: `DELETE ${it.id}`" in lib
    assert '"CLEAR KILL"' in _read("Vpn.tsx")
    assert 'token: dryRun ? "" : "RESTORE"' in _read("Backup.tsx")
    # RebalanceCenter migrated to the shared <ConfirmDialog> in Cut 3, which
    # uses target/consequence text rather than a displayed token label, so the
    # old "EXECUTE REBALANCE" label literal is intentionally retired for that
    # page. Its destructive tier is still enforced (No-default via
    # ConfirmDialog) and it still arms through the confirm flow, never one-click.
    assert '"DELETE MACRO"' in _read("PoolsMacros.tsx")


def test_single_tap_tier_demotions():
    """Tier B: the operator-approved reversible/additive set is
    single-tap (token: ""). RED on 208 (all carried typed tokens)."""
    maint = _read("Maintenance.tsx")
    assert '{ kind: "pauseAll"; token: "" }' in maint
    assert '{ kind: "resumeAll"; token: "" }' in maint
    assert 'kind: "driftReset", sid: s, token: ""' in maint
    assert 'kind: "budgetReset", sid: s, token: ""' in maint
    lib = _read("Library.tsx")
    for k in ("scanStart", "scanCancel", "tagAdd", "tagRemove",
              "tagRename", "regenNfos"):
        assert f'kind: "{k}"' in lib, f"Library kind {k!r} missing"
    assert 'token: "START SCAN"' not in lib
    assert 'token: "REGEN NFOS"' not in lib
    assert 'token: "APPLY TAG"' not in lib
    bak = _read("Backup.tsx")
    assert '{ kind: "create"; token: "" }' in bak
    assert '{ kind: "smoke"; path: string; token: "" }' in bak
    imp = _read("ImportsCenter.tsx")
    assert 'token: "IMPORT TEMPLATES"' not in imp
    assert 'token: "IMPORT BUNDLE"' not in imp


def test_nothing_one_click_invariant_survives():
    """The tier change must not loosen the arm->confirm dispatch: no write
    mutation fires straight from a page-surface onClick in the converted
    Maintenance/History/ImportsCenter routes (their dialogs dispatch via a
    named confirmRun)."""
    for name in ("Maintenance.tsx", "History.tsx", "ImportsCenter.tsx"):
        s = _read(name)
        assert "const confirmRun = () =>" in s, f"{name}: confirmRun missing"
        assert not re.search(r"onClick=\{[^}]*\.mutate", s), (
            f"{name}: a write mutation is wired one-click")


def test_api_error_message_carries_backend_reason():
    """ApiError.message must include the backend's `error` field when the
    response body carries one, so toasts explain failures (the
    /api/library/scan/start 400 finding). RED on 208 (constructor passed
    the bare status line to super())."""
    s = (SRC / "lib" / "api-client.ts").read_text(encoding="utf-8")
    assert re.search(r"class ApiError[\s\S]{0,600}detail \? `\$\{message\}: \$\{detail\}` : message", s), (
        "ApiError does not surface body.error in .message")
