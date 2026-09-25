BD_GATE_SCOPE = "module"

"""A present but unreadable hold store cannot authorize downloads."""

import json

from bulk_downloader import download_hold


def test_broken_hold_store_symlink_is_unknown(tmp_path):
    absent = tmp_path / "new-install.json"
    assert not absent.exists()
    assert download_hold.hold_state(absent)["state"] == download_hold.CLEAR

    target = tmp_path / "record.json"
    store = tmp_path / "app_config.json"
    store.symlink_to(target)
    assert store.is_symlink()
    assert not store.exists()
    assert download_hold.hold_state(store)["state"] == download_hold.UNKNOWN

    target.write_text(json.dumps({"download_hold": {"held": True}}))
    assert download_hold.hold_state(store)["state"] == download_hold.HELD
