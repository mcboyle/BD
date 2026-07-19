"""Cut v3.66.638 / INTEROP-GOV-1: the interop provenance + risk-ack registry (keystone).

EXT-1 (the chromium-extension loader) already ships (runner_browser.py loads
`chromium_extensions` dirs), but UNGATED -- a configured dir loads with no
provenance recorded and no risk acknowledged. The interop roadmap's governance
decision (@601): an OPEN loader + per-plugin risk-ack + provenance, NOT an
allowlist. This module is the keystone every interop track (extensions / JD /
yt-dlp plugins) loads through: it RECORDS provenance (source, sha256, commit) and
REQUIRES an explicit risk acknowledgment + enable before an item is permitted.
Off by default -- an unregistered / unacked / disabled item is never permitted.

Disk-backed JSON under BD_HOME; stateless (each call reads/writes disk, so nothing
to cache or reload). Pure stdlib, no Flask.

RED on pristine 3.66.637: bulk_downloader.interop_registry does not exist.
"""
import os
import tempfile


def _fresh():
    """Bind the registry to a fresh empty home dir (isolated per test)."""
    os.environ["BD_HOME"] = tempfile.mkdtemp()
    import bulk_downloader.interop_registry as ir
    return ir


def test_register_is_off_by_default():
    ir = _fresh()
    ir.register("chromium_extension", "/ext/adblock",
                source="github.com/x/adblock", sha256="abc")
    rec = ir.get("chromium_extension", "/ext/adblock")
    assert rec is not None
    assert rec["risk_acknowledged"] is False
    assert rec["enabled"] is False
    assert rec["source"] == "github.com/x/adblock"
    assert rec["sha256"] == "abc"
    assert ir.is_permitted("chromium_extension", "/ext/adblock") is False


def test_permitted_requires_ack_AND_enabled():
    ir = _fresh()
    ir.register("chromium_extension", "e", sha256="h1")
    assert ir.is_permitted("chromium_extension", "e") is False
    ir.acknowledge("chromium_extension", "e")
    assert ir.is_permitted("chromium_extension", "e") is False   # acked, not enabled
    ir.set_enabled("chromium_extension", "e", True)
    assert ir.is_permitted("chromium_extension", "e") is True    # acked AND enabled
    ir.set_enabled("chromium_extension", "e", False)
    assert ir.is_permitted("chromium_extension", "e") is False   # disabled again


def test_unregistered_is_never_permitted():
    ir = _fresh()
    assert ir.is_permitted("chromium_extension", "never_seen") is False
    assert ir.get("chromium_extension", "never_seen") is None


def test_acknowledge_unregistered_is_noop_false():
    ir = _fresh()
    assert ir.acknowledge("chromium_extension", "ghost") is False
    assert ir.get("chromium_extension", "ghost") is None


def test_provenance_change_resets_ack():
    """Re-registering with a DIFFERENT sha256 (the item changed on disk) resets
    the ack -- an item can't silently change under an existing ack (pin property)."""
    ir = _fresh()
    ir.register("chromium_extension", "e", sha256="h1")
    ir.acknowledge("chromium_extension", "e")
    ir.set_enabled("chromium_extension", "e", True)
    assert ir.is_permitted("chromium_extension", "e") is True
    ir.register("chromium_extension", "e", sha256="h2")   # content changed
    assert ir.get("chromium_extension", "e")["risk_acknowledged"] is False
    assert ir.is_permitted("chromium_extension", "e") is False   # must re-ack


def test_reregister_same_hash_preserves_ack():
    ir = _fresh()
    ir.register("chromium_extension", "e", sha256="h1")
    ir.acknowledge("chromium_extension", "e")
    ir.register("chromium_extension", "e", sha256="h1", source="updated-source")
    assert ir.get("chromium_extension", "e")["risk_acknowledged"] is True
    assert ir.get("chromium_extension", "e")["source"] == "updated-source"


def test_list_all_and_disk_persistence():
    ir = _fresh()
    ir.register("chromium_extension", "e1", sha256="a")
    ir.register("ytdlp_plugin", "p1", source="repo@commit", commit="deadbeef")
    kinds = {(k, i) for k, i, _ in ir.list_all()}
    assert ("chromium_extension", "e1") in kinds
    assert ("ytdlp_plugin", "p1") in kinds
    # stateless/disk-backed: a re-read sees the persisted rows
    assert ir.get("ytdlp_plugin", "p1")["commit"] == "deadbeef"


def test_dir_sha256_stable_and_content_sensitive():
    ir = _fresh()
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        fh.write('{"name":"x"}')
    h1 = ir.dir_sha256(d)
    assert h1 == ir.dir_sha256(d) and len(h1) == 64       # stable, hex sha256
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        fh.write('{"name":"y"}')                            # content changed
    assert ir.dir_sha256(d) != h1                          # content-sensitive


def test_is_permitted_live_hash_pin():
    """With a live hash supplied, a mismatch (item changed on disk since ack)
    blocks even a registered+acked+enabled item."""
    ir = _fresh()
    ir.register("chromium_extension", "e", sha256="pinned")
    ir.acknowledge("chromium_extension", "e")
    ir.set_enabled("chromium_extension", "e", True)
    assert ir.is_permitted("chromium_extension", "e") is True                  # no live check
    assert ir.is_permitted("chromium_extension", "e", "pinned") is True        # live == registered
    assert ir.is_permitted("chromium_extension", "e", "changed") is False      # live != registered
