"""bd_mod3_env_persist must write a DSN that `. mod3.env` reads back verbatim
and never executes (o1671 a15).

The env file is sourced by bash (capture.sh). An unquoted or dq-wrapped DSN
lets `$(..)`, a backtick, `;`, `|` or a newline run code at source time. Each
case plants a canary command in the DSN and asserts the canary file is absent
after persist AND after source, and that the value round-trips byte-exact.
"""

import pathlib
import subprocess

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "lib" / "dev_capabilities.sh"

# {canary} is replaced with the absolute canary path for the case.
DSN_TEMPLATES = {
    "cmdsub": "postgresql://u:p$(touch {canary})@127.0.0.1/db",
    "backtick": "postgresql://u:p`touch {canary}`@127.0.0.1/db",
    "semicolon": "postgresql://u:p;touch {canary};@127.0.0.1/db",
    "pipe": "postgresql://u:p|touch {canary}|@127.0.0.1/db",
    "newline": "postgresql://u:p\ntouch {canary}\n@127.0.0.1/db",
    "ampersand": "postgresql://u:p&touch {canary}&@127.0.0.1/db",
    "squote_cmdsub": "postgresql://u:p'$(touch {canary})'@127.0.0.1/db",
    "dquote_cmdsub": 'postgresql://u:p"$(touch {canary})"@127.0.0.1/db',
}


@pytest.mark.parametrize("case", sorted(DSN_TEMPLATES))
def test_devcap_dsn_round_trips_and_never_executes(tmp_path, case):
    canary = tmp_path / f"PWN-{case}"
    dsn = DSN_TEMPLATES[case].format(canary=canary)
    home = tmp_path / "home"
    home.mkdir()

    persist = subprocess.run(
        ["bash", "-c", '. "$1" && bd_mod3_env_persist', "persist", str(SCRIPT_PATH)],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "MOD3_DSN": dsn, "SUDO": ""},
        cwd=tmp_path,
    )
    assert persist.returncode == 0, (
        f"persist rc={persist.returncode} stderr={persist.stderr!r}"
    )
    assert not canary.exists(), f"{case}: canary executed during persist"

    env_path = home / ".config" / "bd" / "mod3.env"
    assert env_path.is_file(), "mod3.env was not written"

    read = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1" && printf %s "$MOD3_PG_TEST_DSN"',
            "reader",
            str(env_path),
        ],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        cwd=tmp_path,
    )
    assert not canary.exists(), (
        f"{case}: sourcing mod3.env EXECUTED the DSN; file={env_path.read_bytes()!r}"
    )
    assert read.returncode == 0, f"source rc={read.returncode} stderr={read.stderr!r}"
    assert read.stdout == dsn.encode(), (
        f"{case}: round trip differs: got {read.stdout!r} want {dsn.encode()!r}"
    )
