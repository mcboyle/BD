"""IA-01: the eaf_base_api pin must keep the EchterAlsFake extractors importable.

`eaf_base_api>=2.5` was unbounded. A fresh install resolved 4.2.0, and every
release from 3.3.3 on dropped ``base_api.base.setup_logger``, which phub,
xvideos_api, xhamster_api, spankbang_api, eporner_api, hqporner_api,
youporn_api, porntrex_api, missav_api, xfreehd_api and porngo_api all import.
`pip check` stays clean (the consumers declare an unversioned
`eaf-base-api`), and extractors._try_import logs the ImportError at INFO as
"not installed", so the whole library-extractor tier went dark silently.
Bisected on spare8 against the resolved consumer versions: 3.2 .. 3.3.2
import 11/11, 3.3.3 .. 4.2.0 import 0/11, 3.1 and older miss modules.
"""
import importlib
import importlib.util
import re
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]


def _eaf_requirement() -> Requirement:
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if re.match(r"eaf[-_]base[-_]api\b", line, re.I):
            return Requirement(line)
    raise AssertionError("requirements.txt no longer lists eaf_base_api")


@pytest.mark.parametrize("version", ["3.3.3", "3.3.5", "4.0.0", "4.2.0"])
def test_pin_refuses_releases_without_setup_logger(version):
    req = _eaf_requirement()
    assert not req.specifier.contains(version), (
        f"requirements.txt admits eaf_base_api {version} ({req}); that release "
        "has no base_api.base.setup_logger and breaks every EAF extractor")


@pytest.mark.parametrize("version", ["3.2", "3.3.2"])
def test_pin_admits_the_releases_the_extractors_import_with(version):
    req = _eaf_requirement()
    assert req.specifier.contains(version), (
        f"requirements.txt refuses eaf_base_api {version} ({req}), a release "
        "every EAF extractor imports cleanly with")


def test_installed_extractors_import_when_the_env_satisfies_the_pin():
    try:
        installed = metadata.version("eaf_base_api")
    except metadata.PackageNotFoundError:
        pytest.skip("eaf_base_api not installed in this environment")
    req = _eaf_requirement()
    if not req.specifier.contains(installed):
        pytest.skip(f"environment not converged to requirements.txt: "
                    f"eaf_base_api {installed} is outside {req.specifier}")

    from bulk_downloader import extractors

    modules = sorted({entry[0] for entry in extractors._REGISTRY.values()})
    imported, broken = [], []
    for name in modules:
        if importlib.util.find_spec(name) is None:
            continue  # not installed (xnxx_api/beeg_api are deliberately unlisted)
        try:
            importlib.import_module(name)
        except Exception as exc:  # the broken shape is an ImportError at import
            broken.append(f"{name}: {type(exc).__name__}: {exc}")
        else:
            imported.append(name)
    assert not broken, (
        f"eaf_base_api {installed} satisfies {req} yet these extractors "
        f"cannot import: {broken}")
    assert imported, "no EAF extractor is installed; nothing was checked"
