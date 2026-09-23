"""Dynamic Shell Autocompletion Engine for Zsh, Bash, and Fish.

Provides introspective argument and command extraction from argparse CLI hierarchies
and generates dynamic shell autocompletion scripts and runtime token resolvers.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set


@dataclass
class OptionInfo:
    """Metadata for a CLI option flag."""
    flags: List[str]
    help: str = ""
    is_flag: bool = True
    choices: Optional[List[str]] = None
    metavar: Optional[str] = None


@dataclass
class CommandNode:
    """Represents a CLI command or subcommand hierarchy."""
    prog: str
    help: str = ""
    options: List[OptionInfo] = field(default_factory=list)
    subcommands: Dict[str, CommandNode] = field(default_factory=dict)
    positional_args: List[str] = field(default_factory=list)


class ShellCompletionEngine:
    """Dynamic Shell Autocompletion Engine for Zsh, Bash, and Fish.

    Dynamically introspects argparse.ArgumentParser command hierarchies to generate
    accurate, up-to-date autocompletion scripts and provide real-time runtime token
    completions without manual maintenance of hardcoded command lists.
    """

    def __init__(
        self,
        parser: Optional[argparse.ArgumentParser] = None,
        prog: str = "bdctl",
    ) -> None:
        self.prog = prog
        self.parser = parser
        self._tree: Optional[CommandNode] = None
        self._dynamic_completers: Dict[str, Callable[[], List[str]]] = {}
        if parser is not None:
            self._tree = self.introspect(parser, prog=prog)

    def register_dynamic_completer(self, arg_name: str, callback: Callable[[], List[str]]) -> None:
        """Register a dynamic completion provider for a specific argument or option."""
        self._dynamic_completers[arg_name] = callback

    def introspect(
        self,
        parser: Optional[argparse.ArgumentParser] = None,
        prog: Optional[str] = None,
    ) -> CommandNode:
        """Introspect an ArgumentParser hierarchy into a CommandNode tree."""
        p = parser or self.parser
        if p is None:
            raise ValueError("No parser provided for introspection")
        prog_name = prog or getattr(p, "prog", self.prog) or self.prog

        node = CommandNode(prog=prog_name, help=getattr(p, "description", "") or "")
        self._extract_node(p, node)
        self._tree = node
        return node

    def _extract_node(self, parser: argparse.ArgumentParser, node: CommandNode) -> None:
        for action in getattr(parser, "_actions", []):
            # Subparsers
            if isinstance(action, argparse._SubParsersAction):
                for sub_name, sub_parser in action.choices.items():
                    if sub_name.startswith("_"):
                        # Internal helpers (``_complete-sites``) are called by the
                        # scripts, never offered to the user.
                        continue
                    sub_help = getattr(sub_parser, "description", "") or getattr(sub_parser, "help", "") or ""
                    child_node = CommandNode(prog=sub_name, help=str(sub_help).strip())
                    self._extract_node(sub_parser, child_node)
                    node.subcommands[sub_name] = child_node
                continue

            if action.option_strings:
                # Option flags
                is_flag = action.nargs == 0 or isinstance(
                    action,
                    (
                        argparse._StoreTrueAction,
                        argparse._StoreFalseAction,
                        argparse._StoreConstAction,
                        getattr(argparse, "_VersionAction", ()),
                        getattr(argparse, "_HelpAction", ()),
                    ),
                )
                choices = [str(c) for c in action.choices] if action.choices else None
                node.options.append(
                    OptionInfo(
                        flags=list(action.option_strings),
                        help=(action.help or "").strip(),
                        is_flag=is_flag,
                        choices=choices,
                        metavar=action.metavar if isinstance(action.metavar, str) else None,
                    )
                )
            else:
                # Positional
                if action.dest and action.dest != argparse.SUPPRESS:
                    node.positional_args.append(action.dest)

    def get_tree(self) -> CommandNode:
        """Get the cached CommandNode tree, introspecting the parser if necessary."""
        if self._tree is None:
            if self.parser is not None:
                self._tree = self.introspect(self.parser, prog=self.prog)
            else:
                raise ValueError("Parser not introspected yet")
        return self._tree

    def all_subcommands(self) -> List[str]:
        """Return all top-level subcommands."""
        tree = self.get_tree()
        return sorted(tree.subcommands.keys())

    def all_options(self) -> List[str]:
        """Return all distinct option flags across the parser."""
        tree = self.get_tree()
        flags: Set[str] = set()

        def _walk(n: CommandNode):
            for opt in n.options:
                flags.update(opt.flags)
            for sub in n.subcommands.values():
                _walk(sub)

        _walk(tree)
        return sorted(flags)

    def complete_command_line(self, line: str, point: Optional[int] = None) -> List[str]:
        """Dynamically resolve autocompletion candidates for a given command-line prefix."""
        tree = self.get_tree()
        prefix = line[:point] if point is not None else line
        tokens = prefix.split()
        ends_with_space = prefix.endswith(" ")

        # Remove root prog name if present
        if tokens and tokens[0] == tree.prog:
            tokens = tokens[1:]

        curr_node = tree
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            is_last = (idx == len(tokens) - 1)

            if is_last and not ends_with_space:
                # Completing current token 'tok'
                if tok.startswith("-"):
                    opts: List[str] = []
                    for opt in curr_node.options:
                        for flag in opt.flags:
                            if flag.startswith(tok):
                                opts.append(flag)
                    return sorted(set(opts))
                else:
                    subs = [s for s in curr_node.subcommands if s.startswith(tok)]
                    return sorted(subs)
            else:
                # tok is fully entered
                if tok in curr_node.subcommands:
                    curr_node = curr_node.subcommands[tok]
                idx += 1

        # Starting a new token
        candidates: List[str] = []
        if curr_node.subcommands:
            candidates.extend(curr_node.subcommands.keys())
        for opt in curr_node.options:
            for flag in opt.flags:
                candidates.append(flag)
        return sorted(set(candidates))

    def generate_completion_script(self, shell: str) -> str:
        """Generate autocompletion script for the requested shell (bash, zsh, fish)."""
        s = shell.lower().strip()
        if s == "bash":
            return self._generate_bash()
        elif s == "zsh":
            return self._generate_zsh()
        elif s == "fish":
            return self._generate_fish()
        else:
            raise ValueError(f"Unsupported shell: {shell}. Supported: bash, zsh, fish")

    def command_paths(self) -> List[tuple]:
        """Every (path, words) pair in the tree: path is the space-joined command
        chain ("" for the root), words its subcommands followed by its flags."""
        out: List[tuple] = []

        def _walk(n: CommandNode, path: str) -> None:
            words = sorted(n.subcommands)
            for opt in n.options:
                words.extend(f for f in opt.flags if f not in words)
            out.append((path, words))
            for name in sorted(n.subcommands):
                _walk(n.subcommands[name], f"{path} {name}".strip())

        _walk(self.get_tree(), "")
        return out

    def _has_site_option(self) -> bool:
        return "--site" in self.all_options()

    def _generate_bash(self) -> str:
        prog = self.prog
        paths = self.command_paths()
        known = "|".join(f'"{p}"' for p, _ in paths if p)
        arms = "\n".join(
            f'    "{p}") words="{" ".join(w)}" ;;' for p, w in paths
        )
        site = ""
        if self._has_site_option():
            site = f"""
  if [ "$prev" = "--site" ]; then
    COMPREPLY=( $(compgen -W "$({prog} _complete-sites 2>/dev/null)" -- "$cur") )
    return
  fi
"""
        return f"""# Bulk Downloader {prog} completion for bash
# Install:  source <({prog} completion bash)
# Or permanently:
#   {prog} completion bash > ~/.local/share/bash-completion/completions/{prog}
# Generated from the {prog} argument parser; regenerate after upgrading.
_{prog}_complete() {{
  local cur prev cpath w i words
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  prev="${{COMP_WORDS[COMP_CWORD-1]}}"
{site}
  cpath=""
  for ((i=1; i<COMP_CWORD; i++)); do
    w="${{COMP_WORDS[i]}}"
    case "$w" in -*) continue ;; esac
    case "${{cpath:+$cpath }}$w" in
      {known}) cpath="${{cpath:+$cpath }}$w" ;;
    esac
  done

  words=""
  case "$cpath" in
{arms}
  esac
  COMPREPLY=( $(compgen -W "$words" -- "$cur") )
}}
complete -F _{prog}_complete {prog}
"""

    def _generate_zsh(self) -> str:
        # ``path`` is tied to $PATH in zsh; the chain variable is ``cpath``.
        prog = self.prog
        paths = self.command_paths()
        known = "|".join(f'"{p}"' for p, _ in paths if p)
        arms = "\n".join(
            f'    ("{p}") compadd -- {" ".join(w)} ;;' for p, w in paths
        )
        site = ""
        if self._has_site_option():
            site = f"""
  if [[ "$prev" == "--site" ]]; then
    local -a sites
    sites=("${{(@f)$({prog} _complete-sites 2>/dev/null)}}")
    compadd -a sites
    return
  fi
"""
        return f"""#compdef {prog}
# Bulk Downloader {prog} completion for zsh
# Install:  source <({prog} completion zsh)
# Or permanently, add to your fpath as `_{prog}`.
# Generated from the {prog} argument parser; regenerate after upgrading.

_{prog}() {{
  local prev cpath w i
  prev="${{words[CURRENT-1]}}"
{site}
  cpath=""
  for ((i=2; i<CURRENT; i++)); do
    w="${{words[i]}}"
    [[ "$w" == -* ]] && continue
    case "${{cpath:+$cpath }}$w" in
      ({known}) cpath="${{cpath:+$cpath }}$w" ;;
    esac
  done

  case "$cpath" in
{arms}
  esac
}}

compdef _{prog} {prog}
"""

    def _generate_fish(self) -> str:
        tree = self.get_tree()
        prog = self.prog
        top_cmds = sorted(tree.subcommands.keys())

        lines = [
            f"# Bulk Downloader {prog} completion for fish",
            f"# Install:  {prog} completion fish > ~/.config/fish/completions/{prog}.fish",
            f"# Generated from the {prog} argument parser; regenerate after upgrading.",
            f"complete -c {prog} -f",
            f"complete -c {prog} -n '__fish_use_subcommand' -a '{' '.join(top_cmds)}'",
        ]
        for cmd_name in top_cmds:
            h = tree.subcommands[cmd_name].help.replace("'", "\\'")
            if h:
                lines.append(f"complete -c {prog} -n '__fish_use_subcommand' -a '{cmd_name}' -d '{h}'")

        def _opt_lines(node: CommandNode, cond: str) -> None:
            for opt in node.options:
                for flag in opt.flags:
                    if flag.startswith("--"):
                        spec = f"-l {flag[2:]}"
                    elif len(flag) == 2:
                        spec = f"-s {flag[1:]}"
                    else:
                        spec = f"-o {flag[1:]}"
                    extra = ""
                    if flag == "--site":
                        extra = f" -x -a '({prog} _complete-sites 2>/dev/null)'"
                    lines.append(f"complete -c {prog} -n '{cond}' {spec}{extra}")

        _opt_lines(tree, "__fish_use_subcommand")
        for cmd_name in top_cmds:
            node = tree.subcommands[cmd_name]
            cond = f"__fish_seen_subcommand_from {cmd_name}"
            if node.subcommands:
                sub_names = " ".join(sorted(node.subcommands.keys()))
                lines.append(f"complete -c {prog} -n '{cond}' -a '{sub_names}'")
            _opt_lines(node, cond)
            for sub_name in sorted(node.subcommands):
                _opt_lines(node.subcommands[sub_name],
                           f"{cond}; and __fish_seen_subcommand_from {sub_name}")

        return "\n".join(lines) + "\n"


def generate_shell_completion(
    parser_or_engine: Any,
    shell: str,
    prog: str = "bdctl",
) -> str:
    """Generate shell autocompletion script dynamically for bash, zsh, or fish."""
    if isinstance(parser_or_engine, ShellCompletionEngine):
        engine = parser_or_engine
    else:
        engine = ShellCompletionEngine(parser=parser_or_engine, prog=prog)
    return engine.generate_completion_script(shell)
