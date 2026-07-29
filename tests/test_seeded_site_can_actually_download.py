"""A site with no download_dir clicks the link and discards the file.

THE DEFECT, measured on the box 2026-07-29. Both seeded URLs reached a terminal
state and neither produced a file:

    "status": "done", "message": "Clicked (no dl dir)", "filename": ""

runner.py:3618-3623 is the branch responsible. With no download_dir configured
it clicks the download link, sleeps, marks the job `done`, and writes a history
row of size 0. It never fetches anything.

The consequence is that two live checks cannot clear no matter how well BD
works, and both reported exactly that:

    WARN  L12  2 completed download(s) but none via the HLS path
    WARN  L14  2 completed download(s) but none recorded as dedup-skipped

Neither is a statement about BD's download pipeline. They are statements about a
site that was never configured to keep what it downloaded.

WHY THE SEEDER CANNOT SIMPLY NAME A PATH. tools/live_seed.py is an HTTP client;
`--base-url` may address a service on another machine with a different BD_HOME
and cwd, so any path computed inside the seeder is a guess about somebody else's
filesystem. queue_site_config()'s docstring makes that argument and it is
correct. The resolution therefore belongs to the service, which knows its own
filesystem -- exactly where cookie_file is already resolved (app.py:1197-1218
fills BD_HOME/cookies/<site_id>.json when the field is blank).

WHAT THIS CHANGES. _oi_default_download_dir() previously returned None when
neither BD_DOWNLOAD_DIR nor a global config download_dir was set, and nothing
filled a blank per-site download_dir at all. Now it falls back to ~/Downloads,
and _save_sites_config fills a blank per-site value from it, symmetrically with
cookie_file.

BLAST RADIUS, stated plainly: an operator site whose download_dir is blank
currently clicks-and-discards and will, after this, download into the global
default. That is the intended repair -- a site that silently discards downloads
is a footgun, not a feature -- but it is a real behaviour change to a running
deployment and not confined to the seeded site.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"


@pytest.fixture()
def app_mod():
    try:
        from bulk_downloader import app
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"bulk_downloader.app did not import, so this gate cannot "
                    f"verify its subject: {exc}")
    return app


@pytest.fixture()
def clean_env(monkeypatch):
    """No BD_DOWNLOAD_DIR, so the fallback chain is what is under test."""
    monkeypatch.delenv("BD_DOWNLOAD_DIR", raising=False)
    return monkeypatch


def _load_seeder():
    loader = importlib.machinery.SourceFileLoader("bd_live_seed_dl", str(SEED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ── denominator canary ───────────────────────────────────────────────────────

def test_the_runner_still_has_a_no_download_dir_branch():
    """If this branch is gone, the defect below is about nothing.

    The whole point of the fix is to stop reaching it. Pinning its existence
    keeps this file honest: were the branch removed, these tests would pass for
    a reason unrelated to what they claim to check.
    """
    runner_src = (REPO_ROOT / "bulk_downloader" / "runner.py").read_text(
        encoding="utf-8")
    assert "Clicked (no dl dir)" in runner_src, (
        "runner.py no longer has the no-download-dir branch. That may be a "
        "better fix than this one -- but these tests were written against it, "
        "and they must be re-derived rather than left passing vacuously."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_default_download_dir_always_resolves(app_mod, clean_env):
    """With nothing configured the app must still name a directory."""
    resolved = app_mod._oi_default_download_dir()
    assert resolved, (
        "_oi_default_download_dir() returned nothing with no BD_DOWNLOAD_DIR "
        "and no global config value. A site created in that state gets a blank "
        "download_dir, and every download it completes is clicked and "
        "discarded -- terminal `done`, zero bytes, no filename."
    )


def test_the_default_is_an_expanded_absolute_path(app_mod, clean_env):
    """A literal '~' is not a path; it is a string the OS will not resolve."""
    resolved = str(app_mod._oi_default_download_dir())
    assert "~" not in resolved, (
        f"the default download dir was not expanded: {resolved!r}. "
        f"Path('~/Downloads') creates a directory literally named '~'."
    )
    assert os.path.isabs(resolved), (
        f"the default download dir is not absolute: {resolved!r}"
    )


def test_an_explicit_setting_still_wins_over_the_default(app_mod, monkeypatch):
    """The fallback must not override what the operator actually configured."""
    monkeypatch.setenv("BD_DOWNLOAD_DIR", "/tmp/bd_explicit_choice")
    assert str(app_mod._oi_default_download_dir()) == "/tmp/bd_explicit_choice", (
        "BD_DOWNLOAD_DIR no longer takes precedence; the fallback is "
        "overriding an explicit operator setting."
    )


def test_a_blank_download_dir_stays_blank_in_the_config(app_mod, clean_env,
                                                        monkeypatch, tmp_path):
    """The constraint the band found, pinned here so it cannot be re-broken.

    The first version of this fix auto-filled download_dir in
    _save_sites_config, symmetrically with cookie_file. The band caught it:
    tests/test_v3_66_273_gcw_download_gate.py asserts that a site created with
    no download_dir keeps an empty one, because a BLANK value is load-bearing
    state -- /api/captures/setup_site leaves it empty so the operator can choose
    later, and the GCW-4 gate blocks promote precisely while it is empty.

    Filling it would have made that gate structurally unable to see the state it
    exists to catch: fixing one section 0 defect by introducing another. This
    test states the constraint in the file that violated it, so the same fix is
    not re-attempted from this direction.
    """
    monkeypatch.setattr(app_mod, "SITES_FILE", tmp_path / "sites_config.json")
    cfg = {"seedsite": {"name": "bdseed fixture site"}}
    monkeypatch.setattr(app_mod, "s_cfg", cfg, raising=False)
    app_mod._save_sites_config()
    assert not str((cfg.get("seedsite") or {}).get("download_dir", "")).strip(), (
        "_save_sites_config() filled a blank download_dir. That erases the "
        "'operator has not chosen yet' state the GCW-4 promote gate reads, so "
        "the gate can never fire. Resolve the default where the file is "
        "written (runner.py), not where the config is stored."
    )


def test_the_runner_resolves_a_blank_download_dir_to_the_default():
    """The fix's actual subject: the file must land somewhere.

    The download path falls back to the deployment default before reaching the
    click-and-discard branch, so a site that never chose a directory still keeps
    what it downloads.
    """
    runner_src = (REPO_ROOT / "bulk_downloader" / "runner.py").read_text(
        encoding="utf-8")
    marker = 'self.config.get("download_dir","").strip()'
    assert marker in runner_src, (
        "runner.py's download-path read of download_dir has moved; this gate "
        "can no longer locate its subject"
    )
    after = runner_src.split(marker, 1)[1][:1600]
    assert "_oi_default_download_dir" in after, (
        "the runner's download path does not fall back to the deployment "
        "default when the site has no download_dir, so it reaches the "
        "click-and-discard branch and the file is lost."
    )


def test_the_discard_branch_is_now_the_last_resort():
    """It must be reached only when no default resolves, not by default.

    Reads CODE lines only. The first version split on the marker string and so
    matched the explanatory comment above the fix -- which quotes it -- rather
    than the branch itself, and reported a defect that was not there. Prose that
    names the subject is not the subject.
    """
    lines = (REPO_ROOT / "bulk_downloader" / "runner.py").read_text(
        encoding="utf-8").splitlines()
    code = [(i, ln) for i, ln in enumerate(lines)
            if not ln.strip().startswith("#")]
    discard = [i for i, ln in code if '_update_job(url,"done","Clicked (no dl dir)")' in ln]
    assert discard, (
        "could not locate the click-and-discard call in runner.py; this gate "
        "cannot verify its subject"
    )
    resolver = [i for i, ln in code if "_oi_default_download_dir" in ln]
    assert resolver and min(resolver) < min(discard), (
        f"the click-and-discard branch (line {min(discard) + 1}) is reachable "
        f"without first trying the deployment default "
        f"(resolver at {[r + 1 for r in resolver] or 'nowhere'})."
    )


# ── the seeder must still not guess somebody else's filesystem ───────────────

def test_the_seeder_does_not_hardcode_a_download_path():
    """Resolution belongs to the service, which knows its own filesystem.

    The seeder is an HTTP client and --base-url may name another machine. If it
    ever starts naming a concrete directory, that reasoning has been lost and
    the path is a guess.
    """
    seeder = _load_seeder()
    cfg = seeder.queue_site_config()
    assert "download_dir" not in cfg, (
        f"queue_site_config() now names a download_dir ({cfg.get('download_dir')!r}). "
        f"The seeder cannot know the service's filesystem -- --base-url may "
        f"point at another machine. Let the service fill it."
    )
