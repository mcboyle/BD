import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


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


def test_numeric_semver_rejects_prerelease_versions() -> None:
    try:
        _numeric_semver("2.2.0-beta.2")
    except AssertionError:
        return
    raise AssertionError("prerelease dependency versions must not satisfy a stable floor")


def test_frontend_declares_secure_direct_dependency_ranges() -> None:
    package = _load_json("package.json")

    assert package["dependencies"]["react-router-dom"] == "^6.30.4"
    assert package["devDependencies"]["vite"] == "^6.4.3"
    assert package["devDependencies"]["vitest"] == "^3.2.7"


def test_frontend_lock_resolves_secure_dependency_floors() -> None:
    lock_packages = _load_json("package-lock.json")["packages"]
    secure_floors = {
        "form-data": "4.0.6",
        "esbuild": "0.25.0",
        "react-router": "6.30.4",
        "react-router-dom": "6.30.4",
        "vite": "6.4.3",
        "vite-node": "2.2.0",
        "vitest": "3.2.7",
    }

    for dependency, floor in secure_floors.items():
        resolved = lock_packages[f"node_modules/{dependency}"]["version"]
        assert _numeric_semver(resolved) >= _numeric_semver(floor), (
            f"{dependency} resolves to {resolved}; expected at least {floor}"
        )
