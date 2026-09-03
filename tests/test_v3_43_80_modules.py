"""Integration test for v3.43.80 modules.

For each new module shipped this session, this test:
  • Verifies it imports cleanly inside the real BD test environment
  • Calls its public functions with realistic inputs
  • Checks return shapes match what callers expect
  • Asserts that any new DB tables are created idempotently

This is the cross-cutting test that catches "module assumes a column
that doesn't exist" and similar integration bugs that pure ast.parse
+ isolated smoke tests miss.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


# v3.66.6: autouse fixture that saves+restores env + sys.modules across
# every test. _fresh_install() below mutates os.environ["BD_INSTALL_DIR"]
# and purges bulk_downloader.* from sys.modules; without restoration
# those mutations leak into downstream test files (silent killer on
# nproc=1 / --workers=1 runs). Same defect class as the v3.66.6 csrf
# poison fix.
#
# v3.66.8 (item #5 of v3.66.7 backlog): added cwd save/restore. The
# real bug behind the in-isolation flakes was NOT a fixture-snapshot
# timing issue (that diagnosis was based on the v3.66.6 csrf precedent
# and turned out not to apply here). The actual cause: bulk_downloader
# resolves its sqlite DB via `constants.DB_PATH` as a RELATIVE path,
# so the DB lives at `${cwd}/downloader_history.db`. The source tree
# ships with a populated `downloader_history.db` at the repo root, and
# the test runner runs from there. _fresh_install() set BD_INSTALL_DIR
# but never chdir'd, so every test still opened the repo-root DB and
# saw 1446+ pre-existing 'done' rows on top of its seed — counts blew
# up by exactly that amount. The fix is two-part: _fresh_install()
# now chdir's into the tempdir (real fix), and this fixture restores
# the original cwd in teardown so the chdir doesn't leak to other
# tests.
@pytest.fixture(autouse=True)
def _restore_env_and_modules():
    saved_env = {k: os.environ.get(k) for k in (
        "BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")}
    saved_mods = {m: sys.modules[m] for m in list(sys.modules)
                  if m.startswith("bulk_downloader")}
    saved_cwd = os.getcwd()
    try:
        yield
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Purge any new bulk_downloader.* entries added by the test,
        # then re-stamp the originals. Tests left with stale module
        # instances pointing at a defunct BD_INSTALL_DIR are the leak.
        for m in list(sys.modules):
            if m.startswith("bulk_downloader") and m not in saved_mods:
                del sys.modules[m]
        sys.modules.update(saved_mods)
        # Restore cwd. The tempdir we chdir'd into may have been
        # deleted by tempfile cleanup; tolerate that.
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass


# ─── Test scaffolding ─────────────────────────────────────────────────

def _fresh_install():
    """Each test gets a clean BD install dir + reloaded modules.

    v3.66.8: cwd is also moved into the tempdir. bulk_downloader's
    DB_PATH is a relative path, so without the chdir every test
    opens the repo-root downloader_history.db and sees its pre-
    existing rows.
    """
    tmpdir = tempfile.mkdtemp(prefix="bd-v3438-")
    os.environ["BD_INSTALL_DIR"] = tmpdir
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    os.chdir(tmpdir)
    saved_modules = {k: v for k, v in sys.modules.items()
                     if k.startswith("bulk_downloader")}
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    return tmpdir
    for _bd_mod in [_m for _m in list(sys.modules)
                    if _m.startswith("bulk_downloader")]:
        del sys.modules[_bd_mod]
    sys.modules.update(saved_modules)


def _seed_history(*, dones=10, fails=2, sites=("vixen", "blacked")):
    from bulk_downloader.db import db_init, db_log
    db_init()
    for sid in sites:
        for i in range(dones):
            db_log(sid, sid.title(), f"https://{sid}/v/{i}", "done",
                   filename=f"/dl/{sid}{i}.mp4",
                   file_size=1_000_000_000)
        for i in range(fails):
            db_log(sid, sid.title(), f"https://{sid}/v/f{i}", "failed",
                   message=f"HTTP 429 rate limit retry")


# ─── Per-module imports + basic-surface tests ─────────────────────────

# Every exception text below is a VERBATIM string measured from a real host,
# not an invention. `Bad display name ""` and `Can't connect to display ":0"`
# were read on 2026-09-01 from 10.0.70.54 (hostname `test`, the box whose gw10
# worker produced the row-611 failure), pystray 0.19.5, DISPLAY unset and
# DISPLAY=:0 respectively. tools/verify_release.py:_HARNESS_SIGNATURES already
# carries the same two literals.
_MEASURED_HEADLESS_ERRORS = (
    'Bad display name ""',
    'Can\'t connect to display ":0"',
)


def _import_tray_app_with_a_broken_pystray(tmp_root, error_line):
    """Import bulk_downloader.tray_app in a SUBPROCESS whose `pystray` is a
    stub that raises a NON-ImportError at import time.

    Runs out of process on purpose: `pystray` may be genuinely installed (or
    genuinely absent) in the ambient venv, and either way this test must
    measure tray_app's GUARD rather than the host's package set. The stub
    directory is prepended to PYTHONPATH so it wins over any real pystray.

    Returns (returncode, stdout, stderr).
    """
    stub_dir = os.path.join(tmp_root, "stub")
    os.makedirs(stub_dir, exist_ok=True)
    with open(os.path.join(stub_dir, "pystray.py"), "w", encoding="utf-8") as fh:
        fh.write(
            "class _StubDisplayNameError(Exception):\n"
            "    pass\n"
            "\n"
            "raise _StubDisplayNameError(%r)\n" % (error_line,)
        )
    repo_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["PYTHONPATH"] = os.pathsep.join([stub_dir, repo_root])
    probe = (
        "import sys\n"
        # PHASE 1 -- prove the seam. The stub must really be the module that
        # `import pystray` resolves to, and it must really raise something
        # that is NOT an ImportError. Without this the phase-2 verdict could
        # be manufactured by a stub that never loaded.
        "try:\n"
        "    import pystray\n"
        "except ImportError as exc:\n"
        "    print('SEAM=WRONG-ImportError:' + type(exc).__name__)\n"
        "    sys.exit(3)\n"
        "except Exception as exc:\n"
        "    print('SEAM=OK:' + type(exc).__name__ + ':' + str(exc))\n"
        "else:\n"
        "    print('SEAM=WRONG-imported-cleanly')\n"
        "    sys.exit(4)\n"
        "sys.modules.pop('pystray', None)\n"
        # PHASE 2 -- the subject under test.
        "import bulk_downloader.tray_app as tray\n"
        "print('IMPORTED=OK')\n"
        "print('AVAILABLE=' + repr(tray.is_available()))\n"
        "print('REASON=' + repr(getattr(tray, 'unavailable_reason', lambda: None)()))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_root, env=env, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


class TestOptionalTrayImportIsHeadlessSafe:
    """An optional dependency that is PRESENT but UNUSABLE must not break the
    import of the module that wraps it.

    tray_app's own docstring states the contract: "Both are optional deps. If
    missing, this module reports unavailable and the tray is silently skipped
    -- BD's web UI still works."  The guard implemented only half of it. It
    caught ImportError, but on a headless Linux box with pystray INSTALLED the
    import raises Xlib.error.DisplayNameError, which is an Exception and not
    an ImportError, so it escaped and `import bulk_downloader.tray_app` died.

    That is what row 611 recorded as "schedule sensitivity". It was not.
    Measured 2026-09-01: `pystray` was in requirements-OPTIONAL.txt (merged into
    requirements.txt 2026-09-03) and is
    installed on 6 of the 12 fleet hosts and absent on the rest. The failing
    band ran on 10.0.70.54 (pystray 0.19.5, DISPLAY unset) where a bare
    `import pystray` raises with no pytest, no xdist and no neighbouring test
    file involved; the "passed serially in isolation" sample was taken on a
    host with no pystray at all, where the guard's ImportError arm swallows
    everything. Same tree, different ENVIRONMENT -- never a matched
    comparison. The recorded assignment settles the other half: in
    /tmp/bd-runctx/945228/gw10.chain, tests/test_v3_43_80_modules.py is
    position 1 of 12, so no co-scheduled file preceded it on that worker.
    """

    def test_tray_app_imports_when_pystray_raises_a_non_import_error(self):
        tmp_root = tempfile.mkdtemp(prefix="bd-611-tray-")
        rc, out, err = _import_tray_app_with_a_broken_pystray(
            tmp_root, 'Bad display name ""')
        # Seam first: the stub was loaded and raised a non-ImportError.
        assert "SEAM=OK:_StubDisplayNameError:" in out, (
            f"the stub pystray never took effect; rc={rc}\n"
            f"stdout:\n{out}\nstderr:\n{err}")
        assert "SEAM=WRONG" not in out, out
        # Verdict: the wrapper module still imports, and reports unavailable.
        assert rc == 0, (
            "bulk_downloader.tray_app did not survive an optional dependency "
            "that is installed but unusable -- the exact shape that failed "
            "the row-611 band on a headless host with pystray installed.\n"
            f"rc={rc}\nstdout:\n{out}\nstderr:\n{err}")
        assert "IMPORTED=OK" in out, f"stdout:\n{out}\nstderr:\n{err}"
        assert "AVAILABLE=False" in out, (
            "tray_app imported but claimed the tray was usable\n"
            f"stdout:\n{out}")
        # The reason must carry the dependency's OWN words, not a collapsed
        # "unavailable" that cannot be acted on.
        assert "Bad display name" in out, (
            "tray_app swallowed the reason it is unavailable\n"
            f"stdout:\n{out}")

    def test_the_measured_headless_texts_are_both_covered(self):
        """Both real headless shapes, not just the one in the failing log."""
        for error_line in _MEASURED_HEADLESS_ERRORS:
            tmp_root = tempfile.mkdtemp(prefix="bd-611-tray-")
            rc, out, err = _import_tray_app_with_a_broken_pystray(
                tmp_root, error_line)
            assert "SEAM=OK:_StubDisplayNameError:" in out, (error_line, out, err)
            assert rc == 0 and "IMPORTED=OK" in out, (
                f"{error_line!r} still broke the import\n"
                f"rc={rc}\nstdout:\n{out}\nstderr:\n{err}")
            assert "AVAILABLE=False" in out, (error_line, out)

    def test_a_missing_optional_dependency_is_still_reported_unavailable(self):
        """Control: the ORIGINAL ImportError arm must keep working, so the fix
        widens the guard rather than replacing it."""
        tmp_root = tempfile.mkdtemp(prefix="bd-611-tray-")
        stub_dir = os.path.join(tmp_root, "stub")
        os.makedirs(stub_dir, exist_ok=True)
        with open(os.path.join(stub_dir, "pystray.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("raise ImportError('No module named pystray')\n")
        repo_root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        env["PYTHONPATH"] = os.pathsep.join([stub_dir, repo_root])
        proc = subprocess.run(
            [sys.executable, "-c",
             "import bulk_downloader.tray_app as tray\n"
             "print('AVAILABLE=' + repr(tray.is_available()))\n"],
            cwd=tmp_root, env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert "AVAILABLE=False" in proc.stdout, proc.stdout


def _import_failures(modules):
    """Import each ``bulk_downloader.<name>``; return (attempted, failures).

    ``attempted`` exists so the caller can reconcile COLLECTION to EXECUTION:
    an empty or short module list would otherwise report a clean run while
    measuring nothing.

    Each failure records the exception TYPE as well as its text. A bare
    ``{e}`` reduced ``Xlib.error.DisplayNameError: Bad display name ""`` to
    ``Bad display name ""`` -- which named a symptom the reader had to guess
    at, and which is exactly the collapsed diagnostic that sent row 611 after
    a scheduling ghost instead of an optional dependency.
    """
    failures = []
    attempted = 0
    for m in modules:
        attempted += 1
        try:
            __import__(f"bulk_downloader.{m}", fromlist=[m])
        except Exception as e:
            failures.append(f"{m}: {type(e).__name__}: {e}")
    return attempted, failures


def _display_identity():
    """The display-environment facts an import failure here depends on."""
    return (f"DISPLAY={os.environ.get('DISPLAY', '<unset>')!r} "
            f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '<unset>')!r} "
            f"platform={sys.platform!r} executable={sys.executable!r}")


class TestImports:
    """Verify every new module imports without error."""

    def test_the_import_loop_still_fails_for_an_unimportable_module(self):
        """NEGATIVE CONTROL for this file's central gate.

        Row 611 is resolved by widening a guard in bulk_downloader/tray_app.py.
        A guard widened in production must not quietly widen the TEST: prove
        the loop this gate is built on still reports a module that genuinely
        cannot import, and still distinguishes it from one that can.
        """
        _fresh_install()
        attempted, failures = _import_failures(
            ["bw_chart", "no_such_module_row611_negative_control"])
        assert attempted == 2, attempted
        assert len(failures) == 1, (
            "the control expected exactly one failure; a real module failing "
            f"here would launder the result: {failures}")
        assert failures[0].startswith(
            "no_such_module_row611_negative_control: "), failures[0]
        assert "ModuleNotFoundError" in failures[0], failures[0]
        # The verdict the real test reaches is `assert not failures`, so a
        # non-empty list is a RED gate by construction.
        assert failures, "the gate cannot go red"

    def test_all_modules_import(self):
        _fresh_install()
        modules = [
            "range_stream", "friendly_error", "ytdlp_updater",
            "tpdb", "subtitles", "library_final",
            "saved_searches", "tag_inference", "recommendations",
            "capacity", "ramdisk_stage", "circuit_breaker",
            "account_health", "cost_economics", "policy_gates",
            "provenance", "bitrot", "wayback_cdx", "enrichment",
            "openapi_spec", "bg_scheduler", "knowledge",
            "selector_playground", "scheduling", "tray_app",
            "cli_dashboard", "marketplace", "accessibility",
            "plugins", "diagnostics_bundle", "edge_deploy",
            "metrics_prom", "webhooks", "healthcheck", "batch_ops",
            "exports", "cleanup_helpers", "gamification",
            "eol_export", "storage_rebalance", "cookie_quality",
            "federation", "backup_verify", "cookie_relogin",
            "stream_relay", "thumbnail_sheets", "discovery",
            "shares", "command_palette", "smart_wakeup",
            "synthetic_tests", "vpn_stats", "i18n", "cluster_rate",
            "scheduled_exports", "alerts_engine", "error_fingerprint",
            "queue_priority", "site_weather", "bandwidth_shape",
            "shortcuts", "maintenance", "timeline",
            "cookie_clipboard", "migrations", "bw_chart",
        ]
        from bulk_downloader.db import db_init
        db_init()
        # Nonzero denominator, then collection reconciled to execution.
        assert modules, "the module denominator is empty; this gate measures nothing"
        attempted, failures = _import_failures(modules)
        assert attempted == len(modules), (
            f"attempted {attempted} of {len(modules)} modules")
        # The first line keeps the historical "Module import failures: [...]"
        # shape that tools/verify_release.py:_HARNESS_SIGNATURES scans for.
        # The lines after it say WHERE the measurement was taken, because an
        # import that reaches a GUI toolkit depends on the process's display
        # environment and on which OPTIONAL packages this host installed --
        # neither of which the bare list ever showed.
        assert not failures, (
            f"Module import failures: {failures}\n"
            f"environment at import time: {_display_identity()}\n"
            "If a failure above names a display (e.g. Bad display name, "
            "Can't connect to display, Namespace Gtk not available) it is "
            "about THIS HOST's environment and its optional packages -- see "
            "requirements.txt and tools/verify_release.py's "
            "_HARNESS_SIGNATURES -- and NOT about a neighbouring test file.")


# ─── Marketplace round-trip ───────────────────────────────────────────

class TestMarketplace:
    def test_export_import_round_trip(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import marketplace as mp
        site_cfg = {
            "name": "Vixen",
            "template": "vixen",
            "password": "SHOULD_BE_REDACTED",
            "cookies": ["x", "y"],
            "download_dir": "/old/path",
            "min_resolution": 1080,
            "learned": {"login": ".btn"},
        }
        bundle = mp.export_template("vixen", site_cfg, sign_with="secret")
        assert "password" not in bundle["template"]
        assert "cookies" not in bundle["template"]
        assert bundle["template"]["download_dir"] == ""
        assert bundle["template"]["min_resolution"] == 1080
        # Round-trip
        data = mp.bundle_to_bytes(bundle)
        decoded = mp.bundle_from_bytes(data)
        result = mp.import_template(decoded, verify_with="secret",
                                     target_site_id="vixen-prod")
        assert result["ok"]
        assert result["site_id"] == "vixen-prod"
        # Wrong secret rejected
        bad = mp.import_template(decoded, verify_with="wrong")
        assert not bad["ok"]


# ─── Accessibility ────────────────────────────────────────────────────

class TestAccessibility:
    def test_plain_language(self):
        _fresh_install()
        from bulk_downloader import accessibility as a11y
        msg = a11y.plain_language("ConnectionError: HTTPSConnectionPool")
        assert "server" in msg.lower() or "try again" in msg.lower()

    def test_contrast_wcag_pass_fail(self):
        _fresh_install()
        from bulk_downloader import accessibility as a11y
        good = a11y.contrast_ratio("#ffffff", "#000000")
        assert good["ratio"] > 20
        assert good["passes_aa_normal"]
        bad = a11y.contrast_ratio("#888888", "#999999")
        assert bad["ratio"] < 2
        assert not bad["passes_aa_normal"]

    def test_aria_audit_catches_issues(self):
        _fresh_install()
        from bulk_downloader import accessibility as a11y
        audit = a11y.aria_audit(
            '<input type="text" placeholder="name">'
            '<button><svg/></button>'
            '<a href="#"></a>'
            '<img src="x">'
        )
        kinds = set(audit["stats"]["by_kind"].keys())
        assert "input_unlabeled" in kinds
        assert "icon_button_unlabeled" in kinds


# ─── Plugins ──────────────────────────────────────────────────────────

class TestPlugins:
    def test_extractor_and_hook_registration(self):
        _fresh_install()
        from bulk_downloader import plugins as pl
        pl.reset()

        @pl.extractor("test-site")
        def my_extractor(url, ctx):
            return {"video_url": url + "?direct"}

        fired = []

        @pl.hook("download.done")
        def my_hook(payload):
            fired.append(payload)

        fn = pl.get_extractor("test-site")
        assert fn is not None
        result = fn("http://x.com/v", {})
        assert result["video_url"] == "http://x.com/v?direct"
        pl.fire_hook("download.done", {"url": "x"})
        assert len(fired) == 1


# ─── Diagnostics bundle redaction ─────────────────────────────────────

class TestDiagnostics:
    def test_redacts_secrets(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import diagnostics_bundle as db_mod
        s_cfg = {"vixen": {
            "name": "V",
            "password": "secret123",
            "api_key": "abc",
            "cookies": ["c1", "c2"],
            "download_dir": "/very/long/host/specific/path/downloads",
            "min_resolution": 1080,  # non-sensitive — should remain
        }}
        snap = db_mod.bundle(s_cfg=s_cfg)
        cfg = snap["sites"]["vixen"]
        assert cfg["password"] == "<redacted>"
        assert cfg["api_key"] == "<redacted>"
        assert "/very/long/host" not in cfg["download_dir"]
        assert cfg["min_resolution"] == 1080  # preserved


# ─── Edge deploy generators ───────────────────────────────────────────

class TestEdgeDeploy:
    def test_compose_yaml(self):
        from bulk_downloader import edge_deploy as ed
        y = ed.compose_yaml(include_qbittorrent=True,
                            include_vpn_sidecar=False)
        assert "bulkdownloader:" in y
        assert "qbittorrent:" in y
        assert "vpn:" not in y

    def test_systemd_unit(self):
        from bulk_downloader import edge_deploy as ed
        s = ed.systemd_unit()
        assert "[Service]" in s
        assert "Restart=on-failure" in s

    def test_k8s_manifests(self):
        from bulk_downloader import edge_deploy as ed
        k = ed.k8s_manifests()
        assert "PersistentVolumeClaim" in k
        assert "Deployment" in k


# ─── Metrics + healthcheck ────────────────────────────────────────────

class TestMetrics:
    def test_prometheus_format(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import metrics_prom
        body = metrics_prom.render(
            s_cfg={"vixen": {"name": "V"}, "blacked": {"name": "B"}},
            runners={})
        assert "# HELP" in body
        assert "# TYPE" in body
        assert "bd_history_total" in body
        assert "bd_jobs_total{" in body


class TestHealthCheck:
    def test_run_checklist(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import healthcheck as hc
        chk = hc.run_checklist(s_cfg={"vixen": {"name": "V",
                                                "download_dir": "/tmp"}})
        assert "overall_status" in chk
        assert chk["check_count"] >= 5
        names = {c["name"] for c in chk["checks"]}
        assert "database" in names
        assert "disk" in names


# ─── Batch ops ────────────────────────────────────────────────────────

class TestBatchOps:
    def test_dry_run_doesnt_modify(self):
        _fresh_install()
        _seed_history(dones=0, fails=5)
        from bulk_downloader import batch_ops as bo
        r = bo.bulk_retry({"site_id": "vixen", "status": "failed"},
                          dry_run=True)
        assert r["candidates_matched"] == 5
        assert r["dry_run"]
        assert r["processed"] == 0  # dry-run doesn't process

    def test_retry_resets_status(self):
        _fresh_install()
        _seed_history(dones=0, fails=5)
        from bulk_downloader import batch_ops as bo
        r = bo.bulk_retry({"site_id": "vixen", "status": "failed"},
                          dry_run=False)
        assert r["processed"] == 5
        # Verify they're now pending
        from bulk_downloader import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) FROM history
                                WHERE status = 'pending'
                                AND site_id = 'vixen'""").fetchone()
        assert int(row[0]) == 5


# ─── Exports ──────────────────────────────────────────────────────────

class TestExports:
    def test_csv_export(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import exports
        body = exports.to_csv({"limit": 100})
        assert body.startswith(b"id,site_id")
        lines = body.split(b"\n")
        assert len(lines) > 5

    def test_json_export(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import exports
        body = exports.to_json({"limit": 5})
        data = json.loads(body)
        assert isinstance(data, list)
        assert len(data) <= 5


# ─── Cleanup helpers ──────────────────────────────────────────────────

class TestCleanup:
    def test_find_tinies(self):
        _fresh_install()
        from bulk_downloader.db import db_init, db_log
        db_init()
        # Seed: one normal, one tiny
        db_log("vixen", "V", "u1", "done",
               filename="/dl/a.mp4", file_size=2_000_000_000)
        db_log("vixen", "V", "u2", "done",
               filename="/dl/b.mp4", file_size=1024)
        from bulk_downloader import cleanup_helpers as ch
        tinies = ch.find_tinies(threshold_mb=1)
        assert len(tinies) == 1
        assert tinies[0]["filename"] == "/dl/b.mp4"


# ─── Gamification ─────────────────────────────────────────────────────

class TestGamification:
    def test_achievements_unlock_progressively(self):
        _fresh_install()
        _seed_history(dones=150, sites=("vixen", "blacked"))
        from bulk_downloader import gamification as g
        s = g.stats()
        assert s["total_done"] == 300
        achs = g.check_achievements()
        ids = {a["id"] for a in achs if a["unlocked"]}
        assert "first_download" in ids
        assert "century" in ids


# ─── EOL Export ───────────────────────────────────────────────────────

class TestEOLExport:
    def test_full_export_writes_files(self):
        _fresh_install()
        _seed_history()
        dest = tempfile.mkdtemp(prefix="bdtest-eol-")
        from bulk_downloader import eol_export as eol
        r = eol.full_export(dest, s_cfg={"vixen": {"name": "V",
                                                    "template": "vix"}})
        assert r["ok"]
        files = os.listdir(dest)
        assert "history.csv" in files
        assert "bd-state.json" in files
        assert "README.txt" in files
        assert "templates" in files


# ─── Federation ───────────────────────────────────────────────────────

class TestFederation:
    def test_url_claim_coordination(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import federation as fed
        # Peer 1 claims
        assert fed.claim_url("http://x.com/v/1", "peer1")
        # Same instance can re-claim (idempotent)
        assert fed.claim_url("http://x.com/v/1", "peer1")
        # Different instance is blocked
        assert not fed.claim_url("http://x.com/v/1", "peer2")
        # Release; now peer2 can claim
        fed.release_url("http://x.com/v/1", "peer1")
        assert fed.claim_url("http://x.com/v/1", "peer2")

    def test_hmac_signature_verify(self):
        from bulk_downloader import federation as fed
        sig = fed._compute_token_signature("tok", b"body")
        assert fed.verify_request(b"body", signature=sig, token="tok")
        assert not fed.verify_request(b"body", signature=sig,
                                      token="wrong")


# ─── Backup verify ────────────────────────────────────────────────────

class TestBackupVerify:
    def test_verify_db_dump(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader.constants import DB_PATH
        from bulk_downloader import backup_verify as bv
        r = bv.verify_db_dump(DB_PATH)
        assert r["ok"]
        assert "history" in r["tables"]
        assert r["total_rows"] >= 20


# ─── Cookie quality ───────────────────────────────────────────────────

class TestCookieQuality:
    def test_missing_jar_scores_zero(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import cookie_quality as cq
        # No cookies on disk → score 0
        s = cq.score("nonexistent_site")
        assert s["score"] == 0
        assert s["suggested_action"] == "refresh_now"


# ─── Stream relay ─────────────────────────────────────────────────────

class TestStreamRelay:
    def test_token_round_trip(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import stream_relay as sr
        token = sr.generate_token(42, ttl_seconds=60)
        v = sr.verify_token(token)
        assert v["ok"]
        assert v["history_id"] == 42
        # Tampered
        tampered = token[:-3] + "XXX"
        assert not sr.verify_token(tampered)["ok"]

    def test_range_parser(self):
        from bulk_downloader.stream_relay import _parse_range_header
        assert _parse_range_header("bytes=100-200", 1000) == (100, 200)
        assert _parse_range_header("bytes=-50", 1000) == (950, 999)
        assert _parse_range_header("bytes=500-", 1000) == (500, 999)
        assert _parse_range_header("bytes=2000-3000", 1000) is None


# ─── Discovery ────────────────────────────────────────────────────────

class TestDiscovery:
    def test_rss_parse(self):
        from bulk_downloader.discovery import parse_rss
        body = b'''<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><link>https://x.com/v/1</link></item>
<item><link>https://x.com/v/2</link></item>
</channel></rss>'''
        urls = parse_rss(body)
        assert len(urls) == 2

    def test_sitemap_parse(self):
        from bulk_downloader.discovery import parse_sitemap
        body = b'''<?xml version="1.0"?>
<urlset><url><loc>https://x.com/v/1</loc></url></urlset>'''
        urls = parse_sitemap(body)
        assert urls == ["https://x.com/v/1"]


# ─── Shares ───────────────────────────────────────────────────────────

class TestShares:
    def test_create_verify_revoke(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import shares
        r = shares.create_token(scopes=["all_stats"], label="T",
                                ttl_hours=1)
        assert r["ok"]
        v = shares.verify_token(r["token"], required_scope="all_stats")
        assert v["ok"]
        # Wrong scope
        v = shares.verify_token(r["token"], required_scope="bad")
        assert not v["ok"]
        # Revoke
        assert shares.revoke_token(r["token_id"])
        v = shares.verify_token(r["token"], required_scope="all_stats")
        assert not v["ok"]
        assert v["reason"] == "revoked"


# ─── Command palette ──────────────────────────────────────────────────

class TestCommandPalette:
    def test_search(self):
        from bulk_downloader import command_palette as cp
        results = cp.list_commands(q="pause")
        assert len(results) >= 1
        assert any("pause" in c["label"].lower() for c in results)


# ─── Smart wakeup ─────────────────────────────────────────────────────

class TestSmartWakeup:
    def test_outside_quiet_always_wakes(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import smart_wakeup as sw
        r = sw.should_wake_now(quiet_hours=[], wakeup_threshold=10)
        assert r["wake"]


# ─── Synthetic tests ──────────────────────────────────────────────────

class TestSynthetic:
    def test_run_fixture(self):
        _fresh_install()
        from bulk_downloader import synthetic_tests as syn
        # Create a fake HAR
        fix_dir = syn._fixtures_root() / "test-site"
        fix_dir.mkdir(parents=True, exist_ok=True)
        har = {"log": {"entries": [{
            "request": {"url": "https://x/v"},
            "response": {"status": 200,
                         "content": {"mimeType": "text/html",
                                     "text": "<h1>Hello</h1>"}}}]}}
        (fix_dir / "f1.har").write_text(json.dumps(har))
        r = syn.run_fixture(fix_dir / "f1.har",
                            site_cfg={"title_selector": "h1"})
        assert len(r["selector_results"]) == 1
        assert r["selector_results"][0]["ok"]


# ─── VPN stats ────────────────────────────────────────────────────────

class TestVpnStats:
    def test_best_profile(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import vpn_stats as vs
        for i in range(20):
            vs.record_outcome("good-vpn", "sitea", success=True,
                              latency_ms=100)
        for i in range(20):
            vs.record_outcome("bad-vpn", "sitea", success=False,
                              latency_ms=500)
        best = vs.best_profile_for("sitea", min_attempts=5)
        assert best["vpn_profile"] == "good-vpn"
        assert best["success_rate"] == 100.0


# ─── I18n ─────────────────────────────────────────────────────────────

class TestI18n:
    def test_extract_strings(self):
        from bulk_downloader.i18n import extract_strings
        s = extract_strings(
            "<html><h1>Hello</h1><p>Click Save</p></html>")
        texts = {x["text"] for x in s}
        assert "Hello" in texts
        assert "Click Save" in texts

    def test_save_load_roundtrip(self):
        _fresh_install()
        from bulk_downloader import i18n
        assert i18n.save_locale("es", {"Hello": "Hola"})
        assert i18n.load_locale("es") == {"Hello": "Hola"}
        assert i18n.translate("Hello", lang="es") == "Hola"


# ─── Cluster rate ─────────────────────────────────────────────────────

class TestClusterRate:
    def test_acquire_blocks_above_cap(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import cluster_rate as cr
        leases = []
        for _ in range(3):
            r = cr.acquire_lease("vixen", max_concurrent=3,
                                 lease_seconds=60)
            assert r["ok"]
            leases.append(r["lease_id"])
        # 4th must fail
        r = cr.acquire_lease("vixen", max_concurrent=3, lease_seconds=60)
        assert not r["ok"]
        # Release one — then 4th succeeds
        cr.release_lease(leases[0])
        r = cr.acquire_lease("vixen", max_concurrent=3, lease_seconds=60)
        assert r["ok"]


# ─── Scheduled exports ────────────────────────────────────────────────

class TestScheduledExports:
    def test_run_due_writes_file(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import scheduled_exports as se
        dest = tempfile.mkdtemp(prefix="bdtest-sched-")
        sid = se.add_schedule(label="x", format="csv",
                              destination=dest, cadence_hours=168)
        assert sid is not None
        r = se.run_due_exports()
        assert r["ran"] >= 1
        files = os.listdir(dest)
        assert any(f.startswith("bd-csv-") for f in files)


# ─── Alerts engine ────────────────────────────────────────────────────

class TestAlerts:
    def test_evaluate_no_data_doesnt_crash(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import alerts_engine as ae
        # No history; metrics are 0 or None. Should not raise.
        r = ae.evaluate(s_cfg={})
        assert "evaluated" in r
        assert r["evaluated"] >= 5


# ─── Error fingerprint ────────────────────────────────────────────────

class TestErrorFingerprint:
    def test_normalizes_variable_parts(self):
        from bulk_downloader.error_fingerprint import fingerprint
        a = fingerprint("Connection refused at 192.168.1.42:80 at 14:00:01")
        b = fingerprint("Connection refused at 10.0.0.5:443 at 18:42:11")
        assert a["fingerprint"] == b["fingerprint"]

    def test_different_errors_different_fp(self):
        from bulk_downloader.error_fingerprint import fingerprint
        a = fingerprint("Connection refused")
        b = fingerprint("Permission denied")
        assert a["fingerprint"] != b["fingerprint"]


# ─── Queue priority ───────────────────────────────────────────────────

class TestQueuePriority:
    def test_rank_pending_returns_list(self):
        _fresh_install()
        from bulk_downloader.db import db_init, db_log
        db_init()
        for i in range(5):
            db_log("vixen", "V", f"http://v/{i}", "pending")
        from bulk_downloader import queue_priority as qp
        ranked = qp.rank_pending(limit=10, s_cfg={"vixen": {}})
        assert len(ranked) == 5
        for r in ranked:
            assert "score" in r
            assert "breakdown" in r


# ─── Bandwidth shape ──────────────────────────────────────────────────

class TestBandwidthShape:
    def test_token_bucket_blocks_when_empty(self):
        from bulk_downloader.bandwidth_shape import TokenBucket
        bucket = TokenBucket(capacity=1000, refill_rate=100)
        # Drain
        assert bucket.try_acquire(1000)
        # Next try should fail (bucket empty, no time elapsed)
        assert not bucket.try_acquire(100)

    def test_bucket_refills_over_time(self):
        from bulk_downloader.bandwidth_shape import TokenBucket
        bucket = TokenBucket(capacity=100, refill_rate=10000)
        bucket.try_acquire(100)
        time.sleep(0.1)
        # 10000 bytes/sec × 0.1s = 1000 bytes refilled (capped at 100)
        assert bucket.try_acquire(50)


# ─── Site weather ─────────────────────────────────────────────────────

class TestSiteWeather:
    def test_status_with_no_probes(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import site_weather as sw
        s = sw.site_status("vixen")
        assert s["color"] == "gray"
        assert s.get("probes_total", 0) == 0


# ─── Maintenance windows ──────────────────────────────────────────────

class TestMaintenance:
    def test_add_and_active(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        import datetime as dt
        db_init()
        from bulk_downloader import maintenance as mw
        # Active right now
        now = dt.datetime.now()
        start = (now - dt.timedelta(minutes=5)).isoformat()
        end = (now + dt.timedelta(minutes=5)).isoformat()
        wid = mw.add_window(label="test", start_iso=start,
                            end_iso=end, actions_paused=["workers"])
        assert wid is not None
        active = mw.active_now()
        assert any(w["id"] == wid for w in active)
        assert mw.is_action_paused("workers")
        assert not mw.is_action_paused("exports")


# ─── Timeline ─────────────────────────────────────────────────────────

class TestTimeline:
    def test_merged_returns_dict_entries(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import timeline as tl
        entries = tl.merged_timeline(since_ts=time.time() - 86400)
        assert isinstance(entries, list)
        for e in entries:
            assert "source" in e
            assert "ts" in e


# ─── Cookie clipboard ─────────────────────────────────────────────────

class TestCookieClipboard:
    def test_parse_cookie_header(self):
        from bulk_downloader.cookie_clipboard import auto_detect_and_parse
        r = auto_detect_and_parse("sid=abc; token=def; auth=ghi")
        assert r["count"] >= 3
        names = {c["name"] for c in r["cookies"]}
        assert "sid" in names
        assert "token" in names

    def test_parse_json_array(self):
        from bulk_downloader.cookie_clipboard import auto_detect_and_parse
        body = json.dumps([
            {"name": "sid", "value": "abc", "domain": ".x.com",
             "secure": True, "expirationDate": 9999999999},
        ])
        r = auto_detect_and_parse(body)
        assert r["format"] == "json_array"
        assert r["count"] == 1


# ─── Migrations ───────────────────────────────────────────────────────

class TestMigrations:
    def test_drift_detection_on_clean_db(self):
        _fresh_install()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import migrations as m
        drift = m.detect_drift()
        # history + events exist after db_init
        assert "history" not in drift["missing_tables"]


# ─── BW chart ─────────────────────────────────────────────────────────

class TestBWChart:
    def test_hourly_bandwidth_dense_series(self):
        _fresh_install()
        _seed_history()
        from bulk_downloader import bw_chart as bw
        r = bw.hourly_bandwidth(hours=24)
        assert len(r["labels"]) == 24
        assert len(r["bytes"]) == 24
        assert r["total_bytes"] > 0
