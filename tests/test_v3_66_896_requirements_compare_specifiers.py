"""check_requirements.py must answer "is this requirement SATISFIED", not
"does this NAME resolve".

THE GAP. `unresolved()` called `version(name)` and threw the result away, so the
specifier was never compared. Measured on this tree: a manifest containing
`flask==0.0.1` against an installed flask 3.1.3 exited **0 with silent stdout** --
"every entry resolves", over a version that satisfies nothing. All 19 declared
requirements in requirements.txt and requirements-test.txt carry a specifier, so
the blind spot covered 100% of them.

WHY THAT MATTERS MORE THAN IT LOOKS. This tool is the SOLE instrument in all
three recovery paths -- scripts/deploy.sh:321 and :334, and
scripts/cloud-setup.sh:614 and :661. CLAUDE.md section 5 records the consequence:
a reverted container image can restore correct NAMES at wrong VERSIONS and every
gate reports OK. It exists because `pip check` cannot see an uninstalled
requirement; it had the mirror-image blind spot for an unsatisfied one.

THE CALLER CONTRACT IS UNCHANGED, deliberately, and that is why the fix needs no
edit to either script. Exit 1 with names on stdout already means "these need
installing", and `pip install -r` is exactly the right remedy for a version that
fails its specifier -- both callers then RE-ASK with the same instrument, which
is what turns the fix into a converging loop rather than a louder complaint.

PACKAGING IS REQUIRED, AND ITS ABSENCE IS UNEVALUABLE RATHER THAN CLEAN. Version
comparison is PEP 440, not string equality, so it is not hand-rolled here. If
`packaging` cannot be imported the tool exits 2 -- and both callers already treat
2 as "treat as NOT satisfied", so the fail-closed direction needs no new wiring.
It is present wherever the suite runs (pytest requires it) but was only ever
transitive, so this cut declares it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "check_requirements.py"
PYTHON = sys.executable

EXIT_SATISFIED = 0
EXIT_UNSATISFIED = 1
EXIT_UNEVALUABLE = 2


def _installed(name: str) -> str:
    from importlib.metadata import version
    return version(name)


def _check(manifest: Path, env=None) -> subprocess.CompletedProcess[str]:
    assert TOOL.is_file(), f"{TOOL} does not exist -- an exit code would prove nothing"
    return subprocess.run([PYTHON, str(TOOL), str(manifest)],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=120, env=env)


def _manifest(tmp_path: Path, body: str, name="requirements.txt") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# THE DEFECT: a version that cannot satisfy its own specifier.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("spec", ["flask==0.0.1", "flask<1.0", "flask>=999.0"])
def test_an_unsatisfiable_specifier_is_not_satisfied(tmp_path, spec) -> None:
    done = _check(_manifest(tmp_path, spec + "\n"))

    assert done.returncode != EXIT_SATISFIED, (
        f"{spec!r} graded satisfied against installed flask "
        f"{_installed('flask')} -- the specifier was never compared. "
        f"stdout={done.stdout!r} stderr={done.stderr!r}"
    )
    assert done.returncode == EXIT_UNSATISFIED, (
        f"an installed-but-wrong-version package is UNSATISFIED (exit 1, so the "
        f"caller reinstalls), not unevaluable: exit {done.returncode}"
    )
    assert "flask" in done.stdout, (
        "the caller reads stdout to know what to install and to name in its "
        f"failure message; it was {done.stdout!r}"
    )


def test_a_satisfiable_specifier_still_passes(tmp_path) -> None:
    """The over-sensitive direction. A gate that fails a correct manifest is
    switched off, and this one gates every deploy and every provisioning run."""
    done = _check(_manifest(tmp_path, f"flask=={_installed('flask')}\n"))

    assert done.returncode == EXIT_SATISFIED, (
        f"a manifest pinned to the version actually installed was rejected: "
        f"stdout={done.stdout!r} stderr={done.stderr!r}"
    )
    assert done.stdout.strip() == "", (
        f"a clean run must name no packages: {done.stdout!r}"
    )


def test_the_real_manifests_are_evaluable(tmp_path) -> None:
    """Every real requirement line parses and gets compared.

    DELIBERATELY NOT "the manifests are satisfied here". That asserts the
    ENVIRONMENT is correct, which is a different question from whether the TOOL
    is correct, and this container is a live counter-example: it carries
    cryptography 49.0.0 against a declared >=42.0,<46.0. Binding the tool's test
    to that would make it fail for an environmental reason -- the exact class
    CLAUDE.md section 5 says not to chase as a code defect -- and would tempt a
    future reader to "fix" it by loosening a real constraint.

    Exit 2 is the failure that WOULD indict the comparator: it means a line
    could not be parsed or packaging could not be imported.
    """
    for name in ("requirements.txt", "requirements-test.txt"):
        done = _check(REPO_ROOT / name)
        assert done.returncode != EXIT_UNEVALUABLE, (
            f"{name} could not be evaluated -- a real manifest line does not "
            f"parse, or packaging is missing: {done.stderr!r}"
        )


def test_a_manifest_of_every_installed_version_is_satisfied() -> None:
    """The over-sensitivity check, built so the environment cannot skew it.

    Pins each real requirement to the version actually installed. Any strictness
    bug -- a mishandled epoch, a prerelease, a multi-clause range -- shows up
    here as a package reported unsatisfied against its own exact version, in the
    sandbox rather than on the box mid-deploy.
    """
    import re
    import tempfile
    from importlib.metadata import PackageNotFoundError, version

    lines = []
    for name in ("requirements.txt", "requirements-test.txt"):
        for raw in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
            line = raw.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            stem = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
            try:
                lines.append(f"{stem}=={version(stem)}")
            except PackageNotFoundError:
                continue

    assert len(lines) >= 15, (
        f"only {len(lines)} requirement(s) resolved -- too few to be a real "
        "over-sensitivity check; the denominator would not contain the subject"
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "requirements.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        done = _check(path)

    assert done.returncode == EXIT_SATISFIED, (
        f"{len(lines)} packages pinned to their OWN installed versions were "
        f"reported unsatisfied -- that is a comparator bug, not env drift: "
        f"{done.stdout!r} {done.stderr!r}"
    )


def test_an_absent_package_is_still_unsatisfied(tmp_path) -> None:
    """The behaviour that already worked must survive the widening."""
    done = _check(_manifest(tmp_path, "definitely-not-a-real-package-xyz\n"))

    assert done.returncode == EXIT_UNSATISFIED, done.stderr
    assert "definitely-not-a-real-package-xyz" in done.stdout


def test_a_bare_name_with_no_specifier_is_satisfied_when_installed(
    tmp_path,
) -> None:
    """No specifier means no constraint -- presence is the whole question."""
    done = _check(_manifest(tmp_path, "flask\n"))
    assert done.returncode == EXIT_SATISFIED, (
        f"a bare name was rejected: {done.stdout!r} {done.stderr!r}")


def test_mixed_manifest_names_only_the_unsatisfied(tmp_path) -> None:
    good = f"flask=={_installed('flask')}"
    done = _check(_manifest(tmp_path, f"{good}\nhttpx>=999.0\n"))

    assert done.returncode == EXIT_UNSATISFIED
    named = done.stdout.split()
    assert "httpx" in named, f"the unsatisfied entry was not named: {done.stdout!r}"
    assert "flask" not in named, (
        f"a satisfied entry was named for reinstall: {done.stdout!r}")


# --------------------------------------------------------------------------
# Unevaluable stays unevaluable, and gains one case.
# --------------------------------------------------------------------------
def test_zero_requirement_names_is_still_unevaluable(tmp_path) -> None:
    done = _check(_manifest(tmp_path, "# only a comment\n\n-r other.txt\n"))
    assert done.returncode == EXIT_UNEVALUABLE, (
        f"an empty denominator must not read as satisfied: {done.returncode}")


def test_without_packaging_it_refuses_rather_than_guessing(tmp_path) -> None:
    """PEP 440 comparison is not hand-rolled, so its absence is UNEVALUABLE.

    Both callers already treat exit 2 as "treat as NOT satisfied", so the
    fail-closed direction needs no new wiring. Simulated by pointing the child
    at a sitecustomize that makes `import packaging` raise -- the module is
    genuinely present here, so blocking the import is the only way to reach the
    branch, and asserting on the branch rather than on a mocked function keeps
    the subject inside the denominator.
    """
    blocker = tmp_path / "sitecustomize.py"
    blocker.write_text(
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] == 'packaging':\n"
        "            raise ImportError('blocked for test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n",
        encoding="utf-8")
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path)

    done = _check(_manifest(tmp_path, "flask>=3.0\n"), env=env)

    assert done.returncode == EXIT_UNEVALUABLE, (
        f"without packaging the tool must refuse, not fall back to a name-only "
        f"answer it cannot label: exit {done.returncode} "
        f"stdout={done.stdout!r} stderr={done.stderr!r}"
    )
    assert done.stdout.strip() == "", (
        f"stdout is the caller's package list; it must stay empty on an "
        f"unevaluable run: {done.stdout!r}"
    )
    assert "packaging" in done.stderr, (
        f"the refusal must name what is missing: {done.stderr!r}")


def test_packaging_is_declared_not_merely_transitive() -> None:
    """It is imported directly, so it is a direct dependency.

    It happens to arrive today via pytest, which is in a DIFFERENT manifest than
    the one deploy.sh checks first -- so relying on that ordering would make the
    first check on a fresh venv exit 2.
    """
    body = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    names = [ln.split("#")[0].strip() for ln in body.splitlines()]
    names = [n for n in names if n and not n.startswith("-")]
    import re
    stems = {re.split(r"[<>=!~\[; ]", n, maxsplit=1)[0].strip().lower()
             for n in names}
    assert "packaging" in stems, (
        f"check_requirements.py imports packaging directly but requirements.txt "
        f"does not declare it: {sorted(stems)}"
    )


def test_a_prerelease_satisfies_a_plain_lower_bound(monkeypatch) -> None:
    """Closing a mutation escape: `prereleases=True` was unconstrained.

    packaging excludes prereleases by DEFAULT, so `2.0rc1` does not satisfy
    `>=1.9` unless asked. A venv legitimately holding a prerelease would then be
    reported unsatisfied, and both callers respond by running `pip install -r`
    and RE-ASKING -- so the loop cannot converge and the deploy dies on a box
    that is actually fine. That is the over-sensitive direction, and for a gate
    wired into every deploy it is the worse one.

    Driven through the module rather than the CLI because no prerelease is
    installed here, and asserting against a real installed version could not
    reach the branch at all.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_requirements_probe", REPO_ROOT / "tools" / "check_requirements.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "version", lambda name: "2.0rc1")

    assert mod.unsatisfied(["somepkg>=1.9"]) == [], (
        "a prerelease was reported unsatisfied against a plain lower bound; "
        "the caller would reinstall and re-ask forever"
    )
    assert mod.unsatisfied(["somepkg>=3.0"]) == ["somepkg"], (
        "prerelease handling must not become 'everything satisfies everything'"
    )
