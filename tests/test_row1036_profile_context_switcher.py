"""Row 1036: Ergonomic Multi-Environment Profile Context Switcher.

Validates:
1. Multi-environment profile management (default, development, staging, production, custom).
2. Persistent profile state storage and atomic profile switching.
3. Ergonomic scoped context switching via `with switcher.temporary_context(name): ...`
   with full environment variable and settings isolation and restoration.
4. Profile cloning, diffing, validation, and JSON serialization.
5. CLI integration via `bdctl profile` subcommands (list, current, switch, show, create, delete).
6. Backward compatibility preserving existing client_config and standard_profile contracts.

RED on baseline: fails with AssertionError (client_config lacks ProfileContextSwitcher).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_red_baseline_capability_probe():
    """Verify baseline lacks Row 1036 ProfileContextSwitcher.

    RED on baseline (e2989f716d52): fails with AssertionError.
    """
    from bulk_downloader import client_config

    assert hasattr(
        client_config, "ProfileContextSwitcher"
    ), "Row 1036 capability missing: ProfileContextSwitcher not implemented in client_config"


def test_client_config_baseline_positive_control():
    """Positive control proving probe distinguishes existing capabilities from missing ones."""
    from bulk_downloader import client_config

    assert hasattr(client_config, "standard_profile")
    assert callable(client_config.standard_profile)
    prof = client_config.standard_profile()
    assert isinstance(prof, dict)
    assert "headers" in prof


def test_profile_creation_and_switching():
    """Verify creating, listing, and switching between operational profiles."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store_path)

        # Initial state should have default profile
        profiles = switcher.list_profiles()
        assert len(profiles) >= 1
        assert switcher.current_profile().name == "default"

        # Create development profile
        dev = EnvironmentProfile(
            name="development",
            description="Local dev environment",
            api_base_url="http://localhost:8080",
            env_vars={"BD_DEV_MODE": "1", "LOG_LEVEL": "DEBUG"},
            settings={"timeout_sec": 5, "retries": 1},
        )
        switcher.create_profile(dev)
        assert len(switcher.list_profiles()) >= 2

        # Switch to development
        active = switcher.switch_profile("development")
        assert active.name == "development"
        assert switcher.current_profile().name == "development"

        # Verify persistence across instances
        switcher2 = ProfileContextSwitcher(storage_path=store_path)
        assert switcher2.current_profile().name == "development"
        assert switcher2.get_profile("development") is not None


def test_temporary_context_manager_isolation_and_restoration():
    """Verify scoped execution inside temporary profile context."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )
    import threading

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store_path)

        staging = EnvironmentProfile(
            name="staging",
            description="Staging cluster",
            api_base_url="https://staging.internal:8443",
            env_vars={"TEST_STAGE_VAR": "stage_active"},
        )
        switcher.create_profile(staging)

        assert switcher.current_profile().name == "default"
        assert "TEST_STAGE_VAR" not in os.environ

        outside_var_name = "BD_PROBE_OUTSIDE_VAR"
        os.environ.pop(outside_var_name, None)

        # Thread non-blocking probe (R2 regression check)
        reader_done = threading.Event()

        def concurrent_reader():
            if len(switcher.list_profiles()) >= 2:
                reader_done.set()

        reader_thread = threading.Thread(target=concurrent_reader)

        with switcher.temporary_context("staging") as ctx:
            assert ctx.name == "staging"
            assert switcher.current_profile().name == "staging"
            assert os.environ.get("TEST_STAGE_VAR") == "stage_active"

            # Set an unrelated environment variable during the block (R1 regression check)
            os.environ[outside_var_name] = "set_by_unrelated_code"

            reader_thread.start()
            reader_ran = reader_done.wait(timeout=5)

        # Outside context:
        # 1. Profile and its specific env vars are restored
        assert switcher.current_profile().name == "default"
        assert "TEST_STAGE_VAR" not in os.environ

        # 2. R1: Unrelated environment variables set during the body SURVIVE and are not destroyed
        assert os.environ.get(outside_var_name) == "set_by_unrelated_code"
        os.environ.pop(outside_var_name, None)

        # 3. R2: Concurrent thread was able to execute while context was active
        assert reader_ran is True
        reader_thread.join()


def test_profile_cloning_and_diffing():
    """Verify cloning a profile with overrides and computing structural diffs."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store_path)

        prod = EnvironmentProfile(
            name="production",
            description="Production cluster",
            api_base_url="https://api.domain.com",
            settings={"concurrency": 16, "rate_limit": 100},
        )
        switcher.create_profile(prod)

        # Clone prod into dr (disaster recovery) with override
        dr = switcher.clone_profile(
            "production",
            "dr-cluster",
            description="DR standby cluster",
            api_base_url="https://api-dr.domain.com",
        )
        assert dr.name == "dr-cluster"
        assert dr.settings["concurrency"] == 16
        assert dr.api_base_url == "https://api-dr.domain.com"

        # Compute diff between production and dr-cluster
        diff = switcher.diff_profiles("production", "dr-cluster")
        assert "api_base_url" in diff["changed"]
        assert diff["changed"]["api_base_url"]["source"] == "https://api.domain.com"
        assert diff["changed"]["api_base_url"]["target"] == "https://api-dr.domain.com"


def test_profile_validation_and_safety_guards():
    """Verify validation: cannot delete active profile or duplicate profile names."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextError,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store_path)

        # Cannot delete active profile
        with pytest.raises(ProfileContextError, match="Cannot delete active profile"):
            switcher.delete_profile("default")

        # Cannot create duplicate profile
        dup = EnvironmentProfile(name="default", description="dup")
        with pytest.raises(ProfileContextError, match="already exists"):
            switcher.create_profile(dup)


def test_profile_import_export():
    """Verify exporting and importing profiles as portable JSON bundles."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "profiles.json"
        export_path = Path(tmpdir) / "exported_bundle.json"
        switcher = ProfileContextSwitcher(storage_path=store_path)

        switcher.create_profile(
            EnvironmentProfile(name="qa", description="QA Env", api_base_url="http://qa:8000")
        )
        switcher.export_profiles(export_path)
        assert export_path.exists()

        # Import into fresh switcher
        fresh_store = Path(tmpdir) / "fresh_profiles.json"
        fresh_switcher = ProfileContextSwitcher(storage_path=fresh_store)
        imported_count = fresh_switcher.import_profiles(export_path)
        assert imported_count >= 1
        assert fresh_switcher.get_profile("qa") is not None


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    """E1: isolate the CLI singleton in a throwaway HOME and store (never the real one).

    The default store sits in the app's config namespace, which the suite's
    home-store guard refuses, so the resolver itself is steered here as well.
    """
    from bulk_downloader import profile_context

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(profile_context, "default_profiles_file", lambda: tmp_path / "profiles.json")
    monkeypatch.setattr(profile_context, "_GLOBAL_SWITCHER", None)
    return tmp_path


def _run_bdctl(home: Path, *argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "bdctl.py"), *argv],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )


def test_bdctl_profile_cli_integration(capsys, profile_home):
    """Verify bdctl profile CLI command integration across all subcommands."""
    import bdctl
    from bulk_downloader.profile_context import get_profile_switcher

    switcher = get_profile_switcher()
    assert switcher.storage_path == profile_home / "profiles.json"

    # 1. Test parser recognizes 'profile list'
    parser = bdctl.build_parser()
    args_list = parser.parse_args(["profile", "list"])
    assert args_list.cmd == "profile"
    assert args_list.profile_action == "list"

    ret = bdctl.cmd_profile(args_list)
    assert ret == 0
    out_list = capsys.readouterr().out
    assert "default" in out_list

    # 2. Test 'profile create'
    args_create = parser.parse_args([
        "profile", "create", "test-env", "--url", "http://test-env:9000", "--desc", "Test description"
    ])
    ret_cr = bdctl.cmd_profile(args_create)
    assert ret_cr == 0
    assert switcher.get_profile("test-env") is not None
    capsys.readouterr()

    # 3. Test 'profile switch'
    args_sw = parser.parse_args(["profile", "switch", "test-env"])
    ret_sw = bdctl.cmd_profile(args_sw)
    assert ret_sw == 0
    assert switcher.current_profile_name == "test-env"
    capsys.readouterr()

    # 4. Test 'profile current'
    args_curr = parser.parse_args(["profile", "current"])
    ret_curr = bdctl.cmd_profile(args_curr)
    assert ret_curr == 0
    out_curr = capsys.readouterr().out
    assert "test-env" in out_curr
    assert "http://test-env:9000" in out_curr

    # 5. Test 'profile show'
    args_show = parser.parse_args(["profile", "show", "test-env"])
    ret_show = bdctl.cmd_profile(args_show)
    assert ret_show == 0
    out_show = capsys.readouterr().out
    data = json.loads(out_show)
    assert data["name"] == "test-env"
    assert data["api_base_url"] == "http://test-env:9000"

    # Switch back to default before deleting test-env
    switcher.switch_profile("default")
    args_del = parser.parse_args(["profile", "delete", "test-env"])
    ret_del = bdctl.cmd_profile(args_del)
    assert ret_del == 0
    assert switcher.get_profile("test-env") is None
    capsys.readouterr()


def test_e1_import_and_missing_store_never_write_home(tmp_path):
    """E1: importing the modules and reading a missing store write nothing under HOME."""
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "import bulk_downloader.client_config, bulk_downloader.profile_context as pc\n"
        "s = pc.get_profile_switcher(); s.list_profiles()\n"
        "print('READ-OK', s.storage_path)\n"
        "if sys.argv[2] == 'write':\n"
        "    s.create_profile(pc.EnvironmentProfile(name='probe', api_base_url='http://p:1'))\n"
    )
    store = tmp_path / ".config" / "bulk-downloader" / "profiles.json"
    env = dict(os.environ, HOME=str(tmp_path))
    ro = subprocess.run([sys.executable, "-c", probe, str(REPO_ROOT), "read"],
                        env=env, capture_output=True, text=True, timeout=60)
    assert ro.returncode == 0, ro.stderr
    assert f"READ-OK {store}" in ro.stdout
    assert not (tmp_path / ".config").exists(), "E1: import/read wrote under HOME"
    # Positive control: the same probe CAN see a write when one is requested.
    rw = subprocess.run([sys.executable, "-c", probe, str(REPO_ROOT), "write"],
                        env=env, capture_output=True, text=True, timeout=60)
    assert rw.returncode == 0, rw.stderr
    assert "probe" in json.loads(store.read_text())["profiles"][-1]["name"]


def test_e2_cli_error_paths_exit_nonzero(tmp_path):
    """E2/M11/M12: failing profile subcommands exit nonzero; list is name-sorted."""
    ok = _run_bdctl(tmp_path, "profile", "list")
    assert ok.returncode == 0, ok.stderr
    assert "default" in ok.stdout
    for argv in (("profile", "switch", "ghost"),
                 ("profile", "show", "ghost"),
                 ("profile", "delete", "default"),
                 ("profile", "create", "default")):
        res = _run_bdctl(tmp_path, *argv)
        assert res.returncode == 1, (argv, res.returncode, res.stdout, res.stderr)
        assert "Error:" in res.stderr, argv
    for name in ("zeta", "alpha"):
        assert _run_bdctl(tmp_path, "profile", "create", name).returncode == 0
    listed = [ln[2:].split()[0] for ln in _run_bdctl(tmp_path, "profile", "list").stdout.splitlines()]
    assert listed == ["alpha", "default", "zeta"]


def test_e2_cli_malformed_store_exits_nonzero_and_keeps_file(tmp_path):
    """E2+E4: a corrupt store is reported with rc 1 and left byte-identical."""
    store = tmp_path / ".config" / "bulk-downloader" / "profiles.json"
    store.parent.mkdir(parents=True)
    store.write_text("{not json")
    res = _run_bdctl(tmp_path, "profile", "list")
    assert res.returncode == 1
    assert "Error:" in res.stderr
    assert store.read_text() == "{not json"


def test_e3_context_does_not_hold_lock_or_persist_temporary_active(tmp_path):
    """E3: other threads run during the body; a save inside it keeps the real active."""
    import threading

    from bulk_downloader.profile_context import EnvironmentProfile, ProfileContextSwitcher

    store = tmp_path / "profiles.json"
    switcher = ProfileContextSwitcher(storage_path=store)
    switcher.create_profile(EnvironmentProfile(name="staging", env_vars={"E3_VAR": "s"}))
    done = threading.Event()

    def writer():
        switcher.create_profile(EnvironmentProfile(name="from-thread"))
        done.set()

    with switcher.temporary_context("staging"):
        t = threading.Thread(target=writer)
        t.start()
        assert done.wait(timeout=5), "E3: lock held across yield blocked another thread"
        t.join()
        assert switcher.current_profile_name == "staging"
        assert json.loads(store.read_text())["active"] == "default"
    assert switcher.current_profile_name == "default"
    assert "E3_VAR" not in os.environ
    assert ProfileContextSwitcher(storage_path=store).get_profile("from-thread") is not None


def test_e4_save_failure_raises_and_rolls_back(tmp_path):
    """E4: a failed persist raises and leaves memory as it was."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextError,
        ProfileContextSwitcher,
    )

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file")
    switcher = ProfileContextSwitcher(storage_path=blocker / "profiles.json")
    with pytest.raises(ProfileContextError, match="Failed to save"):
        switcher.create_profile(EnvironmentProfile(name="lost"))
    assert switcher.get_profile("lost") is None
    with pytest.raises(ProfileContextError, match="Failed to save"):
        switcher.update_profile("default", description="changed")
    assert switcher.get_profile("default").description == "Default local runtime environment"
    # Positive control: the same calls succeed against a writable path.
    ok = ProfileContextSwitcher(storage_path=tmp_path / "ok.json")
    ok.create_profile(EnvironmentProfile(name="kept"))
    assert ProfileContextSwitcher(storage_path=tmp_path / "ok.json").get_profile("kept") is not None


def test_e4_malformed_store_raises_and_is_not_overwritten(tmp_path):
    """E4/M9: corrupt stores raise untouched; a dangling active falls back to default."""
    from bulk_downloader.profile_context import ProfileContextError, ProfileContextSwitcher

    for bad in ("{not json", '{"profiles": [{"name": ""}]}', '{"profiles": "x"}', "[]"):
        store = tmp_path / "bad.json"
        store.write_text(bad)
        with pytest.raises(ProfileContextError):
            ProfileContextSwitcher(storage_path=store)
        assert store.read_text() == bad
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"active": "ghost", "profiles": [
        {"name": "qa", "api_base_url": "http://qa:1"}]}))
    loaded = ProfileContextSwitcher(storage_path=good)
    assert loaded.current_profile_name == "default"
    assert [p.name for p in loaded.list_profiles()] == ["default", "qa"]


def test_e5_update_and_create_validate_fields(tmp_path):
    """E5/M7: unknown, immutable or ill-typed fields are rejected before any change."""
    import dataclasses

    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextError,
        ProfileContextSwitcher,
    )

    assert "read_only" not in {f.name for f in dataclasses.fields(EnvironmentProfile)}
    switcher = ProfileContextSwitcher(storage_path=tmp_path / "p.json")
    switcher.create_profile(EnvironmentProfile(name="qa", api_base_url="http://qa:1"))
    for bad in ({"name": "other"}, {"created_at": 0.0}, {"updated_at": 0.0},
                {"no_such_field": 1}, {"api_base_url": "ftp://qa"},
                {"api_base_url": 5}, {"env_vars": {"K": 1}}, {"tags": "x"},
                {"settings": []}, {"description": None}):
        with pytest.raises(ProfileContextError):
            switcher.update_profile("qa", **bad)
    assert switcher.get_profile("qa").api_base_url == "http://qa:1"
    assert switcher.get_profile("other") is None
    for bad_prof in (EnvironmentProfile(name=""), EnvironmentProfile(name="a b"),
                     EnvironmentProfile(name="x", api_base_url="not-a-url")):
        with pytest.raises(ProfileContextError):
            switcher.create_profile(bad_prof)
    assert [p.name for p in switcher.list_profiles()] == ["default", "qa"]
    # Positive control: a valid update applies.
    assert switcher.update_profile("qa", tags=["t"]).tags == ["t"]


def test_e5_clone_rejects_name_override_and_diff_ignores_timestamps(tmp_path):
    """E5/M6: clone cannot rename via overrides; diff ignores created/updated stamps."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextError,
        ProfileContextSwitcher,
    )

    switcher = ProfileContextSwitcher(storage_path=tmp_path / "p.json")
    switcher.create_profile(EnvironmentProfile(name="src", created_at=1.0, updated_at=1.0))
    with pytest.raises(ProfileContextError):
        switcher.clone_profile("src", "dst", name="elsewhere")
    assert switcher.get_profile("dst") is None and switcher.get_profile("elsewhere") is None
    clone = switcher.clone_profile("src", "dst")
    assert clone.created_at > 1.0
    assert set(switcher.diff_profiles("src", "dst")["changed"]) == {"name"}


def test_e5_import_is_atomic_and_honours_overwrite(tmp_path):
    """E5/M8: one bad entry imports nothing; overwrite decides existing names."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextError,
        ProfileContextSwitcher,
    )

    switcher = ProfileContextSwitcher(storage_path=tmp_path / "p.json")
    switcher.create_profile(EnvironmentProfile(name="qa", description="A"))
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"profiles": [
        {"name": "fresh"}, {"name": "broken", "api_base_url": "nope"}]}))
    with pytest.raises(ProfileContextError):
        switcher.import_profiles(bundle)
    assert switcher.get_profile("fresh") is None
    bundle.write_text(json.dumps({"profiles": [{"name": "fresh"}, {"name": "fresh"}]}))
    with pytest.raises(ProfileContextError, match="duplicate"):
        switcher.import_profiles(bundle)
    bundle.write_text(json.dumps({"profiles": [{"name": "qa", "description": "B"}, {"name": "fresh"}]}))
    assert switcher.import_profiles(bundle) == 1
    assert switcher.get_profile("qa").description == "A"
    assert switcher.import_profiles(bundle, overwrite=True) == 2
    assert switcher.get_profile("qa").description == "B"


def test_effective_context_mutation_resistance():
    """Verify context switcher enforces active profile state and env modification."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store = Path(tmpdir) / "mut_profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store)

        p = EnvironmentProfile(
            name="isolated",
            api_base_url="https://iso.internal",
            env_vars={"MUT_VAR_X": "active_val_99"},
        )
        switcher.create_profile(p)

        # Mutex test on environment
        assert os.environ.get("MUT_VAR_X") is None
        with switcher.temporary_context("isolated") as active_ctx:
            assert active_ctx.name == "isolated"
            assert switcher.current_profile_name == "isolated"
            assert os.environ.get("MUT_VAR_X") == "active_val_99"

        # After block exit
        assert os.environ.get("MUT_VAR_X") is None
        assert switcher.current_profile_name == "default"


def test_profile_update_and_persistence():
    """Verify updating profile fields and persistence."""
    from bulk_downloader.profile_context import (
        EnvironmentProfile,
        ProfileContextSwitcher,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        store = Path(tmpdir) / "upd_profiles.json"
        switcher = ProfileContextSwitcher(storage_path=store)

        prof = EnvironmentProfile(
            name="local-test",
            description="Initial description",
            api_base_url="http://localhost:5000",
        )
        switcher.create_profile(prof)

        # Update description and api_base_url
        updated = switcher.update_profile(
            "local-test",
            description="Updated description",
            api_base_url="http://localhost:5001",
            settings={"timeout": 15},
        )
        assert updated.description == "Updated description"
        assert updated.api_base_url == "http://localhost:5001"
        assert updated.settings["timeout"] == 15

        # Reload from storage to verify persistence
        fresh_switcher = ProfileContextSwitcher(storage_path=store)
        reloaded = fresh_switcher.get_profile("local-test")
        assert reloaded is not None
        assert reloaded.description == "Updated description"
        assert reloaded.api_base_url == "http://localhost:5001"
        assert reloaded.settings["timeout"] == 15


