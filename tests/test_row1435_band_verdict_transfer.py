"""Cut 1435: a band PASS transfers only across four proven dispositions."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import re
import subprocess

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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--", "bulk_downloader/__init__.py",
         "tests/test_settings_center_slice4.py", "CHANGELOG.md", "feature.txt",
         "unrelated.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _commit_only(repo: Path, message: str, *paths: str) -> str:
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _minimal_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Band Transfer Test")
    _git(repo, "config", "user.email", "band-transfer@example.invalid")
    (repo / "bulk_downloader").mkdir()
    (repo / "tests").mkdir()
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "3.66.1000"\n', encoding="ascii"
    )
    (repo / "tests" / "test_settings_center_slice4.py").write_text(
        'def test_version():\n    assert __version__ == "3.66.1000"\n',
        encoding="ascii",
    )
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v3.66.1000 - Base\n\nbase body\n", encoding="ascii"
    )
    (repo / "feature.txt").write_text("base feature\n", encoding="ascii")
    (repo / "unrelated.txt").write_text("base unrelated\n", encoding="ascii")
    base = _commit_only(
        repo,
        "fixture base",
        "bulk_downloader/__init__.py",
        "tests/test_settings_center_slice4.py",
        "CHANGELOG.md",
        "feature.txt",
        "unrelated.txt",
    )
    return repo, base


def _stub_non_authored_dispositions(monkeypatch, tool):
    calls = {"derived": [], "declared": []}

    def derived(repo, head):
        calls["derived"].append(head)
        return ("DERIVED.json",)

    def declared(repo, base, head):
        calls["declared"].append((base, head))
        return {"base": 1, "head": 1, "live": 1}

    monkeypatch.setattr(tool, "_derived_outputs", derived)
    monkeypatch.setattr(tool, "_declared_evidence", declared)
    return calls


def _release_edit(repo: Path, version: str, title: str) -> None:
    init_path = repo / "bulk_downloader" / "__init__.py"
    pin_path = repo / "tests" / "test_settings_center_slice4.py"
    changelog_path = repo / "CHANGELOG.md"
    init = init_path.read_text(encoding="utf-8")
    current = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE).group(1)
    pin = pin_path.read_text(encoding="utf-8")
    changelog = changelog_path.read_text(encoding="utf-8")
    header = re.search(r"^## v", changelog, re.MULTILINE)
    assert init.count(f'__version__ = "{current}"') == 1
    assert pin.count(f'__version__ == "{current}"') == 1
    assert header is not None
    init_path.write_text(
        init.replace(f'__version__ = "{current}"', f'__version__ = "{version}"', 1),
        encoding="utf-8",
    )
    pin_path.write_text(
        pin.replace(f'__version__ == "{current}"', f'__version__ == "{version}"', 1),
        encoding="utf-8",
    )
    entry = f"## v{version} - {title}\n\nidentical reviewed entry body\n\n"
    changelog_path.write_text(
        changelog[:header.start()] + entry + changelog[header.start():],
        encoding="utf-8",
    )


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


def test_live_derived_population_is_nonzero_and_includes_both_previously_blind_outputs():
    tool = _load_tool()
    loader = importlib.machinery.SourceFileLoader(
        "row1435_regen", str(REPO / "toolchain/bin/bd-regen-order")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)

    derived = tool.derive_tracked_outputs(
        regen.REQUIRED_CHAIN_LABELS, regen.TRACKED_OUTPUTS_BY_LABEL
    )

    assert derived == regen.tracked_outputs()
    assert len(derived) == len(set(derived)) > 0
    assert {"INV_TAGS.md", "tests/source_window_hashes.json"} <= set(derived)


def test_unknown_identity_refuses_and_cannot_yield_a_transfer_key():
    tool = _load_tool()
    identities = {"base": "a" * 40, "head": "UNKNOWN", "tree": "b" * 40}
    assert "UNKNOWN" in identities.values()

    with pytest.raises(tool.TransferRefused, match="UNKNOWN.*head"):
        tool.require_known(**identities)


def test_version_free_candidates_have_a_digest_and_can_transfer(tmp_path, monkeypatch):
    tool = _load_tool()
    calls = _stub_non_authored_dispositions(monkeypatch, tool)
    repo, base = _minimal_repo(tmp_path)

    _git(repo, "checkout", "-q", "-b", "old-candidate")
    (repo / "feature.txt").write_text("candidate feature\n", encoding="ascii")
    old_head = _commit_only(repo, "old candidate", "feature.txt")

    _git(repo, "checkout", "-q", "-b", "new-main", base)
    (repo / "unrelated.txt").write_text("new main bytes\n", encoding="ascii")
    new_base = _commit_only(repo, "new main", "unrelated.txt")

    _git(repo, "checkout", "-q", "-b", "rebased-candidate")
    (repo / "feature.txt").write_text("candidate feature\n", encoding="ascii")
    new_head = _commit_only(repo, "rebased candidate", "feature.txt")

    assert tool._paths_between(repo, base, old_head) == {"feature.txt"}
    assert tool._paths_between(repo, new_base, new_head) == {"feature.txt"}
    old = tool.evidence_at_commit(repo, base, old_head)
    new = tool.evidence_at_commit(repo, new_base, new_head)
    tool.compare_evidence(old, new)

    assert re.fullmatch(r"[0-9a-f]{64}", old["digest"])
    assert old["digest"] == new["digest"]
    assert old["trio"] == new["trio"] == {}
    assert old["trio_entry_bytes"] is new["trio_entry_bytes"] is None
    assert calls == {
        "derived": [old_head, new_head],
        "declared": [(base, old_head), (new_base, new_head)],
    }


def test_partial_release_trio_refuses_and_names_the_missing_member(tmp_path):
    tool = _load_tool()
    repo, base = _minimal_repo(tmp_path)
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "3.66.1001"\n', encoding="ascii"
    )
    (repo / "tests" / "test_settings_center_slice4.py").write_text(
        'def test_version():\n    assert __version__ == "3.66.1001"\n',
        encoding="ascii",
    )
    head = _commit_only(
        repo,
        "partial trio",
        "bulk_downloader/__init__.py",
        "tests/test_settings_center_slice4.py",
    )

    changed = tool._paths_between(repo, base, head)
    assert changed == {
        "bulk_downloader/__init__.py",
        "tests/test_settings_center_slice4.py",
    }
    with pytest.raises(tool.TransferRefused) as refused:
        tool.build_evidence(repo, base, head)
    assert str(refused.value) == (
        "partial release trio in candidate delta: "
        "present=bulk_downloader/__init__.py, tests/test_settings_center_slice4.py "
        "missing=CHANGELOG.md"
    )


def test_complete_release_trio_keeps_its_fixed_content_digest(tmp_path, monkeypatch):
    tool = _load_tool()
    calls = _stub_non_authored_dispositions(monkeypatch, tool)
    monkeypatch.setattr(tool, "_last_touch", lambda repo, base, path: "fixed-base-touch")
    repo, base = _minimal_repo(tmp_path)
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "3.66.1001"\n', encoding="ascii"
    )
    (repo / "tests" / "test_settings_center_slice4.py").write_text(
        'def test_version():\n    assert __version__ == "3.66.1001"\n',
        encoding="ascii",
    )
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## v3.66.1001 - Fixed release\n\nfixed entry body\n\n"
        "## v3.66.1000 - Base\n\nbase body\n",
        encoding="ascii",
    )
    (repo / "feature.txt").write_text("candidate feature\n", encoding="ascii")
    head = _commit_only(
        repo,
        "complete trio",
        "bulk_downloader/__init__.py",
        "tests/test_settings_center_slice4.py",
        "CHANGELOG.md",
        "feature.txt",
    )

    changed = tool._paths_between(repo, base, head)
    assert changed == set(tool.TRIO) | {"feature.txt"}
    evidence = tool.build_evidence(repo, base, head)
    assert evidence["digest"] == (
        "f35df039bcc9b8ae6aaaefb8061e65d4a0783561734425271928d8a981cce43a"
    )
    assert tool._trio_disposition(changed) == "COMPLETE"
    assert calls == {
        "derived": [head],
        "declared": [(base, head)],
    }


def test_real_git_rebase_keeps_one_content_digest_when_all_dispositions_hold(tmp_path):
    tool = _load_tool()
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(REPO), str(repo)], check=True
    )
    _git(repo, "config", "user.name", "Band Transfer Test")
    _git(repo, "config", "user.email", "band-transfer@example.invalid")
    (repo / "feature.txt").write_text("base authored bytes\n", encoding="ascii")
    (repo / "unrelated.txt").write_text("base unrelated bytes\n", encoding="ascii")
    base = _commit(repo, "fixture base")

    _git(repo, "checkout", "-q", "-b", "old-candidate")
    _release_edit(repo, "3.66.9001", "same transferred feature")
    (repo / "feature.txt").write_text("identical authored bytes\n", encoding="ascii")
    old_head = _commit(repo, "old candidate")

    _git(repo, "checkout", "-q", "-b", "new-main", base)
    _release_edit(repo, "3.66.9002", "other candidate landed")
    (repo / "unrelated.txt").write_text("main gained unrelated authored bytes\n", encoding="ascii")
    new_base = _commit(repo, "new main")

    _git(repo, "checkout", "-q", "-b", "rebased-candidate")
    _release_edit(repo, "3.66.9003", "same transferred feature")
    (repo / "feature.txt").write_text("identical authored bytes\n", encoding="ascii")
    new_head = _commit(repo, "rebased candidate")

    old = tool.evidence_at_commit(repo, base, old_head)
    new = tool.evidence_at_commit(repo, new_base, new_head)
    tool.compare_evidence(old, new)

    assert old["digest"] == new["digest"]
    assert old["derived_denominator"] == new["derived_denominator"] > 0
    assert old["authored_blobs"] == new["authored_blobs"] == {
        "feature.txt": old["authored_blobs"]["feature.txt"],
    }
