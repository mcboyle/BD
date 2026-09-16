"""Row 648: the frontend routes on react-router 7, and react-router-dom is gone.

WHY THIS IS A GATE AND NOT A DEPENDABOT BUMP. Two medium advisories against
react-router (Link/useNavigate backslash redirect; deserializeErrors()
injection) are fixed in 7.18.0. Their affected ranges start at 6.0.0 and
6.4.0 respectively; neither advisory lists a 6.x fix. Declarative mode is
excluded from the deserialization advisory. V7 merges react-router-dom INTO react-router. PR#707 (closed
unmerged) bumped only package.json/package-lock.json and left 82 source
files importing a package that had become a re-export shim. That is a
migration, not a bump, and .github/dependabot.yml pinned a MAJOR ignore that
points at this row and must leave with it.

FOUR THINGS ARE PINNED, EACH WITH THE OTHER ANSWER REACHABLE:

  * the declared range: `react-router` in dependencies at or above 7.18.0,
    and `react-router-dom` absent from every section (a leftover direct
    dependency would let the old import path type-check again);
  * the lock: EVERY `node_modules/react-router` key -- the hoisted one and
    any nested `.../node_modules/react-router` copy -- resolves >= 7.18.0
    (648c: a hoisted-only read let a nested 6.30.5 copy board SECURE), and
    `node_modules/react-router-dom` is not in the lock at any key, because a
    transitive re-appearance is exactly how the shim would come back;
  * the source: zero module specifiers naming `react-router-dom` under
    frontend/src, over a denominator enumerated from `git ls-files`, with a
    planted-import control proving the scanner can say YES;
  * dependabot: no MAJOR ignore for react-router remains, with the 900c08fc
    shape as the positive control.

DELIBERATELY NOT ASSERTED: the installed node_modules version (owned by
test_frontend_dependency_security_floor.py in the node-provisioned shard) and
the vitest/tsc/build outcome (owned by the frontend build gates). This file
needs no node and runs in parity-static.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is declared in requirements-test.txt; a missing import here "
           "means the test environment is unprovisioned, not that dependabot "
           "is configured correctly")

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_FRONTEND = _REPO / "frontend"
_DEPENDABOT = _REPO / ".github" / "dependabot.yml"

# first_patched_version for BOTH advisories the row names.
_FLOOR = (7, 18, 0)
_SECTIONS = ("dependencies", "devDependencies")

# Match literal package names only after classifying their module context.
_DOM_SPECIFIER = re.compile(r"""['"]react-router-dom(?:/[^'"]*)?['"]""")
_ROUTER_SPECIFIER = re.compile(r"""['"]react-router(?:/[^'"]*)?['"]""")
_SOURCE_TOKEN = re.compile(
    r"(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
    r"|[A-Za-z_$][\w$]*|[^\s]"
)


def _code_tokens(source: str, start: int = 0, interpolation: bool = False
                 ) -> tuple[list[tuple[str | None, str]], int]:
    """Skip template prose, retaining executable interpolation tokens."""
    tokens: list[tuple[str | None, str]] = []
    depth = 0
    while match := _SOURCE_TOKEN.search(source, start):
        start = match.end()
        kind, token = match.lastgroup, match.group()
        if kind == "comment":
            continue
        if token == "}" and interpolation and depth == 0:
            return tokens, start
        if token == "`":
            tokens.append((None, ";"))
            while start < len(source):
                if source[start] == "\\":
                    start += 2
                elif source[start] == "`":
                    start += 1
                    break
                elif source.startswith("${", start):
                    body, start = _code_tokens(source, start + 2, True)
                    tokens.extend(body)
                    tokens.append((None, ";"))
                else:
                    start += 1
            tokens.append((None, ";"))
            continue
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        tokens.append((kind, token))
    return tokens, len(source)


def _literal_module_specifiers(source: str) -> list[str]:
    """Literal import/export/require and Vitest/Jest module arguments.

    Tokenize comments and whole strings first, so quoted examples are not
    code, including prose around executable template interpolations. Computed
    module names are outside this literal census; this is not a JS parser.
    """
    tokens: list[str] = []
    specifiers: list[str] = []
    for kind, token in _code_tokens(source)[0]:
        if kind == "string":
            static = tokens[-1:] in (["from"], ["import"])
            call = tokens[-1:] == ["("] and (
                tokens[-2:-1] in (["import"], ["require"])
                or (tokens[-3:-2] == ["."]
                    and tokens[-4:-3] in (["vi"], ["jest"])
                    and tokens[-2:-1] in (["mock"], ["doMock"], ["unmock"],
                                           ["importActual"], ["requireActual"]))
            )
            if static or call:
                specifiers.append(token)
        tokens.append(token)
    return specifiers


def _numeric_semver(value: str) -> tuple[int, int, int]:
    assert "-" not in value, f"prerelease dependency is not allowed: {value!r}"
    parts = value.split("+", 1)[0].split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"expected a numeric SemVer version, got {value!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def _range_minimum(spec: str) -> tuple[int, int, int]:
    """Lowest version a `^x.y.z` / `~x.y.z` / bare pin admits; refuses others."""
    text = spec.strip()
    prefix = text[:1] if text[:1] in "^~" else ""
    rest = text[len(prefix):]
    assert rest and rest[0].isdigit(), (
        f"UNKNOWN dependency range shape {spec!r}: this gate compares only "
        "'^x.y.z', '~x.y.z' and bare pins, and will not guess at any other form")
    return _numeric_semver(rest)


def _sections_declaring(package: dict, name: str) -> list[str]:
    return [s for s in _SECTIONS if name in package.get(s, {})]


def _is_copy_of(key: str, suffix: str) -> bool:
    return key == suffix or key.endswith("/" + suffix)


def _lock_copies(packages: dict, name: str) -> dict[str, dict]:
    """EVERY lock key holding `name`: `node_modules/<name>` and each nested
    `.../node_modules/<name>`. Row 648b read only the hoisted key and a nested
    6.30.5 copy escaped the floor; every copy is a real resolvable module."""
    suffix = f"node_modules/{name}"
    copies: dict[str, dict] = {}
    for key, entry in packages.items():
        if _is_copy_of(key, suffix):
            copies[key] = entry
    return copies


def _lock_copies_below_floor(packages: dict, name: str) -> list[tuple[str, str]]:
    return [(key, entry["version"])
            for key, entry in _lock_copies(packages, name).items()
            if _numeric_semver(entry["version"]) < _FLOOR]


def _tracked_frontend_sources(repo: Path) -> list[Path]:
    """The denominator, from the index and never from a handed list."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--",
         "frontend/src/*.ts", "frontend/src/*.tsx",
         "frontend/src/*.js", "frontend/src/*.jsx"],
        capture_output=True, text=True, check=True).stdout
    return [repo / p for p in out.split("\0") if p]


def _files_with_specifier(paths: list[Path], pattern: re.Pattern[str]) -> list[Path]:
    return [p for p in paths if any(
        pattern.fullmatch(specifier) for specifier in
        _literal_module_specifiers(p.read_text(encoding="utf-8")))]


def _react_router_major_ignores(config: dict) -> list[dict]:
    """Every dependabot ignore entry that would suppress a react-router MAJOR."""
    found: list[dict] = []
    for update in config.get("updates", []) or []:
        for entry in update.get("ignore", []) or []:
            name = str(entry.get("dependency-name", ""))
            types = entry.get("update-types") or []
            if name.startswith("react-router") and (
                    not types or "version-update:semver-major" in types):
                found.append(entry)
    return found


# --------------------------------------------------------------------------- #
# Controls: the instrument can return the other answer.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("statement", [
    "import { Link } from 'react-router-dom'",
    "import Router from 'react-router-dom'",
    "import * as Router from 'react-router-dom'",
    "import 'react-router-dom'",
    "export { Link } from 'react-router-dom'",
    "export * from 'react-router-dom'",
    "const router = import('react-router-dom')",
    "const router = require('react-router-dom')",
    "vi.mock('react-router-dom')",
    "jest.requireActual('react-router-dom')",
    "import /* package */ { Link } from 'react-router-dom/server'",
    "const label = `loaded ${import('react-router-dom')}`",
    "const label = `loaded ${({load: () => import('react-router-dom')})}`",
    "const label = `outer ${`inner ${import('react-router-dom')}`}`",
])
def test_scanner_finds_a_planted_react_router_dom_import(
        tmp_path: Path, statement: str) -> None:
    """NEGATIVE CONTROL with an exact count: three files, exactly one offender."""
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "dom.tsx").write_text(
        statement + "\n", encoding="utf-8")
    (src / "router.tsx").write_text(
        'import { Link } from "react-router"\n', encoding="utf-8")
    (src / "prose.tsx").write_text(
        "// import { Link } from 'react-router-dom'\n"
        "/* import 'react-router-dom'; */\n"
        "const oldPackage = 'react-router-dom';\n"
        'const example = "import Router from \'react-router-dom\'";\n'
        "const documentation = `import 'react-router-dom'`;\n"
        "const label = `package ${'react-router-dom'}`;\n"
        "const escaped = `literal \\${import('react-router-dom')}`;\n",
        encoding="utf-8")
    paths = sorted(src.glob("*.tsx"))
    assert len(paths) == 3, "fixture denominator must be exactly three files"

    offenders = _files_with_specifier(paths, _DOM_SPECIFIER)
    assert [p.name for p in offenders] == ["dom.tsx"], (
        f"scanner must name exactly the planted importer, got {offenders!r}")
    assert len(offenders) == 1
    consolidated = _files_with_specifier(paths, _ROUTER_SPECIFIER)
    assert [p.name for p in consolidated] == ["router.tsx"], (
        "the react-router specifier must not match the -dom specifier or prose")


def test_lock_reader_floors_a_nested_react_router_copy() -> None:
    """NEGATIVE CONTROL (the row 648b escape): a scratch lock with the hoisted
    copy at 7.18.3 and a nested copy at 6.30.5 under another package. The reader
    must return both keys and the floor must name the nested one; a hoisted-only
    read sees one copy and passes."""
    nested = "node_modules/some-widget/node_modules/react-router"
    scratch = {
        "": {"name": "frontend"},
        "node_modules/react-router": {"version": "7.18.3"},
        "node_modules/some-widget": {"version": "1.0.0"},
        nested: {"version": "6.30.5"},
        "node_modules/react-router-devtools": {"version": "0.1.0"},
    }
    assert sorted(_lock_copies(scratch, "react-router")) == [
        "node_modules/react-router", nested], (
        "the reader must find the hoisted AND the nested copy and nothing else")
    assert _lock_copies_below_floor(scratch, "react-router") == [(nested, "6.30.5")], (
        "row 648: the nested 6.30.5 copy must be the one and only key named below the floor")
    clean = {k: v for k, v in scratch.items() if k != nested}
    assert _lock_copies_below_floor(clean, "react-router") == [], (
        "POSITIVE CONTROL: with the nested copy removed nothing is below the floor")


def test_range_minimum_refuses_unknown_shapes_and_orders_versions() -> None:
    assert _range_minimum("^7.18.3") == (7, 18, 3)
    assert _range_minimum("~7.18.0") >= _FLOOR
    assert _range_minimum("^6.30.5") < _FLOOR, (
        "the pre-migration range must NOT satisfy the floor")
    assert _range_minimum("^7.17.9") < _FLOOR, (
        "a 7.x below first_patched_version must NOT satisfy the floor")
    for shape in (">=7.18.0", "*", "7 || 8", "github:x/y", ""):
        with pytest.raises(AssertionError, match=(
                "UNKNOWN dependency range shape|expected a numeric SemVer")):
            _range_minimum(shape)


def test_dependabot_probe_finds_the_900c08fc_ignore_shape() -> None:
    """POSITIVE CONTROL: the exact block this cut removes is detected."""
    before = {"updates": [{
        "package-ecosystem": "npm", "directory": "/frontend",
        "ignore": [{"dependency-name": "react-router",
                    "update-types": ["version-update:semver-major"]}]}]}
    found = _react_router_major_ignores(before)
    assert len(found) == 1 and found[0]["dependency-name"] == "react-router"
    # An ignore with no update-types suppresses EVERYTHING, majors included.
    bare = {"updates": [{"ignore": [{"dependency-name": "react-router-dom"}]}]}
    assert len(_react_router_major_ignores(bare)) == 1
    # A minor-only ignore is not a major ignore; the probe must not over-match.
    minor = {"updates": [{"ignore": [{"dependency-name": "react-router",
                                       "update-types": ["version-update:semver-minor"]}]}]}
    assert _react_router_major_ignores(minor) == []


# --------------------------------------------------------------------------- #
# The gate.
# --------------------------------------------------------------------------- #

def test_frontend_declares_react_router_7_and_no_react_router_dom() -> None:
    package = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))

    dom_sections = _sections_declaring(package, "react-router-dom")
    assert dom_sections == [], (
        f"row 648: react-router-dom is still declared in {dom_sections!r} of "
        "frontend/package.json; v7 merged it into react-router and the direct "
        "dependency must go")

    router_sections = _sections_declaring(package, "react-router")
    assert router_sections == ["dependencies"], (
        f"row 648: react-router must be a direct runtime dependency, found in "
        f"{router_sections!r}")
    declared = package["dependencies"]["react-router"]
    assert _range_minimum(declared) >= _FLOOR, (
        f"row 648: react-router declares {declared!r}, whose lowest admitted "
        f"version is below first_patched_version {'.'.join(map(str, _FLOOR))}")


def test_frontend_lock_resolves_react_router_7_and_drops_the_shim() -> None:
    lock = json.loads((_FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    assert packages, "the lock denominator is zero; the verdict is UNKNOWN"

    copies = _lock_copies(packages, "react-router")
    assert copies, (
        "row 648: react-router is absent from frontend/package-lock.json at "
        "every node_modules/ key; the lock verdict is UNKNOWN")
    below = _lock_copies_below_floor(packages, "react-router")
    assert not below, (
        f"row 648: the lock resolves react-router below "
        f"{'.'.join(map(str, _FLOOR))} at {below!r}; every copy, hoisted or "
        "nested, must be at least first_patched_version")

    shims = sorted(k for k in packages if k.endswith("node_modules/react-router-dom"))
    assert shims == [], (
        f"row 648: react-router-dom is still in the lock at {shims!r}; the v7 "
        "shim must not come back transitively")


def test_no_frontend_source_imports_react_router_dom() -> None:
    sources = _tracked_frontend_sources(_REPO)
    assert len(sources) > 0, (
        "the frontend/src denominator is zero; the verdict is UNKNOWN")

    offenders = _files_with_specifier(sources, _DOM_SPECIFIER)
    rel = sorted(str(p.relative_to(_REPO)) for p in offenders)
    assert len(offenders) == 0, (
        f"row 648: {len(offenders)} of {len(sources)} tracked frontend sources "
        f"still import from 'react-router-dom' (first: {rel[:5]}); consolidate "
        "into 'react-router'")
    consolidated = _files_with_specifier(sources, _ROUTER_SPECIFIER)
    assert len(consolidated) > 0, (
        "no tracked frontend source imports react-router at all; the routes "
        "did not consolidate, they vanished")


def test_dependabot_no_longer_ignores_react_router_majors() -> None:
    config = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    assert config.get("updates"), (
        "dependabot.yml declares no updates; the ignore verdict is UNKNOWN")
    found = _react_router_major_ignores(config)
    assert found == [], (
        f"row 648: .github/dependabot.yml still ignores react-router majors "
        f"({found!r}); the ignore was scoped to survive only until this row "
        "landed")
