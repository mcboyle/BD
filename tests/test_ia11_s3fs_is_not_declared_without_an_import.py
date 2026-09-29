"""IA-11 -- requirements.txt does not install s3fs, which nothing imports.

async_object_storage.S3FSAsyncAdapter only duck-types an INJECTED filesystem
object, and "s3fs" is a backend label in the config, never a constructor: no
module in the tree imports s3fs. The pin still made every install resolve and
download s3fs (and its fsspec / aiobotocore constraints) for nothing.

Both sides are derived from the tracked tree: the declared names from
requirements.txt, the imported roots from an AST walk of bulk_downloader/ and
tools/. The positive control (aiobotocore: declared AND imported) shows the
import census can say yes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _declared(manifest: Path) -> set:
    names = set()
    for line in manifest.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _NAME.match(line)
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def _imported_roots() -> set:
    roots = set()
    files = [p for d in ("bulk_downloader", "tools") for p in (_REPO / d).rglob("*.py")]
    assert len(files) > 100, f"import census denominator too small: {len(files)} files"
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_s3fs_is_not_declared_because_nothing_imports_it():
    declared = _declared(_REPO / "requirements.txt")
    imported = _imported_roots()
    # Positive control: a declared dependency the tree does import is seen as imported.
    assert "aiobotocore" in declared and "aiobotocore" in imported
    assert "s3fs" not in imported, "s3fs is now imported -- keep the pin and drop this test"
    assert "s3fs" not in declared, (
        "IA-11: requirements.txt installs s3fs but no module imports it "
        "(async_object_storage only duck-types an injected instance)"
    )
