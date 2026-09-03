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

2026-09-03, OPERATOR DECISION: requirements-optional.txt is MERGED into
requirements.txt and retired. Measured on the fleet that morning, 6 of 12 hosts
ran with 5 to 25 of its pins absent -- install_linux.sh installs them once,
best-effort, and scripts/deploy.sh step [5] converges only the manifests it
names, so a skipped pin was never repaired and every consumer's guarded import
degraded without a word (dedup's videohash among them). "Stated, not gated" was
the right posture for a manifest nothing converged; with one manifest the 25
are converged on every deploy and a missing one is a red deploy rather than a
silent host. requirements-cloak.txt keeps the stated posture; the last test in
this file is the denominator the optional manifest never had.
"""
from __future__ import annotations

import re
from pathlib import Path

# @880: the shared reader. Hand-rolled copies of these two helpers were wrong
# three times across two cuts -- see tests/shell_source.py for both shapes.
from shell_source import blocks_containing, shell_code_only

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "scripts" / "cloud-setup.sh"

# The one capability manifest a recovery path must not gate on. Kept as data
# with the reason attached, so a future reader does not "fix" the omission.
# (requirements-optional.txt was the second such manifest until 2026-09-03,
# when it was merged into requirements.txt -- see the module docstring.)
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
    for manifest in (_STATED,):
        for block in blocks_containing(code, manifest):
            assert "CORE_FAILED" not in block, (
                "the construct grading %s sets CORE_FAILED -- an optional "
                "capability must be RECORDED, not gated, or one yanked "
                "release fails the whole provision:\n%s" % (manifest, block))


# ---------------------------------------------------------------------------
# 2026-09-03: THE GAP THE OPTIONAL MANIFEST OPENED. scripts/deploy.sh step [5]
# converges the manifests it names with `converge_reqs`, and nothing else on the
# deploy path runs pip. A package the application imports but declares only in a
# manifest deploy never names is therefore installed by install_linux.sh once
# (best-effort, per line, non-fatal) and never repaired: measured 2026-09-03 on
# the fleet, 6 of 12 hosts lacked 5 to 25 of them (videohash under dedup,
# playwright-stealth, psycopg2, every site extractor on one host) and no gate
# said a word, because every consumer guards its import. This test is the
# denominator that was missing: every third-party name the app imports that is
# declared SOMEWHERE must be declared in a manifest deploy CONVERGES.
# ---------------------------------------------------------------------------
_DEPLOY = REPO / "scripts" / "deploy.sh"
_APP = REPO / "bulk_downloader"
_EXTRACTORS = _APP / "extractors.py"
# import root -> distribution name, only where the two differ AND the name is a
# declared dependency (undeclared roots fall out of the intersection anyway).
_ROOT_TO_DIST = {
    "psycopg2": "psycopg2-binary",
    "playwright_stealth": "playwright-stealth",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "dateutil": "python-dateutil",
}


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _manifest_names(path: Path) -> set:
    """Distribution names declared in one manifest, under the SAME skipping
    rules as tools/check_requirements.py (the instrument deploy step [5] runs):
    `-r` include lines are skipped, not followed. Converging
    requirements-test.txt therefore converges nothing that only
    requirements.txt declares, and this reader must not pretend otherwise."""
    from packaging.requirements import Requirement
    if not path.is_file():
        return set()
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(_canon(Requirement(line).name))
    return names


def _converged_manifests_in(deploy: Path) -> list:
    """The manifests a deploy.sh step [5] actually converges, read from the
    calls themselves rather than from the comment above them."""
    if not deploy.is_file():
        return []
    code = shell_code_only(deploy)
    return re.findall(r"^\s*converge_reqs\s+(\S+)\s*$", code, flags=re.M)


def _static_import_roots(app: Path) -> set:
    """Third-party import roots of every <app>/**/*.py by AST (static,
    subpackages included)."""
    import ast
    roots = set()
    for py in sorted(app.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return {_canon(_ROOT_TO_DIST.get(r, r)) for r in roots}


def _importlib_roots(extractors: Path) -> set:
    """The extractor library names extractors.py loads through importlib.
    Those are AST-invisible on purpose, and they are exactly the population
    that was 25-absent on one fleet host. Harvested ONLY from Call arguments
    and Dict values (the _REGISTRY table and the _try_import calls), never
    from docstrings or free-standing text, so prose cannot enter the census."""
    import ast
    if not extractors.is_file():
        return set()
    tree = ast.parse(extractors.read_text(encoding="utf-8"), filename=str(extractors))
    names = set()

    def _strings(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if re.fullmatch(r"[a-z][a-z0-9_]*", sub.value):
                    names.add(sub.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant):
                    _strings(arg)
        elif isinstance(node, ast.Dict):
            for value in node.values:
                if value is not None:
                    _strings(value)
    return {_canon(_ROOT_TO_DIST.get(r, r)) for r in names}


def _census(root: Path) -> dict:
    """Everything the verdict needs, from one tree root, so the same reader
    serves the real repository and a synthetic negative control."""
    converged = _converged_manifests_in(root / "scripts" / "deploy.sh")
    converged_names = set()
    for m in converged:
        converged_names |= _manifest_names(root / m)
    declared_anywhere = set()
    for m in sorted(root.glob("requirements*.txt")):
        declared_anywhere |= _manifest_names(m)
    static = _static_import_roots(root / "bulk_downloader")
    dynamic = _importlib_roots(root / "bulk_downloader" / "extractors.py")
    required = (static | dynamic) & declared_anywhere
    return {
        "converged": converged,
        "converged_names": converged_names,
        "declared_anywhere": declared_anywhere,
        "static": static,
        "dynamic": dynamic,
        "required": required,
        "stranded": sorted(required - converged_names),
    }


# cloakbrowser is imported by the app and declared only in requirements-cloak.txt,
# which deploy deliberately does not converge (skippable, own wheel pack): STATED.
# Named here so the waiver is data, and asserted present so it cannot rot.
_STATED_NOT_CONVERGED = {"cloakbrowser"}


def test_every_imported_dependency_is_declared_where_deploy_converges_it():
    """RED on 324ee21b: 24 imported packages live only in requirements-optional.txt,
    a manifest no converge_reqs call names. GREEN once one manifest holds them."""
    c = _census(REPO)
    converged, converged_names, required = c["converged"], c["converged_names"], c["required"]
    assert len(converged) >= 2, (
        "deploy.sh names fewer than two converge_reqs manifests -- the reader "
        "sees no subject: %r" % (converged,))
    assert len(converged_names) >= 15, (
        "converged manifests declare %d names -- denominator too small to be "
        "the real thing" % len(converged_names))
    assert len(required) >= 20, (
        "only %d imported names are declared anywhere -- the census or the "
        "alias map is broken, not the manifests" % len(required))
    # Positive controls, ONE PER CENSUS PATH. flask proves the AST path;
    # eaf-base-api (the shared base every site extractor pulls) proves the
    # importlib path -- without it the extractor population could drop out of
    # `required` silently and the >= 20 floor would still hold on the AST
    # names alone (shape-lens replay S3, 2026-09-03).
    assert "flask" in c["static"] and "flask" in converged_names
    assert "eaf-base-api" in c["dynamic"] and "eaf-base-api" in converged_names, (
        "the importlib harvest no longer sees the extractor libraries -- the "
        "population this gate exists for is invisible: dynamic=%s"
        % sorted(c["dynamic"]))
    assert c["dynamic"] - c["static"], (
        "the importlib harvest contributes nothing beyond the AST census")
    # The waiver both ways: still imported+declared (else it is dead data),
    # and still NOT converged (else deploy repaired it and the entry must go).
    assert _STATED_NOT_CONVERGED <= required, (
        "the stated waiver names a package the app no longer imports: %s"
        % sorted(_STATED_NOT_CONVERGED - required))
    assert not (_STATED_NOT_CONVERGED & converged_names), (
        "waiver is stale -- deploy now converges %s; retire the entry"
        % sorted(_STATED_NOT_CONVERGED & converged_names))
    stranded = sorted(set(c["stranded"]) - _STATED_NOT_CONVERGED)
    assert not stranded, (
        "%d package(s) the application imports are declared only in a manifest "
        "scripts/deploy.sh never converges, so a host that lost them stays "
        "silently degraded: %s" % (len(stranded), ", ".join(stranded)))


def test_the_contract_bands_doc_edits_to_the_freshness_gates():
    """@880's second half, and it cost a CI round trip on v3.66.879.

    The band derived for that cut was correct for the CODE it changed and CI
    still went red: `bd-freshcheck` -- reached through
    tests/test_toolchain_534.py -- is in the blast radius of a SESSION_CARRY
    edit, and no module-derived band reaches a gate whose subject is a DOCUMENT.
    CLAUDE.md now owns this in its concise A5 verification section.
    """
    contract = (REPO / "CLAUDE.md").read_text()
    section = contract[contract.find("## A5 |"):contract.find("## A6 |")]
    assert section, "CLAUDE.md section A5 could not be located"
    low = section.lower()
    assert "documentation" in low or "backlog" in low, (
        "section A5 does not say that editing a current document bands freshness")
    # The RUNNABLE block, not the prose. Asserting only that the section
    # mentions the tool ESCAPED its mutant: the command was swapped for a
    # different tool while the surrounding paragraph still said the name, so
    # the reader who copies the block runs the wrong check. This is the
    # prose-vs-code conflation again, in a document rather than a script.
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    assert blocks, "section A5 gives no runnable block for a doc/backlog edit"
    assert any("bd-freshcheck" in b for b in blocks), (
        "no runnable block in section A5 invokes bd-freshcheck, so the stated "
        "band for a doc or register edit cannot be copied and run. blocks=%r"
        % blocks)


def test_the_reader_names_a_package_stranded_in_an_unconverged_manifest(tmp_path):
    """NEGATIVE CONTROL for the verdict branch itself. After the merge no
    deploy.sh-only mutant can strand a package and still pass the >= 15 floor
    (requirements.txt carries the names), so the mutation battery's OR-M1 is
    caught by that floor. This synthetic tree reaches the stranded branch
    directly and asserts its DISTINCTIVE diagnostic, so the refusal cannot be
    laundered by an earlier precondition (CLAUDE.md A5). The load-bearing
    assertions are the `c["stranded"] == [...]` comparisons: they read the SAME
    reader the verdict reads, on a tree whose shape is asserted first."""
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "scripts" / "deploy.sh").write_text(
        "#!/bin/bash\nconverge_reqs requirements.txt\n"
        "converge_reqs requirements-test.txt\n", encoding="utf-8")
    (root / "requirements.txt").write_text("flask>=3.0,<4.0\n", encoding="utf-8")
    (root / "requirements-test.txt").write_text("-r requirements.txt\npytest\n", encoding="utf-8")
    # The defect's shape: a manifest deploy never names, declaring an import.
    (root / "requirements-extras.txt").write_text(
        "zzz_strandpkg>=1.0\nphub>=4.0\n", encoding="utf-8")
    app = root / "bulk_downloader"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "web.py").write_text("import flask\nimport zzz_strandpkg\n", encoding="utf-8")
    # The docstring is ONE TOKEN on purpose: the harvest regex matches whole
    # identifiers only, so a sentence docstring could never be harvested by ANY
    # reader and would make this control vacuous (shape lens, 2026-09-03).
    (app / "extractors.py").write_text(
        '"""phub"""\n'
        "import importlib\n"
        "_REGISTRY = {'pornhub': ('phub', 'PornHub')}\n"
        "def _try_import(name):\n    return importlib.import_module(name)\n",
        encoding="utf-8")
    import ast as _ast
    tree = _ast.parse((app / "extractors.py").read_text(encoding="utf-8"))
    assert any(isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
               and n.value.value == "phub" for n in tree.body), (
        "precondition: the synthetic extractors.py must carry a one-token "
        "docstring 'phub' that a docstring-harvesting reader WOULD count")
    c = _census(root)
    # Preconditions: the synthetic tree has the shape the test claims.
    assert c["converged"] == ["requirements.txt", "requirements-test.txt"]
    assert "-r" not in " ".join(c["converged_names"]) and "flask" in c["converged_names"]
    assert "zzz-strandpkg" in c["static"], c["static"]
    assert "phub" in c["dynamic"], c["dynamic"]
    assert c["stranded"] == ["phub", "zzz-strandpkg"], c["stranded"]
    # The docstring word is harvested ONLY through the Dict value, never as prose:
    (app / "extractors.py").write_text(
        '"""phub"""\nimport importlib\n', encoding="utf-8")
    tree = _ast.parse((app / "extractors.py").read_text(encoding="utf-8"))
    assert any(isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
               and n.value.value == "phub" for n in tree.body)
    c2 = _census(root)
    assert "phub" not in c2["dynamic"], "a docstring word entered the importlib census"
    assert c2["stranded"] == ["zzz-strandpkg"]
