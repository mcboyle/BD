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
cannot drift apart. Entries carry the section their direct range lives in, or
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
_SECURE_FLOORS = {
    "react-router-dom": ("6.30.4", "dependencies"),
    "vite": ("6.4.3", "devDependencies"),
    "vitest": ("3.2.7", "devDependencies"),
    "form-data": ("4.0.6", None),
    "esbuild": ("0.25.0", None),
    "react-router": ("6.30.4", None),
    "vite-node": ("2.2.0", None),
}
_SECTIONS = ("dependencies", "devDependencies")


def _load_json(name: str) -> dict:
    return json.loads((FRONTEND / name).read_text(encoding="utf-8"))


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
    moved = {"dependencies": {}, "devDependencies": {"react-router-dom": "^6.30.5"}}
    try:
        _declared_range(moved, "react-router-dom", "dependencies")
    except AssertionError as exc:
        assert "moved" in str(exc) and "devDependencies" in str(exc), str(exc)
    except KeyError:  # pragma: no cover -- this is the defect being removed
        raise AssertionError("a moved dependency still raises a bare KeyError")
    else:
        raise AssertionError("a moved dependency was accepted")

    absent = {"dependencies": {}, "devDependencies": {}}
    try:
        _declared_range(absent, "react-router-dom", "dependencies")
    except AssertionError as exc:
        assert "absent from every section" in str(exc), str(exc)
    else:
        raise AssertionError("an absent dependency was accepted")


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
        entry = lock_packages.get(f"node_modules/{name}")
        assert entry is not None, (
            f"{name} carries a security floor but does not appear in "
            "frontend/package-lock.json; the lock verdict for it is UNKNOWN"
        )
        resolved = entry["version"]
        assert _numeric_semver(resolved) >= _numeric_semver(floor), (
            f"{name} resolves to {resolved}; expected at least {floor}"
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
        installed = _installed_version(modules, name)
        assert _numeric_semver(installed) >= _numeric_semver(floor), (
            f"{name} is INSTALLED at {installed}; expected at least {floor}"
        )


def test_every_secure_floor_is_checked_by_every_population() -> None:
    """The three populations share one map and cannot drift apart.

    The defect this replaces was two hand-written literals -- 3 names here and 7
    in the lock test -- that nobody reconciled, so a newly added vulnerable
    direct dependency was invisible to both.
    """
    package = _load_json("package.json")
    lock_packages = _load_json("package-lock.json")["packages"]
    assert len(_SECURE_FLOORS) >= 7, "the floor map lost entries"

    for name, (floor, section) in _SECURE_FLOORS.items():
        _numeric_semver(floor)
        assert section in (*_SECTIONS, None), f"{name}: bad section {section!r}"
        assert f"node_modules/{name}" in lock_packages, name
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
