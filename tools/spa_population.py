"""ONE definition of which ``frontend/src`` file may answer a scanner's question.

A SCANNER'S POPULATION IS ITS DENOMINATOR, IN BOTH DIRECTIONS, and this module
exists because the repository proved that twice in two directions inside one
week:

* v3.66.1217 -- ``tools/gui_parity_inventory.py::_spa_wiring`` walked
  ``frontend/src`` with ``rglob("*.ts*")`` and treated any /api/* literal it
  found as evidence that the SPA WIRES that endpoint. Vitest specs are in that
  glob, so a FIXTURE could vouch for a route no product code called: 457 wired
  endpoints with specs in the population, 443 without.
* v3.66.1218 -- ``tests/test_t5_t6_wired.py`` used the SAME glob to forbid a raw
  state-changing ``fetch()``, because one ships to a browser without
  X-CSRF-Token. A spec never ships; ``lib/api-client.csrf.test.ts`` contains a
  deliberate tokenless-fetch NEGATIVE CONTROL, and that control manufactured a
  CI failure on a correct cut. Reshaping the control would have been evading
  the gate; the POPULATION was wrong.

Same glob, same over-inclusion, opposite consequence. Row 232 filed the
remainder as a CLASSIFICATION task rather than a blanket patch, because the
right population depends on the question:

``PRODUCT``
    Files the SPA SHIPS. The population for any question about deployed
    behaviour -- "does the app call this endpoint", "does a control for this
    config key exist", "is this route reachable by a click", "would this
    request 403 on a real cookie session".

``SPEC``
    ``*.test.ts(x)`` / ``*.spec.ts(x)``. A description of the app, not the app.
    Never admissible as evidence ABOUT the shipped app -- but still inside the
    population of a question about the SOURCE TREE, e.g. ``test_t5_t6_wired.py``
    forbids any reference to the dead ``/api/auth_surface`` route repo-wide,
    because a spec naming a nonexistent route is also a defect.

``HARNESS``
    ``frontend/src/test/`` -- ``wiredGateHarness.tsx`` today. It carries no
    ``.test.``/``.spec.`` suffix, so ``SPEC_FILE_RE`` cannot see it, and it is
    neither shipped product nor a spec. Row 232 named it as the third class
    the shared rule did not cover. MEASURED at v3.66.1223: the one harness file
    contains zero ``/api/`` literals and zero ``<Link>``/``to=``/``href=``
    occurrences, so classifying it changes NO verdict today. That is a latent
    class made visible before it fires, stated as such rather than dressed up
    as a live bug.

The module is deliberately IMPORT-SIDE-EFFECT-FREE. ``gui_parity_inventory``
mutates ``sys.path`` at import time, which is why ``test_t5_t6_wired.py``
re-declared the regex instead of importing it; nothing here does that, so a
gate sharing a pytest process with two dozen others can import it directly.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "PRODUCT",
    "SPEC",
    "HARNESS",
    "SPEC_FILE_RE",
    "HARNESS_DIRS",
    "DEFAULT_SUFFIXES",
    "classify",
    "select",
    "product_files",
    "product_text",
    "require_nonzero",
    "require_both_halves",
]

PRODUCT = "product"
SPEC = "spec"
HARNESS = "harness"

#: Kept byte-identical to ``tools/gui_parity_inventory.py::_TEST_FILE_RE``.
#: ``tests/test_v3_66_1223_*`` fails if the two ever drift, because two gates
#: disagreeing about what the SPA IS is worse than either being wrong alone.
SPEC_FILE_RE = re.compile(r"\.(test|spec)\.tsx?$")

#: Top-level directories under ``frontend/src`` holding Vitest harness code.
HARNESS_DIRS = ("test",)

#: EXPLICIT suffixes, not ``*.ts*``. The star form also admits ``*.tsbuildinfo``
#: and any future ``.ts``-prefixed artefact; measured equal on the real tree
#: today (352 files either way), so this is a tightening with no delta.
DEFAULT_SUFFIXES = ("*.ts", "*.tsx")


def classify(rel_path) -> str:
    """PRODUCT / SPEC / HARNESS for one path RELATIVE to ``frontend/src``.

    Order matters: the SPEC suffix wins over the HARNESS directory, so a
    ``test/foo.test.tsx`` is a spec rather than harness.
    """
    rel = Path(rel_path).as_posix()
    name = rel.rsplit("/", 1)[-1]
    if SPEC_FILE_RE.search(name):
        return SPEC
    parts = rel.split("/")
    if len(parts) > 1 and parts[0] in HARNESS_DIRS:
        return HARNESS
    return PRODUCT


def select(src_root, include=(PRODUCT,), suffixes=DEFAULT_SUFFIXES):
    """``(selected, excluded)`` relative POSIX paths, both sorted.

    BOTH halves are returned so a caller can assert each is nonzero instead of
    trusting the rule. A rule that quietly matched everything would empty the
    scan and pass forever; a rule that matched nothing would restore the defect
    while every assertion beside it still went green.

    An absent tree yields two empty lists rather than raising -- the
    behaviour-preserving contract ``_spa_wiring`` has always had. Callers that
    need a verdict call :func:`require_nonzero` on the result; that is the
    fail-closed half and it is deliberately a separate decision.
    """
    src_root = Path(src_root)
    include = tuple(include)
    selected: list[str] = []
    excluded: list[str] = []
    if not src_root.is_dir():
        return selected, excluded
    seen: set[str] = set()
    for suffix in suffixes:
        for path in src_root.rglob(suffix):
            if not path.is_file():
                continue
            rel = path.relative_to(src_root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            if classify(rel) in include:
                selected.append(rel)
            else:
                excluded.append(rel)
    return sorted(selected), sorted(excluded)


def product_files(src_root, suffixes=DEFAULT_SUFFIXES):
    """Absolute paths of the SHIPPED SPA source, sorted."""
    src_root = Path(src_root)
    selected, _ = select(src_root, (PRODUCT,), suffixes)
    return [src_root / rel for rel in selected]


def product_text(src_root, suffixes=DEFAULT_SUFFIXES):
    """One concatenation of the shipped SPA source.

    Joined with a newline: the hand-rolled ``blob += p.read_text()`` loops this
    replaces could match a token that spanned a file boundary, which is a
    false-positive route in exactly the "a path NAMED is not a path CALLED"
    family the population rule closes.
    """
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in product_files(src_root, suffixes)
    )


def require_nonzero(selected, where):
    """FAIL-CLOSED on an empty population; returns its size.

    CLAUDE.md A7: a scanner over an empty population passes vacuously, and an
    unmeasurable claim is UNKNOWN, never OK.
    """
    assert selected, (
        "%s: the selected SPA population is EMPTY. A scan over nothing "
        "reports no offenders and proves nothing -- this is UNKNOWN, not a "
        "pass. Check that frontend/src is present and that the population "
        "rule has not widened to exclude every file." % (where,)
    )
    return len(selected)


def require_both_halves(selected, excluded, where):
    """FAIL-CLOSED on either half being empty; returns ``(len, len)``.

    The narrowing is itself a hole unless guarded. An exclusion that never
    fires on the real tree is untested by it and proves nothing about the
    laundering route it was added to close.
    """
    require_nonzero(selected, where)
    assert excluded, (
        "%s: nothing was EXCLUDED from the SPA population, so the exclusion "
        "rule is untested by the real tree and this gate proves nothing about "
        "it. Either the rule stopped matching or the spec files left the "
        "tree." % (where,)
    )
    return len(selected), len(excluded)
