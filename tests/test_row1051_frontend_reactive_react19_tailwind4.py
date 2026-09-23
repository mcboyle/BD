"""Row 1051: the SPA is built on React 19 and Tailwind CSS 4 (Oxide, via @tailwindcss/vite).

The cutover is a property of frontend/ -- the declared and locked dependency
versions, the Vite plugin chain and the CSS entrypoint -- so that is what this
file measures. Versions are compared by parsed major, never by substring
("^18.19.0" contains "19"; "^3.4.13" is not Tailwind 4).

The lock must also be installable where CI installs it: every tarball is the
public registry's URL for that name@version. A lock written behind a local
mirror (~/.npmrc registry=http://127.0.0.1:4873) pins the mirror, which a
GitHub runner cannot reach -- `npm ci` died there ("Exit handler never
called!") and vitest was never installed (T67 run 35808387178).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_FE = Path(__file__).resolve().parents[1] / "frontend"
_NPM = "https://registry.npmjs.org/"

_WANT_MAJOR = {
    "react": 19, "react-dom": 19, "@types/react": 19, "@types/react-dom": 19,
    "tailwindcss": 4, "@tailwindcss/vite": 4,
}


def _major(spec: str) -> int:
    m = re.fullmatch(r"\s*(?:\^|~|>=|=)?\s*v?(\d+)(?:\.\d+){0,2}(?:[-+][\w.]+)?\s*", spec)
    if not m:
        raise ValueError(f"unparseable version spec {spec!r}")
    return int(m.group(1))


def cutover_problems(pkg: dict) -> list[str]:
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    problems = []
    for name, want in _WANT_MAJOR.items():
        if name not in deps:
            problems.append(f"{name} missing")
        elif _major(deps[name]) != want:
            problems.append(f"{name} {deps[name]} is not {want}.x")
    if "autoprefixer" in deps:
        problems.append("autoprefixer present (Tailwind 4 prefixes via Lightning CSS)")
    return problems


def off_registry(lock: dict) -> list[str]:
    """Lock entries whose "resolved" is not the public registry tarball of their name@version.

    An entry without "resolved" pins no URL, so it cannot send `npm ci` to an
    unreachable host and is not judged. npm writes no "resolved" for a bundled
    child ("inBundle": it ships inside its parent's tarball); npm 10.8.2's own
    --package-lock-only pass on this manifest adds six such entries under
    @tailwindcss/oxide-wasm32-wasi.
    """
    bad = []
    for path, entry in lock["packages"].items():
        if not path or "resolved" not in entry:
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        want = f"{_NPM}{name}/-/{name.rsplit('/', 1)[-1]}-{entry.get('version')}.tgz"
        if entry["resolved"] != want:
            bad.append(path)
    return bad


def v3_utility_sites(src: Path) -> tuple[list[str], list[str]]:
    """(files read, path:line of each v3-only utility or upgrade-tool artifact) over the .ts/.tsx under src.

    outline-none changed meaning in v4 (the v3 behaviour is outline-hidden); the upgrade
    tool also rewrote Button/Badge variant="outline" to "outline-solid". Specs are read
    too: the scan only forbids, so a spec can add a finding but never hide one, and a spec
    rendering a variant that does not exist is itself wrong. An empty or absent tree is
    UNKNOWN, not a pass, so it raises.
    """
    files = sorted(p for p in src.rglob("*.ts*")
                   if p.suffix in (".ts", ".tsx") and p.is_file()) if src.is_dir() else []
    assert files, f"ROW1051-V3-UTILITY UNKNOWN: no .ts/.tsx under {src}"
    bad = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            if re.search(r"(?<![\w-])outline-none\b", line) or '"outline-solid"' in line:
                bad.append(f"{f.relative_to(src).as_posix()}:{n}")
    return [f.relative_to(src).as_posix() for f in files], bad


@pytest.mark.parametrize("spec,major", [
    ("^19.3.0", 19), ("^18.3.19", 18), ("^18.19.0", 18), ("^3.4.13", 3), ("4.3.3", 4), ("~4.0.0-beta.1", 4),
])
def test_major_is_parsed_not_substring_matched(spec, major):
    assert _major(spec) == major


def test_unparseable_spec_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        _major("latest")


def test_base_shaped_manifest_is_rejected():
    # Positive control (rule 7): the v3.66.1624 manifest and the two strings that
    # defeated the substring gate must each be reported.
    base = {"dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
            "devDependencies": {"@types/react": "^18.3.11", "@types/react-dom": "^18.3.0",
                                "tailwindcss": "^3.4.13", "autoprefixer": "^10.4.20"}}
    assert len(cutover_problems(base)) == 7
    sneaky = {"dependencies": {"react": "^18.19.0", "react-dom": "^18.3.19"},
              "devDependencies": {"@types/react": "^19.0.0", "@types/react-dom": "^19.0.0",
                                  "tailwindcss": "^3.4.13", "@tailwindcss/vite": "^4.0.0"}}
    assert cutover_problems(sneaky) == [
        "react ^18.19.0 is not 19.x", "react-dom ^18.3.19 is not 19.x", "tailwindcss ^3.4.13 is not 4.x"]


def test_declared_dependencies_are_react19_tailwind4():
    pkg = json.loads((_FE / "package.json").read_text(encoding="utf-8"))
    assert cutover_problems(pkg) == [], "ROW1051-NOT-CUT-OVER"


def test_lockfile_resolves_what_is_declared():
    pkg = json.loads((_FE / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((_FE / "package-lock.json").read_text(encoding="utf-8"))
    root = lock["packages"][""]
    assert root.get("dependencies") == pkg.get("dependencies")
    assert root.get("devDependencies") == pkg.get("devDependencies")
    for name, want in _WANT_MAJOR.items():
        got = lock["packages"][f"node_modules/{name}"]["version"]
        assert _major(got) == want, f"ROW1051-LOCK {name} resolves {got}"


def test_off_registry_flags_exactly_the_non_public_tarballs():
    # Negative control + exact count (rule 7): canonical scoped, aliased and bundled
    # entries pass; a mirror URL and a public URL naming another version are the two
    # offenders.
    lock = {"packages": {
        "": {"name": "bulkdownloader-frontend"},
        "node_modules/@scope/a": {"version": "1.0.0", "resolved": _NPM + "@scope/a/-/a-1.0.0.tgz"},
        "node_modules/b": {"version": "2.0.0", "resolved": "http://127.0.0.1:4873/b/-/b-2.0.0.tgz"},
        "node_modules/a/node_modules/c": {"version": "3.0.0", "resolved": _NPM + "c/-/c-3.0.1.tgz"},
        "node_modules/c-alias": {"name": "c", "version": "3.0.1", "resolved": _NPM + "c/-/c-3.0.1.tgz"},
        "node_modules/w/node_modules/tslib": {"version": "2.8.1", "inBundle": True},
    }}
    assert off_registry(lock) == ["node_modules/b", "node_modules/a/node_modules/c"]


def test_lockfile_fetches_every_tarball_from_the_public_registry():
    lock = json.loads((_FE / "package-lock.json").read_text(encoding="utf-8"))
    pinned = [p for p, entry in lock["packages"].items() if p and "resolved" in entry]
    assert "node_modules/react" in pinned  # nonzero denominator, independent of the cutover
    bad = off_registry(lock)
    assert bad == [], f"ROW1051-OFF-REGISTRY {len(bad)} of {len(pinned)}: {bad[:3]}"


@pytest.mark.parametrize("cfg", ["vite.config.ts", "vite.config.js"])
def test_vite_runs_the_tailwind4_plugin(cfg):
    src = (_FE / cfg).read_text(encoding="utf-8")
    assert re.search(r'^import tailwindcss from "@tailwindcss/vite";$', src, re.MULTILINE)
    assert re.search(r"plugins:\s*\[react\(\),\s*tailwindcss\(\)\]", src)
    assert not (_FE / "postcss.config.js").exists(), "v3 PostCSS pipeline still present"


def test_css_entry_is_tailwind4():
    css = (_FE / "src" / "index.css").read_text(encoding="utf-8")
    assert css.startswith('@import "tailwindcss";\n')
    assert '@config "../tailwind.config.js";' in css
    assert not re.search(r"^@tailwind\s", css, re.MULTILINE)


def test_v3_utility_scan_flags_exactly_the_planted_sites(tmp_path):
    # Negative control + exact count (rule 7): v3 outline-none under a variant prefix in
    # product .tsx, in a .ts class string, and an upgrade-tool "outline-solid" in a spec
    # are the three offenders; a comment, the v4 spelling, a prefixed look-alike, the
    # real "outline" variant and a non-source file are not. No tree is UNKNOWN.
    src = tmp_path / "src"
    with pytest.raises(AssertionError, match="UNKNOWN"):
        v3_utility_sites(src)
    (src / "routes").mkdir(parents=True)
    (src / "lib").mkdir()
    with pytest.raises(AssertionError, match="UNKNOWN"):
        v3_utility_sites(src)
    (src / "routes" / "A.tsx").write_text(
        '<input className="rounded focus-visible:outline-none" />\n'
        '  // v3 outline-none is v4 outline-hidden\n'
        '<i className="outline-hidden x-outline-none" />\n'
        '<Button variant="outline" />\n', encoding="utf-8")
    (src / "lib" / "ring.ts").write_text('export const ring = "outline-none ring-2";\n', encoding="utf-8")
    (src / "routes" / "A.test.tsx").write_text('render(<Badge variant="outline-solid" />);\n', encoding="utf-8")
    (src / "README.md").write_text("outline-none\n", encoding="utf-8")
    assert v3_utility_sites(src) == (["lib/ring.ts", "routes/A.test.tsx", "routes/A.tsx"],
                                     ["lib/ring.ts:1", "routes/A.test.tsx:1", "routes/A.tsx:1"])


def test_no_v3_only_utilities_or_upgrade_tool_artifacts_in_src():
    read, bad = v3_utility_sites(_FE / "src")
    assert bad == [], f"ROW1051-V3-UTILITY {len(bad)} of {len(read)} files: {bad}"
    # Positive control: both halves are read, including the file defining the "outline" variant.
    assert "components/ui/button.tsx" in read and any(r.endswith(".test.tsx") for r in read)


def test_python_side_makes_no_frontend_stack_claim():
    # The refuted cut (tree 2c6401e9) shipped bulk_downloader/frontend_reactive.py and an
    # app.py serve_spa_root seam, inside a bare except, that stamped constant
    # "X-Frontend-Framework: React/19" / "X-CSS-Engine: Tailwind-4-Oxide" on every SPA
    # response. The cutover is a property of frontend/; no Python code asserts it.
    pkg = _FE.parent / "bulk_downloader"
    claims = [f"{p.relative_to(pkg.parent)}:{n}" for p in sorted(pkg.rglob("*.py"))
              for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
              if re.search(r"X-Frontend-Framework|X-CSS-Engine|apply_reactive_headers", line)]
    if (pkg / "frontend_reactive.py").exists():
        claims.append("bulk_downloader/frontend_reactive.py exists")
    assert claims == [], f"ROW1051-PY-STACK-CLAIM {claims}"
    # Positive control: the scan reads the module that serves the SPA root.
    assert (pkg / "app.py").read_text(encoding="utf-8").count("def serve_spa_root(") == 1
