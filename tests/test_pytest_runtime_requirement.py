from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pytest_is_installed_by_core_requirements() -> None:
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    declared = {
        line.strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "pytest>=7.0,<9.0" in declared, (
        "requirements.txt must install real pytest so capture and validation "
        "use pytest rather than depending on the custom fallback runner"
    )
    assert "pytest-xdist>=3.6,<4.0" in declared, (
        "requirements.txt must install pytest-xdist so capture --workers "
        "continues to provide real parallel pytest execution"
    )
