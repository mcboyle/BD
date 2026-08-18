"""Repository invariants for the fleet authority-document mirrors."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_EXPECTED_SHA256 = {
    "project-knowledge/SUPERSEDED.md":
        "02ba441f40fe0eec8460b68c118c02e2bf4b2dda96f756083c3cc403c3ae1ca5",
    "project-knowledge/ERRATA_2026-08-18.md":
        "cdff9825acf4cbc92dd618bee339cd5f9b535cbe959798dbc3f7438a220c49b8",
    "project-knowledge/AUTONOMY_POLICY.md":
        "9b6125c91484ba5ad5d6dc8a2d7ad1f0cf3879d0af63bc859c4ffd53c338d11f",
    "project-knowledge/build_current_overlay.py":
        "1e276e5634d1a0b247e3f76f432af62ff75ac548c96696c20d21681ed184a6f9",
}


def test_reviewed_authority_document_mirrors_are_byte_pinned() -> None:
    # The fleet-autonomy bundle is reviewed and byte-pinned. These repository
    # copies are convenience mirrors and must never diverge silently.
    observed = {
        relative: hashlib.sha256((_REPO / relative).read_bytes()).hexdigest()
        for relative in _EXPECTED_SHA256
    }

    assert observed == _EXPECTED_SHA256


def test_current_state_overlay_is_never_tracked() -> None:
    overlay = "project-knowledge/CURRENT_STATE.json"
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", overlay],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, (
        f"{overlay} is tracked; committing an overlay changes HEAD and makes "
        "its recorded repository identity stale"
    )
