"""Cut 1435: a band PASS transfers only across four proven dispositions."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-band-transfer-key"


def _load_tool():
    loader = importlib.machinery.SourceFileLoader("row1435_transfer", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authored_blob_difference_refuses_instead_of_treating_reapply_as_identity():
    tool = _load_tool()
    old = {"bulk_downloader/db.py": "blob-old"}
    new = {"bulk_downloader/db.py": "blob-new"}
    assert old.keys() == new.keys() and old != new

    with pytest.raises(tool.TransferRefused, match="authored blob differs.*db.py"):
        tool.compare_authored(old, new, set())


def test_authored_overlap_with_mains_gained_authored_paths_refuses():
    tool = _load_tool()
    blobs = {"bulk_downloader/runner_transport.py": "same-blob"}
    gained = {"bulk_downloader/runner_transport.py"}
    assert blobs.keys() & gained == gained

    with pytest.raises(tool.TransferRefused, match="authored overlap.*runner_transport.py"):
        tool.compare_authored(blobs, dict(blobs), gained)


def test_changelog_entry_difference_beyond_version_token_refuses():
    tool = _load_tool()
    old = b"## v3.66.1417 - same title\n\nreviewed body\n"
    new = b"## v3.66.1418 - same title\n\nDIFFERENT body\n"
    assert old.splitlines()[0].replace(b"1417", b"1418") == new.splitlines()[0]
    assert old.splitlines()[2] != new.splitlines()[2]

    with pytest.raises(tool.TransferRefused, match="CHANGELOG entry differs"):
        tool.compare_changelog_entries(old, new)


def test_declared_edge_absent_from_base_and_live_graph_refuses():
    tool = _load_tool()
    base = {("bulk_downloader/a.py", "bulk_downloader/b.py")}
    invented = ("bulk_downloader/a.py", "bulk_downloader/invented.py")
    head = base | {invented}
    live = set(base)
    assert invented not in base and invented not in live and invented in head

    with pytest.raises(tool.TransferRefused, match="declared edge absent from both sides"):
        tool.validate_declared_union(base, head, live, gate_rc=0)


def test_derived_output_declaration_disagreement_with_required_chain_refuses():
    tool = _load_tool()
    required = ("ROUTE_INDEX", "SOURCE_WINDOW_HASHES")
    partial = {"ROUTE_INDEX": ("ROUTE_INDEX.json",)}
    assert set(required) != set(partial)

    with pytest.raises(tool.TransferRefused, match="derived chain labels disagree"):
        tool.derive_tracked_outputs(required, partial)


def test_unknown_identity_refuses_and_cannot_yield_a_transfer_key():
    tool = _load_tool()
    identities = {"base": "a" * 40, "head": "UNKNOWN", "tree": "b" * 40}
    assert "UNKNOWN" in identities.values()

    with pytest.raises(tool.TransferRefused, match="UNKNOWN.*head"):
        tool.require_known(**identities)
