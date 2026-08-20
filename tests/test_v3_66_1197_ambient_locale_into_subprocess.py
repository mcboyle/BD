"""A test must not let the host's ambient locale collation decide its verdict.

Backlog row 178: ``test_v3_66_1192`` inherited the host ``LANG`` into the
installer subprocess, which globs ``bin/*`` under ``LC_COLLATE``. glibc en_US
collation folds out a leading underscore while C collation keeps it (0x5F <
0x62), so the first-staged file -- and the test's expected diagnostic -- flipped
with the launcher's locale: green on a C-collating launcher, red on a UTF-8 one,
same tree. The installer now sorts its glob under C (the root fix); this gate
stops the whole CLASS from recurring.

Invariant: any test that inherits ``os.environ`` into a child process's ``env``
must also pin ``LC_ALL`` to a C-family value in that env. ``LC_ALL`` -- not
``LC_COLLATE`` -- because an inherited ``LC_ALL`` from the launcher DOMINATES a
bare ``LC_COLLATE`` (glibc precedence), and these tests filter only ``BD_*``
keys, so a launcher ``LC_ALL`` survives into the child; only pinning ``LC_ALL``
itself is robust. ``LC_ALL=C`` forces C collation and always exists.

EVASION SURFACE, declared rather than hidden (this is a TEXTUAL gate, a proxy
for a runtime property, so it has one): it detects the ``os.environ.items()``
inheritance idiom handed via an ``env=`` kwarg to a ``subprocess.*`` or
``create_subprocess_*`` spawn, in any spacing. It does NOT catch: the
``os.environ.copy()`` / ``dict(os.environ)`` / ``**os.environ`` inheritance
forms (~60 other tests, whose subprocesses are locale-insensitive -- widen
``_INHERIT`` if one ever regresses); a bare ``from subprocess import run``; or a
pin syntactically present but attached to a dict never passed to the spawn. The
``test_detection_catches_the_known_evasions`` self-test below pins the spellings
this gate DOES catch, so a future weakening of detection fails loudly. The
authoritative protection for row 178's own case is the installer's command-scoped
``LC_ALL=C sort``; this gate is defense-in-depth against accidental regressions.
"""
from __future__ import annotations

import pathlib
import re

BD_GATE_SCOPE = "repo-wide"

_TESTS = pathlib.Path(__file__).resolve().parent

# The env-inheritance idiom this gate covers: a filtered child env built by
# iterating environ.items() (matches both ``os.environ.items()`` and a
# ``from os import environ`` alias). Whitespace-tolerant on every dot and paren
# so the detection is not itself defeated by spacing -- the same defect it exists
# to catch (CLAUDE.md A7: a fix must not reproduce the defect's shape).
_INHERIT = re.compile(r"environ\s*\.\s*items\s*\(\s*\)")
# An actual spawn call: subprocess.<method>( or [asyncio.]create_subprocess_*( .
# Named forms only, so this gate file -- which describes "subprocess" in prose --
# does not match itself.
_SPAWN = re.compile(
    r"(subprocess\s*\.\s*(run|Popen|call|check_output|check_call)"
    r"|(asyncio\s*\.\s*)?create_subprocess_(exec|shell))\s*\("
)
# An env= kwarg in any spacing (``env=x`` or ``env = x``).
_ENV_KWARG = re.compile(r"\benv\s*=")
# A C-family LC_ALL pin, in dict ("LC_ALL": "C"), subscript (e["LC_ALL"] = "C")
# or shell (LC_ALL=C) form. LC_ALL only -- LC_COLLATE alone is not robust.
_PIN = re.compile(r"""LC_ALL["']?\]?\s*[:=]\s*["']?(C|C\.UTF-8|C\.utf8)\b""")

# Files exempted with a documented reason (closed set; empty = all comply).
_EXEMPT: dict[str, str] = {}


def _strip_comments(src: str) -> str:
    """Source with trailing/whole-line comments removed, so a pin that appears
    only in a comment does not count as compliance. Line-based (not tokenized)
    so real code text stays contiguous -- ``e["LC_ALL"] = "C"`` must survive
    intact for the pin regex. A ``#`` inside a string literal is truncated too,
    which is harmless here: a pin is never written inside a string."""
    return "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())


def _is_hazard(src: str) -> bool:
    return (
        _INHERIT.search(src) is not None
        and _SPAWN.search(src) is not None
        and _ENV_KWARG.search(src) is not None
    )


def _is_pinned(src: str) -> bool:
    return _PIN.search(_strip_comments(src)) is not None


def _hazard_files() -> list[pathlib.Path]:
    # Exclude this gate's own file: its evasion-fixture strings contain the idiom
    # as TEST DATA, not as a real inherited-env subprocess, so scanning itself
    # would count a fixture as a hazard.
    self_name = pathlib.Path(__file__).name
    return [
        p for p in sorted(_TESTS.glob("test*.py"))
        if p.name != self_name and _is_hazard(p.read_text(encoding="utf-8"))
    ]


def test_env_inheriting_tests_pin_lc_all():
    hazards = _hazard_files()
    # Non-vacuity: the gate must see a population, and specifically its motivating
    # subject. Zero, or a population missing test_v3_66_1192, means detection
    # regressed -- broken, not satisfied.
    assert hazards, "gate found no env-inheriting tests -- detection is broken (vacuous)"
    names = {p.name for p in hazards}
    assert "test_v3_66_1192_live_path_defaults_are_portable.py" in names, (
        "the motivating subject (row 178) is not in the scanned hazard population -- "
        "env-inheritance detection has regressed"
    )
    violations = []
    for p in hazards:
        rel = f"tests/{p.name}"
        if rel in _EXEMPT:
            continue
        if not _is_pinned(p.read_text(encoding="utf-8")):
            violations.append(rel)
    assert not violations, (
        "these tests inherit os.environ into a subprocess env but do not pin "
        f"LC_ALL to a C-family value, so their verdict can depend on the host "
        f"locale (row 178's failure class): {sorted(violations)}. Add "
        '"LC_ALL": "C" to the subprocess env, or add the file to _EXEMPT with a reason.'
    )


def test_detection_catches_the_known_evasions():
    """Ship the evasion fixture with the gate: the spellings an adversarial pass
    found must stay caught, so a future weakening of _SPAWN/_ENV_KWARG/_INHERIT
    fails here rather than silently letting a hazard through."""
    base = "import os, subprocess, asyncio\nx = {k: v for k, v in os.environ.items()}\n"
    catches = {
        "spaced env=": base + "subprocess.run([], env = x)",
        "asyncio spawn": base + "asyncio.create_subprocess_exec('a', env=x)",
        "from-os-import alias": "from os import environ\nx = {k: v for k, v in environ.items()}\nsubprocess.run([], env=x)",
        "Popen form": base + "subprocess.Popen(['a'], env=x)",
        "space before items paren": "import os, subprocess\nx = {k: v for k, v in os.environ.items ()}\nsubprocess.run([], env=x)",
        "spaced subprocess dot": base + "subprocess . run([], env=x)",
        "newline-split items": "import os, subprocess\nx = {k: v for k, v in os.environ\n    .items()}\nsubprocess.run([], env=x)",
    }
    for label, snippet in catches.items():
        assert _is_hazard(snippet), f"detection MISSES a known hazard spelling: {label}"
    # a comment-only pin must NOT read as compliant
    assert not _is_pinned('subprocess.run([], env=x)  # LC_ALL: C is set elsewhere'), (
        "a pin appearing only in a comment counted as compliance"
    )
    # a real pin, in each accepted form, must read as compliant
    for pin in ('env = {"LC_ALL": "C"}', 'e["LC_ALL"] = "C"', 'LC_ALL=C'):
        assert _is_pinned(pin), f"a real LC_ALL pin was not recognised: {pin}"
    # LC_COLLATE alone must NOT satisfy the gate (it is defeated by inherited LC_ALL)
    assert not _is_pinned('e["LC_COLLATE"] = "C"'), "LC_COLLATE-only was accepted; it is not robust"
