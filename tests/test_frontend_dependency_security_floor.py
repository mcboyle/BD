"""The frontend dependency security floor, enforced as a FLOOR.

WHY THIS FILE WAS REWRITTEN AT ROW 326. It had four defects, and the first hid
the second in production:

  * IT NEVER RAN. The filename appeared ZERO times in .github/workflows/ci.yml,
    which schedules pytest only over explicitly named `matrix.suites` entries --
    there is no catch-all shard. The file also declared no BD_GATE_SCOPE and sat
    in tests/gate_scope_baseline.txt, so the frozen legacy classification
    exempted it from the shard-coverage assertion in
    test_v3_66_939_ci_gate_shards_cover_every_gate.py. Nothing scheduled it and
    nothing noticed.
  * AND SO IT WAS RED ON MAIN. Measured at v3.66.1304 (d7f3114): package.json
    declares react-router-dom "^6.30.5" while the old test asserted equality
    with "^6.30.4". A legitimate PATCH UPGRADE, above the security floor, had
    already tripped the gate -- silently, because nothing ran it.
  * EXACT EQUALITY INVERTED THE GATE. `== "^6.30.4"` fails "^6.31.0" and would
    fail "^7.x" adopted in response to a future advisory. A floor gate that
    refuses the remediation it exists to require is the inverse defect CLAUDE.md
    A7 names.
  * A MOVED DEPENDENCY RAISED KeyError. Moving react-router-dom to
    devDependencies produced `KeyError: 'react-router-dom'` rather than a
    diagnostic saying what moved and where it went.
  * THE TWO POPULATIONS DISAGREED. This file checked 3 packages; the lock test
    carried 7, unreconciled, with nothing asserting either set was complete.

ONE MAP IS NOW THE AUTHORITY. _SECURE_FLOORS below is consumed by the declared
range check, the lock check and the installed check, so the three populations
cannot drift apart. ROW 648c: the lock and installed checks floor EVERY copy
of a name (hoisted and nested), because a hoisted-only read boarded a nested
vulnerable copy as SECURE (the row 648b refutation). Entries carry the section their direct range lives in, or
None when the package reaches the tree only as a transitive dependency and
therefore has no declared range to check -- four of the seven do.

THE INSTALLED CHECK NEEDS NODE, so this whole gate is scheduled in the
`parity-graph` shard, the only one for which gate-suites runs setup-node and
`npm ci` (see test_v3_66_1218_vitest_delegating_shards_have_node.py). Scheduling
it anywhere else would make the installed check a gate that can only run where
it cannot pass -- the defect row 319 fixed.
"""

import json
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

# name -> (floor, section-of-the-declared-range or None if transitive-only)
# ROW 648: react-router-dom is gone (v7 merged it into react-router) and
# react-router became the DIRECT dependency, floored at 7.18.0 -- the
# first_patched_version for both advisories the 6.x line never received.
_SECURE_FLOORS = {
    "react-router": ("7.18.0", "dependencies"),
    "vite": ("6.4.3", "devDependencies"),
    "vitest": ("3.2.7", "devDependencies"),
    "form-data": ("4.0.6", None),
    "esbuild": ("0.25.0", None),
    "vite-node": ("2.2.0", None),
}
_SECTIONS = ("dependencies", "devDependencies")

# ROW A (648c): a floor VALUE is pinned to the NAMED advisories it closes, so a
# quietly lowered floor is refused by name rather than measured against itself.
# name -> ((advisory id, first_patched_version), ...). Measured, not typed from
# memory: `npm audit --package-lock-only --json` against the pre-row648 lock
# (66178294, react-router-dom ^6.30.5) reports both with range `<7.18.0`.
_ADVISORIES = {
    "react-router": (
        # open redirect via backslash in <Link> and useNavigate
        ("GHSA-wrjc-x8rr-h8h6", "7.18.0"),
        # arbitrary constructor injection via deserializeErrors() (SSR hydration)
        ("GHSA-337j-9hxr-rhxg", "7.18.0"),
    ),
}


def _load_json(name: str) -> dict:
    return json.loads((FRONTEND / name).read_text(encoding="utf-8"))


def _is_copy_of(key: str, suffix: str) -> bool:
    """True for the hoisted key `node_modules/<name>` AND every nested
    `.../node_modules/<name>` key: a nested copy is a real installed module
    that the bundler resolves for the package it sits under."""
    return key == suffix or key.endswith("/" + suffix)


def _lock_copies(lock_packages: dict, name: str) -> dict[str, dict]:
    """EVERY copy of `name` in the lock, never only the top-level one.

    ROW 648c. The predecessor read `lock_packages.get(f"node_modules/{name}")`
    and was REFUTED for it: a second copy nested under another package
    (`node_modules/some-widget/node_modules/react-router` at 6.30.5) escaped the
    floor entirely, so a re-nested vulnerable copy would board SECURE. The floor
    is a floor on every copy, hoisted or nested, and the census below asserts
    the denominator is nonzero before anything is compared.
    """
    suffix = f"node_modules/{name}"
    copies: dict[str, dict] = {}
    for key, entry in lock_packages.items():
        if _is_copy_of(key, suffix):
            copies[key] = entry
    return copies


def _floor_matches(floor: str, first_patched: str) -> bool:
    return _numeric_semver(floor) == _numeric_semver(first_patched)


def _floors_off_their_advisories(floors: dict, advisories: dict) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(name, floor, first_patched, advisory ids) for every floor that is not
    the highest first_patched_version of the advisories recorded for it."""
    off: list[tuple[str, str, str, tuple[str, ...]]] = []
    for name, records in advisories.items():
        floor = floors[name][0]
        first_patched = max((v for _id, v in records), key=_numeric_semver)
        if not _floor_matches(floor, first_patched):
            off.append((name, floor, first_patched, tuple(i for i, _v in records)))
    return off


def _copies_below_floor(copies: dict, floor: str) -> list[tuple[str, str]]:
    """(location, version) for every copy below the floor, in lock order."""
    return [(key, entry["version"]) for key, entry in copies.items()
            if _numeric_semver(entry["version"]) < _numeric_semver(floor)]


def _numeric_semver(value: str) -> tuple[int, int, int]:
    """Return the numeric SemVer core without adding a test-only dependency."""
    assert "-" not in value, f"prerelease dependency is not allowed: {value!r}"
    core = value.split("+", 1)[0]
    parts = core.split(".")
    assert len(parts) == 3 and all(part.isdigit() for part in parts), (
        f"expected a numeric SemVer version, got {value!r}"
    )
    return tuple(int(part) for part in parts)


def _range_minimum(spec: str) -> tuple[int, int, int]:
    """The lowest version a declared range admits.

    UNKNOWN RANGE SHAPES REFUSE. Only `^`, `~` and a bare pin exist in this
    manifest today. Guessing at `>=`, `||`, `*` or a git/url specifier would
    silently compare the wrong number, which is worse than refusing to compare
    at all -- so an unrecognised shape is named and fails.
    """
    text = spec.strip()
    assert text, "empty dependency range"
    prefix = text[0] if text[0] in "^~" else ""
    rest = text[len(prefix):]
    assert rest and rest[0].isdigit(), (
        f"UNKNOWN dependency range shape {spec!r}: this gate compares only "
        "'^x.y.z', '~x.y.z' and bare pins, and will not guess at any other form"
    )
    return _numeric_semver(rest)


def _declared_range(package: dict, name: str, section: str) -> str:
    """The declared range, or a refusal that says what actually happened."""
    found = [s for s in _SECTIONS if name in package.get(s, {})]
    assert found, (
        f"{name} is required in frontend/package.json {section!r} and is "
        f"absent from every section {_SECTIONS!r}"
    )
    assert section in found, (
        f"{name} moved: expected in {section!r}, found in {found!r}. A section "
        "change is a review decision, not a silent equivalence"
    )
    return package[section][name]


def _direct_floors() -> dict[str, str]:
    return {n: f for n, (f, s) in _SECURE_FLOORS.items() if s is not None}


def test_numeric_semver_rejects_prerelease_versions() -> None:
    try:
        _numeric_semver("2.2.0-beta.2")
    except AssertionError:
        return
    raise AssertionError("prerelease dependency versions must not satisfy a stable floor")


def test_range_minimum_refuses_unknown_range_shapes() -> None:
    """NEGATIVE CONTROL for the parser: it must refuse, not guess."""
    assert _range_minimum("^6.30.5") == (6, 30, 5)
    assert _range_minimum("~3.2.7") == (3, 2, 7)
    assert _range_minimum("6.4.3") == (6, 4, 3)
    for shape in (">=6.30.4", "*", "6 || 7", "github:x/y", ""):
        try:
            _range_minimum(shape)
        except AssertionError:
            continue
        raise AssertionError(f"unknown range shape {shape!r} was silently accepted")


def test_a_declared_range_below_the_floor_is_refused() -> None:
    """NEGATIVE CONTROL: moving to floors must not surrender the other direction.

    The old equality check caught downgrades by accident. This proves the floor
    comparison still catches them on purpose.
    """
    floor = _numeric_semver("6.30.4")
    assert _range_minimum("^6.29.0") < floor, "a downgrade must not satisfy the floor"
    assert _range_minimum("^6.30.4") >= floor
    assert _range_minimum("^6.31.0") >= floor, (
        "a legitimate upgrade above the floor must satisfy it -- refusing it is "
        "the inverted gate row 326 removed"
    )


def test_a_moved_dependency_is_named_rather_than_raising_keyerror() -> None:
    """NEGATIVE CONTROL for finding 2, without touching the real manifest."""
    moved = {"dependencies": {}, "devDependencies": {"react-router": "^7.18.3"}}
    try:
        _declared_range(moved, "react-router", "dependencies")
    except AssertionError as exc:
        assert "moved" in str(exc) and "devDependencies" in str(exc), str(exc)
    except KeyError:  # pragma: no cover -- this is the defect being removed
        raise AssertionError("a moved dependency still raises a bare KeyError")
    else:
        raise AssertionError("a moved dependency was accepted")

    absent = {"dependencies": {}, "devDependencies": {}}
    try:
        _declared_range(absent, "react-router", "dependencies")
    except AssertionError as exc:
        assert "absent from every section" in str(exc), str(exc)
    else:
        raise AssertionError("an absent dependency was accepted")


def test_every_recorded_advisory_pins_its_floor_value() -> None:
    """ROW A (648c): the react-router floor IS its advisories' first_patched_version.
    Lowering `_SECURE_FLOORS["react-router"]` below 7.18.0 is refused here by
    advisory id; before this test the file stayed green at 6.0.0 (S4 escaped)."""
    assert set(_ADVISORIES) <= set(_SECURE_FLOORS), (
        f"advisories recorded for a name with no floor: {sorted(set(_ADVISORIES) - set(_SECURE_FLOORS))}"
    )
    assert "react-router" in _ADVISORIES, "row 648's two advisories must stay recorded"
    off = _floors_off_their_advisories(_SECURE_FLOORS, _ADVISORIES)
    assert not off, (
        f"floor value off its named advisories: {off!r}; the floor must equal the "
        "highest first_patched_version of the advisories it closes"
    )


def test_a_lowered_floor_is_refused_by_advisory_id() -> None:
    """NEGATIVE CONTROL for the advisory pin: a scratch floor map with
    react-router lowered to 6.0.0 must be named against both advisory ids."""
    lowered = dict(_SECURE_FLOORS, **{"react-router": ("6.0.0", "dependencies")})
    off = _floors_off_their_advisories(lowered, _ADVISORIES)
    assert off == [("react-router", "6.0.0", "7.18.0",
                    ("GHSA-wrjc-x8rr-h8h6", "GHSA-337j-9hxr-rhxg"))], off
    assert _floors_off_their_advisories(_SECURE_FLOORS, _ADVISORIES) == [], (
        "POSITIVE CONTROL: the real map is on its advisories"
    )


def test_frontend_declares_secure_direct_dependency_ranges() -> None:
    package = _load_json("package.json")
    direct = _direct_floors()
    assert direct, "the direct floor denominator is zero; the verdict is UNKNOWN"

    for name, floor in direct.items():
        section = _SECURE_FLOORS[name][1]
        declared = _declared_range(package, name, section)
        assert _range_minimum(declared) >= _numeric_semver(floor), (
            f"{name} declares {declared!r}, whose lowest admitted version is "
            f"below the security floor {floor}"
        )


def test_frontend_lock_resolves_secure_dependency_floors() -> None:
    lock_packages = _load_json("package-lock.json")["packages"]
    assert _SECURE_FLOORS, "the floor denominator is zero; the verdict is UNKNOWN"

    for name, (floor, _section) in _SECURE_FLOORS.items():
        copies = _lock_copies(lock_packages, name)
        assert copies, (
            f"{name} carries a security floor but does not appear in "
            "frontend/package-lock.json at any node_modules/ key; the lock "
            "verdict for it is UNKNOWN"
        )
        below = _copies_below_floor(copies, floor)
        assert not below, (
            f"{name} resolves below the floor {floor} at lock key(s) "
            f"{below!r}; every copy, hoisted or nested, must be at least {floor}"
        )


def test_a_nested_lock_copy_below_the_floor_is_refused() -> None:
    """NEGATIVE CONTROL for the lock reader (the row 648b escape).

    A scratch lock carries the hoisted copy at the floor and a second copy
    nested under another package below it. The reader must enumerate BOTH and
    the floor must refuse the nested one BY ITS KEY; a hoisted-only read
    returns one copy and passes, which is the defect being pinned.
    """
    nested = "node_modules/some-widget/node_modules/react-router"
    scratch = {
        "": {"name": "frontend"},
        "node_modules/react-router": {"version": "7.18.3"},
        "node_modules/some-widget": {"version": "1.0.0"},
        nested: {"version": "6.30.5"},
        "node_modules/react-router-devtools": {"version": "0.1.0"},
    }
    copies = _lock_copies(scratch, "react-router")
    assert sorted(copies) == ["node_modules/react-router", nested], (
        f"the reader must find the hoisted AND the nested copy and nothing "
        f"else (react-router-devtools is a different package), got {sorted(copies)}"
    )
    assert _copies_below_floor(copies, "7.18.0") == [(nested, "6.30.5")], (
        "the nested 6.30.5 copy must be the one and only copy named below the floor"
    )
    clean = {k: v for k, v in scratch.items() if k != nested}
    assert _copies_below_floor(_lock_copies(clean, "react-router"), "7.18.0") == [], (
        "POSITIVE CONTROL: with the nested copy removed nothing is below the floor"
    )


def _installed_version(modules: Path, name: str) -> str:
    """The version actually installed, or a named UNKNOWN refusal.

    THIS IS A SEPARATE FUNCTION SO SOMETHING OTHER THAN THE TEST BODY CONSTRAINS
    IT. When the absence check lived inline, a mutant that redirected the path
    and returned early ESCAPED the battery: the only thing asserting "absent is
    UNKNOWN" was the code being mutated, so nothing could catch its removal.
    test_an_absent_node_modules_is_unknown_rather_than_a_skip now pins it from
    the outside, against a fixture directory -- never against the real tree,
    whose node_modules is a symlink into the integrator's checkout.
    """
    assert modules.is_dir(), (
        f"UNKNOWN installed versions: {modules} is absent, so this gate measured "
        "nothing. Run `npm ci` in frontend/; this is a failure, not a skip"
    )
    manifest = modules / name / "package.json"
    assert manifest.is_file(), (
        f"UNKNOWN installed version for {name}: {manifest} is absent"
    )
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def _package_dirs(modules: Path):
    """Every package directory directly under a node_modules directory,
    descending one level into @scope/ directories; dotfiles (.bin, .cache)
    and files are not packages."""
    for child in sorted(modules.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if child.name.startswith("@"):
            yield from (c for c in sorted(child.iterdir()) if c.is_dir())
        else:
            yield child


def _nested_manifests(modules: Path, name: str) -> list[Path]:
    """Every `<pkg>/node_modules/<name>/package.json` below `modules`, at any
    depth: the nested copies npm installs when a dependency needs a version
    other than the hoisted one."""
    found: list[Path] = []
    for package_dir in _package_dirs(modules):
        nested = package_dir / "node_modules"
        if not nested.is_dir():
            continue
        manifest = nested / name / "package.json"
        if manifest.is_file():
            found.append(manifest)
        found.extend(_nested_manifests(nested, name))
    return found


def _installed_copies(modules: Path, name: str) -> dict[Path, str]:
    """EVERY installed copy of `name`: the hoisted one (absence stays a named
    UNKNOWN via _installed_version) plus each nested one, keyed by manifest.

    ROW 648c: the predecessor read only `modules/<name>/package.json`, so a
    nested vulnerable copy that another package actually resolves was never
    measured -- the same hoisted-only escape as the lock reader above.
    """
    copies = {modules / name / "package.json": _installed_version(modules, name)}
    for manifest in _nested_manifests(modules, name):
        copies[manifest] = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    return copies


def _installed_below_floor(copies: dict, floor: str) -> list[tuple[str, str]]:
    return [(str(path), version) for path, version in copies.items()
            if _numeric_semver(version) < _numeric_semver(floor)]


def test_a_nested_installed_copy_below_the_floor_is_refused(tmp_path) -> None:
    """NEGATIVE CONTROL for the installed reader, against a fixture tree:
    hoisted react-router at the floor, a nested copy two levels down below it,
    a scoped package on the way, and an unrelated nested package."""
    modules = tmp_path / "node_modules"

    def plant(rel: str, version: str) -> Path:
        manifest = modules / rel / "package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
        return manifest

    plant("react-router", "7.18.3")
    plant("@scope/widget", "1.0.0")
    plant("@scope/widget/node_modules/inner", "1.0.0")
    deep = plant("@scope/widget/node_modules/inner/node_modules/react-router", "6.30.5")
    plant("other/node_modules/lodash", "4.17.21")
    (modules / ".bin").mkdir()

    copies = _installed_copies(modules, "react-router")
    assert sorted(copies) == sorted([modules / "react-router" / "package.json", deep]), (
        f"the reader must find the hoisted AND the deep nested copy, got {sorted(copies)}"
    )
    assert _installed_below_floor(copies, "7.18.0") == [(str(deep), "6.30.5")], (
        "the nested 6.30.5 copy must be the one and only copy named below the floor"
    )
    deep.unlink()
    assert _installed_below_floor(_installed_copies(modules, "react-router"), "7.18.0") == [], (
        "POSITIVE CONTROL: with the nested copy removed nothing is below the floor"
    )


def test_an_absent_node_modules_is_unknown_rather_than_a_skip(tmp_path) -> None:
    """NEGATIVE CONTROL: an unmeasured population must fail, not pass quietly."""
    try:
        _installed_version(tmp_path / "node_modules", "vite")
    except AssertionError as exc:
        assert "UNKNOWN installed versions" in str(exc), str(exc)
    else:
        raise AssertionError("an absent node_modules was accepted as measured")

    present = tmp_path / "node_modules"
    (present / "vite").mkdir(parents=True)
    try:
        _installed_version(present, "vite")
    except AssertionError as exc:
        assert "UNKNOWN installed version for vite" in str(exc), str(exc)
    else:
        raise AssertionError("a package with no manifest was accepted as measured")

    (present / "vite" / "package.json").write_text(
        json.dumps({"version": "6.4.3"}), encoding="utf-8")
    assert _installed_version(present, "vite") == "6.4.3", (
        "the positive path must still read a real installed version"
    )


def test_installed_frontend_dependencies_meet_secure_floors() -> None:
    """What is INSTALLED, not only what is declared or locked.

    A range does not pin a version and a lock only describes an intended tree.
    This gate is scheduled in the node-provisioned shard so the tree is really
    there; absence is UNKNOWN and fails, via _installed_version above.
    """
    modules = FRONTEND / "node_modules"
    for name, (floor, _section) in _SECURE_FLOORS.items():
        below = _installed_below_floor(_installed_copies(modules, name), floor)
        assert not below, (
            f"{name} is INSTALLED below the floor {floor} at {below!r}; every "
            f"copy, hoisted or nested, must be at least {floor}"
        )


def test_every_secure_floor_is_checked_by_every_population() -> None:
    """The three populations share one map and cannot drift apart.

    The defect this replaces was two hand-written literals -- 3 names here and 7
    in the lock test -- that nobody reconciled, so a newly added vulnerable
    direct dependency was invisible to both.
    """
    package = _load_json("package.json")
    lock_packages = _load_json("package-lock.json")["packages"]
    # Pinned by IDENTITY, not by count: row 648 retired react-router-dom from
    # the tree (7 -> 6), and a count floor would either refuse that or hide a
    # same-size swap. A retired name is removed HERE in the same cut, on purpose.
    assert set(_SECURE_FLOORS) == {
        "react-router", "vite", "vitest", "form-data", "esbuild", "vite-node",
    }, f"the floor map changed shape: {sorted(_SECURE_FLOORS)}"

    for name, (floor, section) in _SECURE_FLOORS.items():
        _numeric_semver(floor)
        assert section in (*_SECTIONS, None), f"{name}: bad section {section!r}"
        assert _lock_copies(lock_packages, name), (
            f"{name} appears at no node_modules/ key in the lock"
        )
        if section is None:
            assert not any(name in package.get(s, {}) for s in _SECTIONS), (
                f"{name} is marked transitive but package.json declares it "
                "directly; it now needs a declared-range check"
            )
        else:
            assert name in package.get(section, {}), name

    direct = _direct_floors()
    declared_here = set(direct)
    assert declared_here == {
        n for n, (_f, s) in _SECURE_FLOORS.items() if s is not None
    }, "the direct subset disagrees with the map it is derived from"
