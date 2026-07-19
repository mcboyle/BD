"""RED-first repro for F-TOOLSO04-02.

``organize_captures._fs_safe`` keeps ``.`` and ``-`` in its allowlist, so a
dot-only host/format token (``.``, ``..``, ``...``) passes through unchanged and
is then used verbatim as a path segment (``args.organize / _fs_safe(host) /
...``) -> directory traversal / self-reference. After the fix a dot-only token
maps to ``unknown``, while ordinary hosts are still slugified unchanged.

Pristine-source RED: ``_fs_safe('..') == '..'``, so the dot-only assertions fail.
"""
import importlib.util
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parent.parent / "tools" / "organize_captures.py"
    spec = importlib.util.spec_from_file_location("_organize_captures_t", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fs_safe_rejects_dot_only_segments():
    m = _load()
    for bad in ("..", ".", "..."):
        got = m._fs_safe(bad)
        assert got.strip(".") != "", f"_fs_safe({bad!r}) -> {got!r} (dot-only/traversal)"
    # ordinary tokens still slugify as before
    assert m._fs_safe("example.com") == "example.com"
    assert m._fs_safe("a/b") == "a_b"
    assert m._fs_safe("") == "unknown"
