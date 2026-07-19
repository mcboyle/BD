"""RED-first repro for F-COREBD11-01.

``global_config.set_config`` persists the secret-bearing global config
(``app_config.json``) via a temp-file + rename with no restrictive permissions,
so under the process umask it lands group/other-readable. A secret written into
the global config (tokens, credentials) is therefore exposed to any local
reader. After the fix the persisted file is owner-only (0o600).

Pristine-source RED: with a permissive umask the written file's mode carries
group/other bits, so the ``mode & 0o077 == 0`` assertion fails until set_config
restricts the file.
"""
import os
import stat

from bulk_downloader import global_config as gc


def test_config_file_is_owner_only(tmp_path, monkeypatch):
    cfg = tmp_path / "app_config.json"
    monkeypatch.setattr(gc, "_CONFIG_FILE", cfg)
    # Force a permissive umask so an unrestricted write would be group/world readable.
    old = os.umask(0o022)
    try:
        assert gc.set_config({"secret_token": "s3cr3t", "auto_refresh": False}) is True
    finally:
        os.umask(old)
    assert cfg.exists()
    mode = stat.S_IMODE(cfg.stat().st_mode)
    assert mode & 0o077 == 0, f"config is group/other-accessible: {oct(mode)}"
