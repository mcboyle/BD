"""O1671 a18 (AUDIT-18 HIGH SEC): tools/rollback.py extracted each zip member to
``app_dir / target_rel`` with no containment check, so a member named
``../x``, an absolute name, or a path through a symlink wrote outside the app dir
(Zip Slip). A release zip with any member whose resolved target leaves app_dir
must fail the extract (rc 2) and write nothing.
"""
import importlib.util
import os
import zipfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
ROLLBACK = Path(__file__).resolve().parent.parent / "tools" / "rollback.py"
OLD_INIT = '__version__ = "3.66.1"\n'
NEW_INIT = '__version__ = "3.66.2"\n'


@pytest.fixture
def rb(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("o1671_a18_rollback", ROLLBACK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = tmp_path / "app"
    (app / "bulk_downloader").mkdir(parents=True)
    (app / "bulk_downloader" / "__init__.py").write_text(OLD_INIT)
    archive = tmp_path / "archive"
    archive.mkdir()
    calls = []
    monkeypatch.setattr(module, "_app_dir", lambda: app)
    monkeypatch.setattr(module, "_systemctl", lambda action: calls.append(action) or (0, ""))
    return module, app, archive, tmp_path


def _release(archive, members):
    path = archive / "BulkDownloader_v3_66_2.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


def _rollback(module, archive):
    return module.cmd_rollback(archive, "3.66.2", skip_service=False, skip_confirm=True)


def test_clean_release_extracts_positive_control(rb):
    module, app, archive, _ = rb
    _release(archive, [("bulk_downloader/__init__.py", NEW_INIT), ("tools/x.py", "x = 1\n")])
    assert _rollback(module, archive) == 0
    assert (app / "bulk_downloader" / "__init__.py").read_text() == NEW_INIT
    assert (app / "tools" / "x.py").read_text() == "x = 1\n"


def _hostile_cases(tmp_path):
    outside = tmp_path / "outside"
    return {
        "dotdot_flat": ([("bulk_downloader/__init__.py", NEW_INIT), ("../escape.txt", "pwn")],
                        tmp_path / "escape.txt"),
        "dotdot_after_prefix": ([("BD/bulk_downloader/__init__.py", NEW_INIT), ("BD/../escape2.txt", "pwn")],
                                tmp_path / "escape2.txt"),
        "absolute": ([("bulk_downloader/__init__.py", NEW_INIT), (str(outside / "abs.txt"), "pwn")],
                     outside / "abs.txt"),
        "backslash_dotdot": ([("bulk_downloader/__init__.py", NEW_INIT), ("..\\escape3.txt", "pwn")],
                             tmp_path / "escape3.txt"),
    }


@pytest.mark.parametrize("case", ["dotdot_flat", "dotdot_after_prefix", "absolute", "backslash_dotdot"])
def test_escaping_member_fails_and_writes_nothing(rb, capsys, case):
    module, app, archive, tmp_path = rb
    (tmp_path / "outside").mkdir()
    members, escaped = _hostile_cases(tmp_path)[case]
    _release(archive, members)
    rc = _rollback(module, archive)
    assert not escaped.exists(), f"zip slip wrote {escaped}"
    assert rc == 2
    assert "FAIL: unsafe zip member" in capsys.readouterr().err
    assert (app / "bulk_downloader" / "__init__.py").read_text() == OLD_INIT, "partial extract before refusing"


def test_drive_letter_member_refused(rb, capsys):
    module, app, archive, _ = rb
    _release(archive, [("bulk_downloader/__init__.py", NEW_INIT), ("C:/Windows/evil.txt", "pwn")])
    assert _rollback(module, archive) == 2
    assert "FAIL: unsafe zip member" in capsys.readouterr().err
    assert not (app / "C:").exists()


def test_dotdot_segment_refused_even_when_it_resolves_inside(rb, capsys):
    module, app, archive, _ = rb
    _release(archive, [("bulk_downloader/__init__.py", NEW_INIT), ("bulk_downloader/../evil.py", "pwn")])
    assert _rollback(module, archive) == 2
    assert not (app / "evil.py").exists()
    assert "FAIL: unsafe zip member" in capsys.readouterr().err


def test_member_through_symlink_out_of_app_refused(rb, capsys):
    module, app, archive, tmp_path = rb
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, app / "link")
    _release(archive, [("bulk_downloader/__init__.py", NEW_INIT), ("link/evil.txt", "pwn")])
    assert _rollback(module, archive) == 2
    assert not (outside / "evil.txt").exists()
    assert "FAIL: unsafe zip member" in capsys.readouterr().err
