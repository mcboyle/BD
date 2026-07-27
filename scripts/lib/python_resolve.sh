# shellcheck shell=bash
# Shared Python-interpreter resolver. SOURCE this; it has no main and no side
# effects at file scope.
#
# Why it exists: tools/sast.sh and tools/dast.sh each carried their own ladder,
# and both probed `$REPO_DIR/.venv/bin/python` -- a path that does not exist in
# this repo -- then fell through to bare `python3`. In the cloud container that
# is Python 3.11 WITHOUT the project dependencies, while `venv/bin/python` is
# 3.12, the box/CI interpreter. Measured before this fragment existed: both
# scripts selected /usr/local/bin/python3 -> Python 3.11.15.
#
# A ladder is a denominator. A rung missing from it is a candidate the script
# can never choose, and the failure is silent: the script runs, under the wrong
# interpreter, and reports success.
#
# Deliberately no `set -e` / `set -u` at file scope -- those would leak into
# whatever sources this. Deliberately no BD_* names for internals, so the
# config-surface scanner is not asked to ledger locals; the ONE exported name is
# BD_PYTHON_RESOLVED, which is a real operator-visible output, plus BD_PYTHON as
# an operator override read from the environment.

# bd_resolve_python <work-tree> [required-major.minor]
#
# Sets BD_PYTHON_RESOLVED to an absolute interpreter path and returns 0.
# Returns 1 WITHOUT setting it if nothing suitable was found -- callers must
# treat that as fatal. Falling back to "whatever python is on PATH" is the bug
# this function exists to remove; unknown is a third state and it fails.
bd_resolve_python() {
    local work="${1:-$PWD}"
    local want="${2:-3.12}"
    local candidate
    BD_PYTHON_RESOLVED=""

    # $BD_PYTHON first: an explicit operator decision outranks any probe.
    # `venv` before `.venv` because `venv` is what this repo builds; `.venv` is
    # kept only because the WSL Codex box uses it (see CODEX_HANDOFF.md), and a
    # rung that costs nothing should not be removed just because it is unused
    # here.
    # NO system-interpreter rung, deliberately. A system python3.12 has the
    # right VERSION but not the project's DEPENDENCIES, and the question this
    # function answers is "which interpreter belongs to this work tree" -- for
    # which a system Python is not a worse answer, it is an answer to a
    # different question. Accepting it would reproduce the original bug in a
    # subtler form: tools that only walk the AST would appear to work, and
    # anything importing flask would fail somewhere far from here.
    # $BD_PYTHON is the escape hatch for a tree whose venv lives elsewhere.
    for candidate in \
        "${BD_PYTHON:-}" \
        "$work/venv/bin/python" \
        "$work/.venv/bin/python"
    do
        [ -n "$candidate" ] || continue
        [ -x "$candidate" ] || continue
        if "$candidate" -c "import sys; raise SystemExit(0 if '.'.join(map(str, sys.version_info[:2])) == '$want' else 1)" 2>/dev/null; then
            BD_PYTHON_RESOLVED="$candidate"
            return 0
        fi
    done

    # Nothing matched the required version. Say what was wanted and stop --
    # returning a wrong-version interpreter is how the 3.11 selections happened.
    echo "bd_resolve_python: no Python $want venv found for work tree '$work'" >&2
    echo "  probed: \$BD_PYTHON, $work/venv/bin/python, $work/.venv/bin/python" >&2
    echo "  build one (python3.12 -m venv venv) or set BD_PYTHON explicitly." >&2
    return 1
}
