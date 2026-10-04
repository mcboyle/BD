"""O1698 M7 state line (trial): the bd-guard prompt.submit mod, as a harness candidate.

The candidate is a HARNESS tree (O1045/O1066): it lives under bd-persist/harness-work/FIX/, never in this
repo. BD_TEST_O1698_MOD_M7_STATE_LINE_TRIAL=1 opts in; BD_O1698_MOD_M7_STATE_LINE_TRIAL_CANDIDATE overrides
the candidate's bd-guard folder. The behaviour (exact line, `?` fallbacks, OFF by default) is proved by the
candidate's own hooks/stateline.test.ts under `claude plugin test`, which the last test runs where claude is on
PATH; the rest check the source shape a lens and the PM install rely on.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_MOD_M7_STATE_LINE_TRIAL") != "1",
    reason="candidate opt-in required (BD_TEST_O1698_MOD_M7_STATE_LINE_TRIAL=1)",
)

FIX = Path("/home/mboyle/bd-persist/harness-work/FIX/o1698-mod-m7-state-line-trial")
PLUGIN = Path(os.environ.get("BD_O1698_MOD_M7_STATE_LINE_TRIAL_CANDIDATE", str(FIX / "bd-guard")))
# the live plugins/bd-guard this candidate was cut on (stateline.ts 544397af, after the ledger repoint)
BASE = FIX / "base-live-544397af" / "bd-guard"


def _read(rel: str, root: Path = PLUGIN) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The source without // comments, so a mention in prose is not a call."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_only_stateline_changes_and_its_code_is_the_live_code():
    # register.tsx is already live: the candidate must not ship it
    assert _read("hooks/register.tsx") == _read("hooks/register.tsx", BASE)
    # a comment-only change, so installing cannot revert what live already carries (the ledger repoint)
    def lines(root: Path) -> list[str]:
        return [line for line in _code(_read("hooks/stateline.ts", root)).splitlines() if line.strip()]

    assert lines(PLUGIN) == lines(BASE)
    # the tests only grow: every live test is kept verbatim
    assert _read("hooks/stateline.test.ts").startswith(_read("hooks/stateline.test.ts", BASE))


def test_stateline_hooks_prompt_submit_only_and_touches_no_network_or_process():
    code = _code(_read("hooks/stateline.ts"))
    assert re.findall(r"\bon\('([\w.]+)'", code) == ["prompt.submit"]
    calls = set(re.findall(r"\$\.(\w+)\.(\w+)\(", code))
    assert calls == {("env", "get"), ("clock", "now"), ("fs", "read")}
    for banned in ("fetch(", "$.network", "$.process", "import("):
        assert banned not in code


def test_off_by_default_the_allow_list_is_the_env_alone():
    code = _code(_read("hooks/stateline.ts"))
    assert "$.env.get('BD_STATELINE')" in code
    # membership is exact, never a prefix or substring of the seat name
    assert "allow.includes(seat)" in code
    assert not re.search(r"'bd-(?:worker|pm|agy)[\w-]*'", code)
    assert "BD_STATELINE" not in _read(".claude-plugin/plugin.json")


def test_the_line_is_bounded_at_200_bytes_and_prepended_to_the_prompt():
    code = _code(_read("hooks/stateline.ts"))
    assert "const MAX_BYTES = 200" in code
    # NUM has no length limit, so this comparison is the bound; the plugin test's 201 B case is what proves it
    assert "return new TextEncoder().encode(line).length > MAX_BYTES ? undefined : line" in code
    assert "next({ ...e, text: `${line}\\n${e.text}` })" in code


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH (COULD NOT LOOK)")
def test_candidate_plugin_tests_pass(tmp_path):
    work = tmp_path / "bd-guard"
    shutil.copytree(PLUGIN, work)
    r = subprocess.run(["claude", "plugin", "test", str(work)], capture_output=True, text=True, timeout=120, check=False)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert re.search(r"^\s*0 fail$", out, re.MULTILINE), out[-3000:]
    assert out.count("(pass) M7") == 20, out[-3000:]
    assert "(pass) M7 bound: a numeric loadavg that makes the line 201 B adds no line" in out, out[-3000:]
