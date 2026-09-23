"""Row 1037: Dynamic Shell Autocompletion Engine for Zsh, Bash, and Fish.

Validates:
(1) ShellCompletionEngine providing dynamic command line introspection and shell script generation.
(2) Generation of dynamic autocompletion scripts for Bash, Zsh, and Fish.
(3) Dynamic runtime completion resolving for command line tokens and nested subcommands.
(4) Integration with bdctl CLI parser and completion command.

RED on baseline: fails with explicit semantic AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import argparse
import sys
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import shell_completion
except ImportError:
    shell_completion = None


def test_positive_control_cli_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline CLI completion."""
    import bdctl

    assert hasattr(bdctl, "cmd_completion")
    assert callable(bdctl.cmd_completion)
    assert hasattr(bdctl, "cmd_complete_sites")
    assert callable(bdctl.cmd_complete_sites)


def test_shell_completion_engine_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    import bdctl

    assert shell_completion is not None, (
        "Row 1037 capability missing: Dynamic Shell Autocompletion Engine for Zsh, Bash, and Fish "
        "not implemented in bulk_downloader.shell_completion"
    )
    assert hasattr(shell_completion, "ShellCompletionEngine"), (
        "Row 1037 component missing: bulk_downloader.shell_completion.ShellCompletionEngine"
    )
    assert hasattr(shell_completion, "generate_shell_completion"), (
        "Row 1037 component missing: bulk_downloader.shell_completion.generate_shell_completion"
    )
    assert hasattr(bdctl, "build_parser"), (
        "Row 1037 caller missing: bdctl.build_parser"
    )


def test_shell_completion_parser_introspection():
    """Verify ShellCompletionEngine inspects ArgumentParser hierarchies and extracts commands and options."""
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl
    from bulk_downloader.shell_completion import ShellCompletionEngine

    parser = bdctl.build_parser()
    engine = ShellCompletionEngine(parser=parser, prog="bdctl")

    tree = engine.introspect()
    assert tree is not None
    assert tree.prog == "bdctl"
    # Verify top-level commands extracted
    top_cmds = set(tree.subcommands.keys())
    assert "status" in top_cmds
    assert "library" in top_cmds
    assert "site" in top_cmds
    assert "token" in top_cmds

    # Verify nested subcommands extracted
    lib_node = tree.subcommands["library"]
    assert "scan" in lib_node.subcommands
    assert "stats" in lib_node.subcommands

    site_node = tree.subcommands["site"]
    assert "export" in site_node.subcommands
    assert "import" in site_node.subcommands
    assert "validate" in site_node.subcommands

    # Verify options extracted
    assert any("--site" in opt.flags for opt in tree.subcommands["status"].options)


def test_shell_completion_bash_generation():
    """Verify dynamic Bash completion script generation."""
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl
    from bulk_downloader.shell_completion import ShellCompletionEngine

    engine = ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")
    script = engine.generate_completion_script("bash")

    assert "# Bulk Downloader bdctl completion for bash" in script
    assert "_bdctl_complete()" in script
    assert "complete -F _bdctl_complete bdctl" in script
    assert "status" in script
    assert "library" in script
    assert "site" in script


def test_shell_completion_zsh_generation():
    """Verify dynamic Zsh completion script generation."""
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl
    from bulk_downloader.shell_completion import ShellCompletionEngine

    engine = ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")
    script = engine.generate_completion_script("zsh")

    assert "#compdef bdctl" in script
    assert "_bdctl()" in script
    assert "status" in script
    assert "library" in script
    assert "compdef _bdctl bdctl" in script


def test_shell_completion_fish_generation():
    """Verify dynamic Fish completion script generation."""
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl
    from bulk_downloader.shell_completion import ShellCompletionEngine

    engine = ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")
    script = engine.generate_completion_script("fish")

    assert "complete -c bdctl" in script
    assert "__fish_use_subcommand" in script
    assert "status" in script
    assert "library" in script


def test_shell_completion_runtime_resolver():
    """Verify runtime completion token resolution across subcommand paths."""
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl
    from bulk_downloader.shell_completion import ShellCompletionEngine

    engine = ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")

    # Empty command line prefix -> top-level subcommands
    c_empty = engine.complete_command_line("bdctl ")
    assert "status" in c_empty
    assert "library" in c_empty
    assert "site" in c_empty

    # Partial command match
    c_stat = engine.complete_command_line("bdctl stat")
    assert c_stat == ["status"]

    # Subcommand options / nested subcommands
    c_lib = engine.complete_command_line("bdctl library ")
    assert "scan" in c_lib
    assert "stats" in c_lib
    assert "scan-status" in c_lib

    # Option flags
    c_opts = engine.complete_command_line("bdctl status --")
    assert "--site" in c_opts
    assert "--json" in c_opts


# --- REFUTE N6-A E1-E4: completions are the parser's, end to end ---

import io
import os
import shutil
import stat
import subprocess
import tempfile
import types
from contextlib import redirect_stdout


def _parser_paths():
    """(path, subcommands, flags) for every node of bdctl.build_parser(), walked
    straight off argparse -- independent of the engine under test."""
    import bdctl

    out = []

    def _walk(p, path):
        subs, flags = [], []
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for name, sp in a.choices.items():
                    if not name.startswith("_"):
                        subs.append((name, sp))
            flags.extend(a.option_strings)
        out.append((" ".join(path), sorted(n for n, _ in subs), flags))
        for name, sp in subs:
            _walk(sp, path + [name])

    _walk(bdctl.build_parser(), [])
    return out


def _bash_complete(script, lines, env_path=None):
    """Source the generated script in a real bash and return COMPREPLY per line."""
    assert shutil.which("bash"), "bash not available"
    body = [script]
    for line in lines:
        words = line.split(" ")
        arr = " ".join(f"'{w}'" for w in words)
        body.append(f"COMP_WORDS=({arr}); COMP_CWORD={len(words) - 1}; COMPREPLY=();"
                    f" _bdctl_complete; echo \"@@${{COMPREPLY[*]}}\"")
    env = dict(os.environ)
    if env_path:
        env["PATH"] = env_path + os.pathsep + env["PATH"]
    r = subprocess.run(["bash", "--norc", "--noprofile", "-c", "\n".join(body)],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    got = [l[2:].split() for l in r.stdout.splitlines() if l.startswith("@@")]
    assert len(got) == len(lines), r.stdout + r.stderr
    return got


def _script(shell):
    import bdctl

    return shell_completion.generate_shell_completion(bdctl.build_parser(), shell, prog="bdctl")


def test_e1_bash_offers_exactly_the_parser_words_at_every_command_path():
    assert shell_completion is not None, "shell_completion capability missing"
    paths = _parser_paths()
    lines = [" ".join(["bdctl"] + (p.split() if p else []) + [""]) for p, _, _ in paths]
    got = _bash_complete(_script("bash"), lines)
    for (path, subs, flags), words in zip(paths, got):
        assert sorted(words) == sorted(set(subs) | set(flags)), (
            f"bash completion at {path or '<top>'!r} disagrees with build_parser(): "
            f"extra={sorted(set(words) - set(subs) - set(flags))} "
            f"missing={sorted((set(subs) | set(flags)) - set(words))}")
    by_path = dict(zip([p for p, _, _ in paths], got))
    assert {"find", "scan", "status"} <= set(by_path["dedup"]) and "match" not in by_path["dedup"]
    assert "available" in by_path["scrapers"] and "remove" not in by_path["scrapers"]


def test_e3_bash_matches_by_prefix_and_skips_option_words():
    assert shell_completion is not None, "shell_completion capability missing"
    got = _bash_complete(_script("bash"), [
        "bdctl sta", "bdctl library --json sc", "bdctl library scan --r", "bdctl nosuch "])
    assert sorted(got[0]) == ["start", "status"]
    assert sorted(got[1]) == ["scan", "scan-status"]
    assert got[2] == ["--root"]
    top_words = {w for p, s, f in _parser_paths() if p == "" for w in s + f}
    assert set(got[3]) == top_words, "an unknown word must not change the command path"


def test_e3_runtime_resolver_filters_options_by_prefix():
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl

    engine = shell_completion.ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")
    assert engine.complete_command_line("bdctl status --j") == ["--json"]
    assert engine.complete_command_line("bdctl library scan --w") == ["--wait"]


def test_e1_site_values_come_from_complete_sites():
    assert shell_completion is not None, "shell_completion capability missing"
    d = tempfile.mkdtemp(prefix="row1037_")
    fake = os.path.join(d, "bdctl")
    with open(fake, "w") as fh:
        fh.write('#!/bin/sh\n[ "$1" = "_complete-sites" ] && printf "alpha\\nbeta\\n"\n')
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    got = _bash_complete(_script("bash"), ["bdctl status --site a"], env_path=d)
    assert got[0] == ["alpha"]


def test_e1_zsh_and_fish_derive_every_list_from_the_parser():
    assert shell_completion is not None, "shell_completion capability missing"
    zsh, fish = _script("zsh"), _script("fish")
    for path, subs, flags in _parser_paths():
        words = subs + [f for f in flags if f not in subs]
        assert f'("{path}") compadd -- {" ".join(words)} ;;' in zsh, f"zsh arm for {path!r}"
        if path and " " not in path and subs:
            assert (f"complete -c bdctl -n '__fish_seen_subcommand_from {path}' "
                    f"-a '{' '.join(subs)}'") in fish, f"fish subcommands for {path!r}"
    top_flags = next(f for p, _, f in _parser_paths() if p == "")
    assert "--json" not in top_flags
    assert '("") compadd -- ' in zsh and "--json" not in zsh.split('("") compadd -- ')[1].split("\n")[0]


def test_e4_internal_commands_are_never_offered():
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl

    engine = shell_completion.ShellCompletionEngine(parser=bdctl.build_parser(), prog="bdctl")
    assert "_complete-sites" not in engine.complete_command_line("bdctl ")
    assert engine.complete_command_line("bdctl _") == []
    assert "_complete-sites" not in _bash_complete(_script("bash"), ["bdctl "])[0]
    assert '"_complete-sites"' not in _script("zsh")
    fish_offers = [l for l in _script("fish").splitlines() if "__fish_use_subcommand' -a" in l]
    assert fish_offers and not any("_complete-sites" in l for l in fish_offers)


def test_e2_cmd_completion_emits_the_generated_script():
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl

    for shell in ("bash", "zsh", "fish"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            bdctl.cmd_completion(types.SimpleNamespace(shell=shell))
        assert buf.getvalue() == _script(shell), f"bdctl completion {shell} is not the generated script"


def test_e2_cmd_completion_does_not_hide_a_generator_failure(monkeypatch):
    assert shell_completion is not None, "shell_completion capability missing"
    import bdctl

    def boom(*a, **k):
        raise RuntimeError("row1037-generator-broken")

    monkeypatch.setattr(shell_completion, "generate_shell_completion", boom)
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(RuntimeError, match="row1037-generator-broken"):
        bdctl.cmd_completion(types.SimpleNamespace(shell="bash"))
    assert buf.getvalue() == "", "a stale fallback script was printed"
