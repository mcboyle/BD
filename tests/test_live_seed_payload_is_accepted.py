"""The seeder's payload must survive BD's REAL validator, not a fake client.

`tests/test_live_seed.py` drives the seeder through a `FakeClient` that records
requests and validates nothing. That is the right instrument for asking "what
did the seeder try to do", and it is why the existing suite has 20-odd green
tests over a tool that has never once succeeded on the box:

    live_seed: REFUSED - could not create the fixture login site
    (response: {'error': "cookie_file must be an absolute path
     (got 'cookies/bdseed_fixture.json')"})
        -- capture 2026-07-27, 05a_live_seed.log, on test4

The request SHAPE was never in the fake client's denominator. It accepted a
payload BD rejects with a 400, so every assertion downstream of it was true and
useless. A gate that cannot see the thing it is asked about reports OK.

This file closes that specific hole and nothing else: it takes the payloads the
seeder actually builds and runs them through `bulk_downloader.app`'s own
path validator -- the same function `_create_site` calls -- so a payload BD
would refuse fails here instead of on the box fifteen minutes into a capture.

Deliberately NOT asserted here: that the site is created end to end. That needs
a live app, a vault, and a fixture origin, none of which belong in a unit test.
The defect this catches is a validation contract mismatch, and the contract is
checkable without any of that.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"


def _load_seeder():
    loader = importlib.machinery.SourceFileLoader("bd_live_seed_payload", str(SEED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def app_module():
    """BD's real app, for its real validator.

    Skipped rather than failed if the app cannot import: an environment without
    the app is a fact about the environment, and reporting it as a seeder defect
    would be a false accusation. But note the skip is narrow -- it does not
    cover the assertions below, it replaces them, and a run that skips has
    proven nothing about the payload.
    """
    try:
        from bulk_downloader import app as bd_app
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"bulk_downloader.app did not import: {exc}")
    return bd_app


def _payload_builders(seeder):
    """Every seeder function that produces a site-creation body.

    Derived from the module rather than listed, so a new builder is covered the
    day it is added instead of the day someone remembers this file.
    """
    builders = []
    for name in dir(seeder):
        if not name.endswith("_site_config"):
            continue
        fn = getattr(seeder, name)
        if callable(fn):
            builders.append((name, fn))
    return builders


def test_there_is_at_least_one_payload_to_check():
    """Denominator canary.

    If the seeder's builders are renamed, the parametrised test below would
    silently run zero cases and report success over nothing -- the exact shape
    of the bug this file exists to catch.
    """
    seeder = _load_seeder()
    builders = _payload_builders(seeder)
    assert builders, (
        "no *_site_config builders found in tools/live_seed.py. Either they "
        "were renamed -- update the predicate here in the same cut -- or the "
        "seeder no longer creates sites, in which case this file should go."
    )


def test_every_seeder_site_payload_passes_bd_path_validation(app_module):
    """The one that fails today.

    `_validate_config_paths` is what `_create_site` calls (app.py:4111), so a
    payload that fails here is a payload the running service refuses.
    """
    seeder = _load_seeder()
    failures = []
    for name, build in _payload_builders(seeder):
        try:
            cfg = build()
        except Exception as exc:
            failures.append(f"{name}() raised {type(exc).__name__}: {exc}")
            continue
        ok, message = app_module._validate_config_paths(cfg)
        if not ok:
            offending = {
                k: v for k, v in cfg.items()
                if isinstance(v, str) and ("file" in k or "dir" in k or "path" in k)
            }
            failures.append(f"{name}() -> BD refuses: {message}\n      path fields: {offending}")

    assert not failures, (
        "the seeder builds a site payload BD will reject:\n  "
        + "\n  ".join(failures)
        + "\n\nBD accepts an EMPTY path field and fills it in itself: "
          "app.py:3941 returns early on empty, and _save_sites_config "
          "(app.py:1197-1218) derives the absolute path from the SERVICE's "
          "BD_HOME. The seeder is an HTTP client -- its BD_HOME and cwd are "
          "not necessarily the service's -- so any absolute path it computes "
          "is a second, unverifiable source of truth. Send empty, do not "
          "compute one."
    )


def test_the_seeder_does_not_compute_its_own_absolute_cookie_path(app_module):
    """Guards the fix against being 'corrected' in the wrong direction.

    Hardcoding an absolute path would make the test above pass while
    reintroducing the real defect: a path derived from the wrong machine.
    """
    seeder = _load_seeder()
    for name, build in _payload_builders(seeder):
        try:
            cfg = build()
        except Exception:
            continue
        raw = (cfg.get("cookie_file") or "").strip()
        assert not raw, (
            f"{name}() sets cookie_file={raw!r}. Leave it empty: BD derives it "
            f"from the service's own BD_HOME. A path computed by the seeder is "
            f"correct only when the seeder and the service share a filesystem "
            f"and a BD_HOME, which --base-url makes an assumption, not a fact."
        )
