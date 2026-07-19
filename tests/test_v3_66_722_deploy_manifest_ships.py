"""v3.66.722 -- deploy_manifest ships IN the release, so it reaches stash.

`bd-deploy-manifest` existed only in the sandbox bdsuite, which is why running it on
stash gave `command not found` -- and the orphan cleanup it exists to emit had to be
done by hand (718: app_sched_exports.py, deleted @716, still on the stash disk, tripping
the disk-globbing graph gates and turning three suites RED against a CORRECT release).

Shipping it as tools/deploy_manifest.py means the overlay deploy carries it, so it is on
stash from the next deploy onward.

BOOTSTRAPPING NOTE (stated once, honestly): it lands *with* a deploy, so it cannot clean
up the very deploy that introduces it. For THIS release, generate the rm lines in the
sandbox and paste them. From the next deletion cut onward it is already there:

    python3 tools/deploy_manifest.py --zip ~/BulkDownloader_v3_66_<n>.zip --script | sh
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_deploy_manifest_is_in_the_tools_tree():
    p = os.path.join(ROOT, "tools", "deploy_manifest.py")
    assert os.path.isfile(p), (
        "tools/deploy_manifest.py missing -- it must ship in the release, or it never "
        "reaches stash and the operator gets `command not found` (bit @718)")


def test_it_refuses_to_propose_deleting_runtime_data():
    """The guard that makes it safe to pipe into `sh`: a runtime store absent from the
    zip must NEVER be proposed for deletion. Without this, `--script | sh` could nuke
    the DB, the .env, or the secrets."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "deploy_manifest", os.path.join(ROOT, "tools", "deploy_manifest.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tree = os.path.join(td, "tree")
        os.makedirs(os.path.join(tree, "bulk_downloader"))
        # a real orphan + the runtime stores that must be spared
        open(os.path.join(tree, "bulk_downloader", "orphan.py"), "w").write("x")
        for keep in ("bulk_downloader.db", "app_config.json", "secrets.json",
                     "sites_config.json", ".env"):
            open(os.path.join(tree, keep), "w").write("x")
        zp = os.path.join(td, "rel.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("bulk_downloader/keep.py", "x")

        gone = m.orphans(zp, tree)
        assert "bulk_downloader/orphan.py" in gone, "the real orphan was not flagged"
        for keep in ("bulk_downloader.db", "app_config.json", "secrets.json",
                     "sites_config.json", ".env"):
            assert keep not in gone, (
                "%s was proposed for deletion -- piping --script into sh would destroy "
                "runtime state" % keep)
