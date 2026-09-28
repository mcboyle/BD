"""BH-bd-agy-audit-2-009: the bd-say PreToolUse gate refused a literal '$' ("Cost is $ 5", "regex a$", "\\$HOME").

BD_SAYHOOK2_CANDIDATE = cli.py under test (default: candidate). server is stubbed: validators record and pass.
Oracle: bash itself. Whenever the gate accepts a send, the (target, message) it validated must equal the argv
bash hands bd-say; a '$' bash expands must still be refused as a dynamic argument.
"""
BD_GATE_SCOPE = "module"
import importlib.util
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
CLI = Path(os.environ.get('BD_SAYHOOK2_CANDIDATE', HERE / 'candidate/cli.py'))
if not CLI.is_file():
    pytest.skip("candidate opt-in required", allow_module_level=True)


def load(path):
    stub = types.ModuleType('server')
    stub.premise_verify = stub.say_validate = lambda *a, **k: {'valid': True}
    stub.say_validate_batch = lambda items, *a, **k: [('ITEM', t, m) for t, m, _ in items]
    sys.modules['server'] = stub
    spec = importlib.util.spec_from_file_location(f'cli_{abs(hash(str(path)))}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = load(CLI)
SANDBOX = tempfile.mkdtemp(prefix='sayhook2-')  # bash redirections ('p$>q') write here, never the cwd


def gate(cmd):
    """('ok', [(target, message)...]) or ('refused', text)."""
    try:
        got = cli.hook(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': cmd}}))
    except ValueError as e:
        return 'refused', str(e)
    return 'ok', [(t, m) for _, t, m in got]


def bash_argv(cmd):
    """argv bash passes to every bd-say in cmd, as (target, message) pairs."""
    fn = 'bd-say(){ printf "%s\\0" "$#" "$@"; }; MSG=dyn; x=X; '
    out = subprocess.run(['env', '-i', 'HOME=/nonexistent', '/bin/bash', '--norc', '--noprofile', '-c', fn + cmd],
                         capture_output=True, timeout=10, check=False, cwd=SANDBOX).stdout.decode()
    parts = out.split('\0')[:-1]
    sends = []
    while parts:
        k = int(parts[0]); sends.append(tuple(parts[1:1 + k])); parts = parts[1 + k:]
    return sends


LITERAL = [
    'bd-say bd-pm-A "Cost is $ 5"',
    'bd-say bd-pm-A "regex a$"',
    'bd-say bd-pm-A "tail $"',
    'bd-say bd-pm-A "path $/x and $.y $, $: $= $% $+ $^ $] $} $~"',
    'bd-say bd-pm-A "esc \\$HOME and \\`id\\`"',
    'bd-say bd-pm-A esc\\$HOME',
    'bd-say bd-pm-A a$',
    'bd-say bd-pm-A "a$\'b"',
    'bd-say bd-pm-A \'single $HOME\'',
    'bd-say bd-pm-A "100$"',
    'bd-say bd-pm-A "x $\\"y"',
    'bd-say bd-pm-A "done $"; echo ok',
]
DYNAMIC = [
    'bd-say bd-pm-A "Cost is $0"',          # the finding's repro: bash sends "Cost is bash", not "$0"
    'bd-say bd-pm-A "$MSG"',
    'bd-say bd-pm-A "a ${x}"',
    'bd-say bd-pm-A "a $(id -u)"',
    'bd-say bd-pm-A "a $((1+2))"',
    'bd-say bd-pm-A "a $[1+2]"',
    'bd-say bd-pm-A "a $$ $? $# $@ $* $! $- $1"',
    'bd-say bd-pm-A "a `id -u`"',
    'bd-say bd-pm-A a$x',
    "bd-say bd-pm-A $'a\\x41'",
    'bd-say bd-pm-A $"a"',
    'bd-say $x "literal"',
    'bd-say bd-pm-A p$<q',  # bash: word 'p$' + redirect; the gate would validate one word 'p$<q'
    'bd-say bd-pm-A p$>q',
    'bd-say bd-pm-A "a \\\\$x"',            # escaped backslash, then a real expansion
    'bd-say bd-pm-A "a $é"',
]


@pytest.mark.parametrize('cmd', LITERAL)
def test_literal_dollar_passes_and_matches_bash(cmd):
    verdict, got = gate(cmd)
    assert verdict == 'ok', f'literal $ refused: {cmd!r}: {got}'
    assert got == bash_argv(cmd), f'gate validated {got!r}, bash sends {bash_argv(cmd)!r}'


@pytest.mark.parametrize('cmd', DYNAMIC)
def test_expanding_dollar_still_refused(cmd):
    verdict, got = gate(cmd)
    assert verdict == 'refused' and 'dynamic argument' in got, f'expansion passed unrefused: {cmd!r} -> {got!r}'


def test_dispatch_a_refusals_stay_refused():
    # The 5 dispatch-A sends refused 2026-09-28 each expand a variable ($F $S $P, loop $s) in a bd-say arg.
    for cmd in ('F=a.md; harness/bd-say bd-pm-A "HOLD /p/$F" 2>&1 | tail -3',
                'S=bd-kimi-worker-1; harness/bd-say $S "/p/x.md"',
                'P=/p; $P/harness/bd-say bd-agy-worker-1 "REFUTE $P/x.md"',
                'for s in a b; do bd-say $s "AMEND /p/x.md"; done'):
        assert gate(cmd)[0] == 'refused', cmd


def test_fuzz_accepted_equals_bash():
    """Any send the gate accepts is validated with exactly what bash passes; also count literal-$ acceptances."""
    rng = random.Random(9009)
    atoms = ['$', '$x', '$0', '$ ', '\\$', '\\\\', '\\"', '`', '\\`', "'", 'a', ' ', '{', '(', '%', '/', '.', ':', '-',
             '}', '#', '"', '$"', "$'", '${x}']
    accepted = mismatched = 0
    for _ in range(600):
        body = ''.join(rng.choice(atoms) for _ in range(rng.randint(1, 6)))
        for cmd in (f'bd-say bd-pm-A "{body}"', f'bd-say bd-pm-A {body}'):
            ref = bash_argv(cmd)
            verdict, got = gate(cmd)
            if verdict != 'ok' or len(ref) != 1 or len(ref[0]) != 2:
                continue
            accepted += 1
            if got != ref:
                mismatched += 1
                pytest.fail(f'gate accepted {cmd!r} as {got!r}; bash sends {ref!r}')
    assert accepted > 50 and mismatched == 0


def test_every_ascii_follower_matches_bash():
    """For each printable ASCII char c after '$': the gate accepts only if bash leaves '$c' literal."""
    checked = set()
    for c, quoted in itertools.product([chr(i) for i in range(32, 127)], (True, False)):
        if quoted and c in '"\\`':
            continue
        cmd = f'bd-say bd-pm-A "p${c}q"' if quoted else f'bd-say bd-pm-A p${c}q'
        verdict, got = gate(cmd)
        ref = bash_argv(cmd)
        if verdict == 'ok' and ref:  # ref == []: bash syntax error ('p$)q'), nothing is sent
            checked.add(c)
            assert got == ref, f'{cmd!r}: gate {got!r} != bash {ref!r}'
    # positive control: the literal followers were actually exercised in both contexts
    assert set(' %+,./:=^]}~') <= checked
