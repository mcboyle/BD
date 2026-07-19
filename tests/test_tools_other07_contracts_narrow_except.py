"""RED-first repro for F-TOOLS_OTHER07-02.

runner_contracts.main() parses each sibling module under `except: continue`,
which swallows BaseException -- so a non-parse error (KeyboardInterrupt,
SystemExit, or an OSError like reading a directory) is silently dropped instead
of surfacing. After the fix the except is narrowed to (SyntaxError,
UnicodeDecodeError), so a genuine non-parse error propagates.

Pristine RED: an IsADirectoryError while reading a globbed *.py path is swallowed
(main does not raise).
"""
import importlib.util
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parent.parent / "tools" / "runner_contracts.py"
    s = importlib.util.spec_from_file_location("_rc_to07", str(p))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def test_non_parse_error_propagates(tmp_path, monkeypatch):
    m = _load()
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "evil.py").mkdir()   # a directory named *.py -> open() raises IsADirectoryError
    monkeypatch.setattr(m, "PKG", str(pkg))
    monkeypatch.setattr(m, "ROOT", str(tmp_path))   # isolate any doc writes
    raised = None
    try:
        m.main()
    except BaseException as e:  # noqa: BLE001 - we assert on the type
        raised = e
    assert isinstance(raised, IsADirectoryError), f"got {type(raised).__name__}: {raised!r}"
