"""The provisioner graded two manifests of five and said nothing about a third.

@880, and it is @879's defect one layer out. v3.66.879 made scripts/cloud-setup.sh
resolution-check `requirements.txt` AND `requirements-test.txt` instead of the
core manifest alone. Measured after it shipped, the denominator of every recovery
path is still smaller than the set of manifests:

    requirements.txt           hook, cloud-setup, deploy
    requirements-test.txt      hook, cloud-setup, deploy
    requirements-dev.txt       cloud-setup, deploy
    requirements-cloak.txt     NOTHING
    requirements-optional.txt  NOTHING

`cloakbrowser` is INSTALLED in this container (0.5.2) and no path can see it, so
a reverted image that dropped only that package is invisible to all three.

BOTH manifests are now INSTALLED and checked -- an operator decision, taken
2026-08-05 after the gap was measured. The first draft of this cut argued the
opposite for requirements-optional.txt (19 of its 21 packages were absent, so
absence looked like the expected state and gating it looked like crying wolf).
That reasoning described the container as it happened to be, not as it is meant
to be: those 19 are site extractors and a notifier stack -- phub, xvideos_api,
m3u8, scrapling, apprise -- which is capability this application exists to have.

Measured before wiring it, rather than assumed: `pip install -r
requirements-optional.txt` exits 0 and all 21 resolve with 0 specifier drift,
and the app still imports afterwards. A step that cannot install is worse than
no step.

STATED, not GATED, for both. cloakbrowser is skippable via BD_SKIP_CLOAK
(cloud-setup.sh:381) and DEFERRED when there is no venv (:387); the optional set
reaches 21 third-party indexes, any of which can yank a release. A FAILED row
would brick provisioning over a single unavailable extractor, which is the
over-sensitive failure CLAUDE.md section 0 counts as equal to a false clean. The
report already tells its reader that "a WARN row is a capability", and an absent
capability that is NAMED is a different object from one nobody measured.
"""
from __future__ import annotations

import re
from pathlib import Path

# @880: the shared reader. Hand-rolled copies of these two helpers were wrong
# three times across two cuts -- see tests/shell_source.py for both shapes.
from shell_source import blocks_containing, shell_code_only

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "scripts" / "cloud-setup.sh"

# The manifest a recovery path must not gate on. Kept as data with the reason
# attached, so a future reader does not "fix" the omission.
# Named _CAPABILITY sets: installed and REPORTED, never gated.
_UNGATED = "requirements-optional.txt"
_STATED = "requirements-cloak.txt"


def test_the_cloak_manifest_is_resolution_checked():
    """THE DEFECT. Nothing in any recovery path names requirements-cloak.txt, so
    the one capability package that IS installed cannot be seen to go missing."""
    code = shell_code_only(SETUP)
    assert _STATED in code, (
        "%s is named nowhere in cloud-setup.sh's executable text, so no row "
        "reports whether the capability it declares is present" % _STATED)
    # It must reach the resolution check, not merely appear in an install line:
    # installing is not verifying, which is the whole premise of this tool.
    graded = "\n".join(blocks_containing(code, "check_requirements.py"))
    assert _STATED in graded, (
        "%s is not inside any construct that calls check_requirements.py -- it "
        "is installed and not verified" % _STATED)


def test_the_cloak_row_is_stated_not_gated():
    """The over-sensitive direction, and the reason this cut is small.

    cloakbrowser is optional by design: BD_SKIP_CLOAK skips it and a repo-less
    setup DEFERS it. A row that set CORE_FAILED would turn a correct, deliberate
    skip into a failed provision -- a gate that cries wolf, which section 0
    counts as a soundness bug equal to a false clean.
    """
    code = shell_code_only(SETUP)
    for line in code.splitlines():
        if _STATED in line and "CORE_FAILED" in line:
            raise AssertionError(
                "the cloak check sets CORE_FAILED on %r -- an optional "
                "capability must be RECORDED, not gated" % line.strip())


def test_the_optional_manifest_is_installed_and_checked():
    """Operator decision: the extractor and notifier stack is capability this
    application exists to have, so it is installed rather than left absent."""
    code = shell_code_only(SETUP)
    assert _UNGATED in code, (
        "%s is named nowhere in cloud-setup.sh's executable text, so its 21 "
        "packages are neither installed nor reported" % _UNGATED)
    installed = "\n".join(blocks_containing(code, "pip install"))
    assert _UNGATED in installed, (
        "%s is not inside any construct that pip-installs -- its 21 packages "
        "are declared and never fetched" % _UNGATED)
    graded = "\n".join(blocks_containing(code, "check_requirements.py"))
    assert _UNGATED in graded, (
        "%s is installed but never resolution-checked -- installing is not "
        "verifying, which is this tool's entire premise" % _UNGATED)


def test_neither_capability_manifest_can_brick_a_provision():
    """The over-sensitive direction, and the reason both stay WARN.

    The optional set reaches 21 third-party indexes and any of them can yank a
    release; cloakbrowser is skippable by design. A CORE_FAILED on either turns
    one unavailable extractor into a failed provision, and a provisioner that
    fails for reasons unrelated to the code is one an operator learns to ignore.
    """
    code = shell_code_only(SETUP)
    # BLOCK-scoped, not line-scoped. The line-scoped version of this assertion
    # ESCAPED its mutant: the loop body grades "$CAP_FILE", so a CORE_FAILED
    # added there contains no manifest literal and no per-line check can see it.
    # That is the third time in two cuts that a line-scoped assertion about a
    # loop body was wrong; the block extractor is the fix.
    for manifest in (_STATED, _UNGATED):
        for block in blocks_containing(code, manifest):
            assert "CORE_FAILED" not in block, (
                "the construct grading %s sets CORE_FAILED -- an optional "
                "capability must be RECORDED, not gated, or one yanked "
                "release fails the whole provision:\n%s" % (manifest, block))


def test_the_contract_bands_doc_edits_to_the_freshness_gates():
    """@880's second half, and it cost a CI round trip on v3.66.879.

    The band derived for that cut was correct for the CODE it changed and CI
    still went red: `bd-freshcheck` -- reached through
    tests/test_toolchain_534.py -- is in the blast radius of a SESSION_CARRY
    edit, and no module-derived band reaches a gate whose subject is a DOCUMENT.
    CLAUDE.md section 4 documented this for test files (the axis-6 gates) but
    said nothing about doc edits, which have their own gate family.
    """
    contract = (REPO / "CLAUDE.md").read_text()
    section = contract[contract.find("## 4 |"):contract.find("## 5 |")]
    assert section, "CLAUDE.md section 4 could not be located"
    low = section.lower()
    assert "session_carry" in low or "register" in low, (
        "section 4 does not say that editing a register bands anything")
    # The RUNNABLE block, not the prose. Asserting only that the section
    # mentions the tool ESCAPED its mutant: the command was swapped for a
    # different tool while the surrounding paragraph still said the name, so
    # the reader who copies the block runs the wrong check. This is the
    # prose-vs-code conflation again, in a document rather than a script.
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    assert blocks, "section 4 gives no runnable block for a doc/register edit"
    assert any("bd-freshcheck" in b for b in blocks), (
        "no runnable block in section 4 invokes bd-freshcheck, so the stated "
        "band for a doc or register edit cannot be copied and run. blocks=%r"
        % blocks)
