"""The zip-era install workflow was abolished; its tools must not come back.

WHY THEY WENT. CLAUDE.md section 7: the deploy is `git fetch` + `git reset
--hard`, and "there is no zip overlay and no zip fallback". These eleven files
implemented the workflow that replaced -- `bd-install` unzipped a
`BulkDownloader_v*.zip` into a work tree and `rm -rf`'d it, `bd-boot` booted
from three zip kinds, `bd-handoff` REQUIRED a `--zip`, and the build scripts
staged releases under the retired sandbox home. Rewriting their paths would
have produced a working installer for a process that no longer exists, so they
were retired rather than repointed (item 38, operator decision at v3.66.961).

WHY A GATE AND NOT JUST A DELETION. Three tools retired before @858 came back
as tracked runnable files that no gate could see -- that is item 16, and it
took until @917 to notice. A deletion nothing watches is a deletion that gets
undone by the next person who needs the file for something.

THE COUPLING IS THE PART WORTH KEEPING. `bd-coretest` carried dedicated
`test_handoff` and `test_zipcheck` probes and named both in CORE_TOOLS, so the
retirement had to remove them in the same cut or the suite's own core battery
would call a missing executable. That is measured, not assumed: bd-coretest's
selftest passes without them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Retired at v3.66.961. Removing an entry needs an operator decision recorded
# in project-knowledge/SESSION_CARRY.md -- this list is the record that they
# went deliberately, not the mute button for a file someone re-added.
_RETIRED = (
    "toolchain/bin/bd-install",
    "toolchain/bin/bd-boot",
    "toolchain/bin/bd-repin-dist",
    "toolchain/bin/bd-zipcheck",
    "toolchain/bin/bd-handoff",
    "project-knowledge/install_bulkdl_kits.sh",
    "project-knowledge/gen_batch_kickoffs.py",
    "scripts/build_146.sh",
    "scripts/build_147.sh",
    "scripts/build_148.sh",
    "scripts/build_150.sh",
)


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(_REPO),
                         capture_output=True, text=True, check=True).stdout
    return {p for p in out.split("\0") if p}


def test_no_retired_zip_era_file_is_tracked_again():
    tracked = _tracked()
    assert tracked, (
        "BD-GATE-UNRUNNABLE: git ls-files returned nothing, so this assertion "
        "would pass over an empty tree")
    back = sorted(set(_RETIRED) & tracked)
    assert not back, (
        f"{len(back)} retired zip-era file(s) are tracked again: {back}. The "
        f"zip install workflow was abolished when the deploy became git; if "
        f"one is genuinely needed, that is an operator decision to record, "
        f"not a re-add.")


def test_nothing_invokes_a_retired_tool():
    """A dangling invocation is worse than the file: it fails at run time.

    bd-coretest called bd-handoff and bd-zipcheck through
    `os.path.join(BIN, ...)`, which no import graph or grep for a module name
    would have surfaced.
    """
    names = [Path(p).name for p in _RETIRED if p.startswith("toolchain/bin/")]
    bad = []
    for name in names:
        hits = subprocess.run(
            ["git", "grep", "-n", "-E",
             r'join\(\s*BIN\s*,\s*["\']' + name + r'["\']|toolchain/bin/' + name + r'\b',
             "--", "."],
            cwd=str(_REPO), capture_output=True, text=True).stdout.splitlines()
        for h in hits:
            f = h.split(":", 1)[0]
            # Markdown is PROSE, and prose naming a retired tool is a record,
            # not a call. The first draft of this gate failed on a
            # pending-spec sentence describing the very retirement it guards.
            if f.endswith(".md") or f == "tests/test_zip_era_tools_stay_retired.py":
                continue
            bad.append(h)
    assert not bad, (
        "something still invokes a retired zip-era tool by path:\n  "
        + "\n  ".join(bad[:10]))
