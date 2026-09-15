"""Explicit authorization injection for legacy tool-driver tests.

The cut-quality receipt protocol has its own direct tests.  Legacy drivers
exercise behavior after that boundary, so they inject only ``enforce`` while
leaving argument registration and every downstream production seam real.
"""

from __future__ import annotations

from pathlib import Path
import sys


def authorize_module(module):
    """Authorize one loaded tool module and retain an exact call census."""
    calls = []

    def allow(*args, **kwargs):
        calls.append((args, kwargs))
        return calls

    module.cut_quality.enforce = allow
    module._legacy_cut_quality_calls = calls
    return module


def raw_module(module):
    """Mutation target: leave a loaded protected tool unauthorized."""
    module._legacy_cut_quality_calls = []
    return module


def authorized_tool_argv(tool: Path, *args: str) -> list[str]:
    """Return argv that loads a real tool, injects authorization, and runs it."""
    loader = (
        "import importlib.machinery,importlib.util,pathlib,sys;"
        "p=pathlib.Path(sys.argv.pop(1));sys.path.insert(0,str(p.parent));"
        "l=importlib.machinery.SourceFileLoader('legacy_authorized_uut',str(p));"
        "s=importlib.util.spec_from_loader(l.name,l);"
        "m=importlib.util.module_from_spec(s);l.exec_module(m);"
        "m.cut_quality.enforce=lambda *a,**k:a or k;"
        "raise SystemExit(m.main(sys.argv[1:]))"
    )
    return [sys.executable, "-c", loader, str(tool), *args]


def raw_tool_argv(tool: Path, *args: str) -> list[str]:
    """Mutation target: invoke a protected CLI with no permit injection."""
    return [sys.executable, str(tool), *args]


def write_authorized_cut_quality_stub(directory: Path) -> Path:
    """Supply copied-tool fixtures with an explicit post-permit test boundary."""
    target = directory / "bd_cut_quality.py"
    target.write_text(
        "def add_permit_argument(parser):\n"
        "    parser.add_argument('--cut-quality-permit')\n\n"
        "def enforce(*args, **kwargs):\n"
        "    return args or kwargs\n",
        encoding="utf-8",
    )
    return target


def write_refusing_cut_quality_stub(directory: Path) -> Path:
    """Mutation target: keep copied tools importable but refuse authorization."""
    target = directory / "bd_cut_quality.py"
    target.write_text(
        "def add_permit_argument(parser):\n"
        "    parser.add_argument('--cut-quality-permit')\n\n"
        "def enforce(*args, **kwargs):\n"
        "    return ()\n",
        encoding="utf-8",
    )
    return target
