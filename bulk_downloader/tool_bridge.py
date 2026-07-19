"""tool_bridge -- the ONE validated, allowlisted exec bridge (v3.66.717, Cut 7).

Until now tools_exec_bridged = 0: no code path took a GUI value and passed it as an
argument to a tool, which is why 739 CLI flags could not be exposed incrementally --
there was nothing to hang a control on. This is the seam. It is deliberately the ONLY
one, and it is built to refuse.

DESIGN (safe-default = DENY):

  * ALLOWLIST is DATA. {tool_name: {argv0, flags:{name: spec}, ...}}. A tool not in it
    cannot run; a flag not in its entry cannot be passed. There are no dynamic branches
    that could grow a capability by accident.
  * argv0 is a RESOLVED ABSOLUTE PATH captured at import from the allowlist, never taken
    from the request. The request names a tool by KEY; it never supplies a path.
  * NO SHELL. subprocess with a list argv and shell=False. _USES_SHELL is a module
    constant pinned False by a test -- a shell payload in a flag value is an inert argv
    element, never interpreted. Even so, values are validated first, so the payload is
    usually refused before it becomes an argument at all.
  * every flag value is TYPE/RANGE validated against its spec: bool (flag present/absent,
    no value on the wire), int (bounded), enum (member of a fixed set), str (bounded
    length, no NUL, no control chars), path (must resolve UNDER an allowed root -- no
    traversal, no absolute escape).
  * hard TIMEOUT and output SIZE CAP: a tool cannot hang the request or flood the caller.
  * CSRF-gated at the route like every mutating endpoint.

Adding a tool = adding a reviewed dict entry. Adding a capability is a diff to DATA, not
a new code path -- which is the whole point of doing it once, centrally, paranoid.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# NEVER flip this. Pinned False by test_bridge_never_uses_a_shell. A True here would turn
# every validated argv element back into shell-interpretable text.
_USES_SHELL = False

TIMEOUT_S = 30
MAX_OUTPUT_BYTES = 256 * 1024  # 256 KiB per stream; enough for --version / a report

# Path-typed flag values must resolve UNDER one of these roots. Nothing else.
_ALLOWED_PATH_ROOTS = (
    os.environ.get("BD_HOME") or str(Path.home() / "BulkDownloader"),
    "/tmp",
)

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _resolve(binary: str) -> str | None:
    """Absolute path to a binary, or None. Captured at import -- the REQUEST never
    supplies a path, only a tool key."""
    p = shutil.which(binary)
    return p if p and os.path.isabs(p) else None


# ── THE ALLOWLIST (data) ──────────────────────────────────────────────────────
# Start SMALL and read-only. yt-dlp and ffprobe are the download/probe engines an
# operator most wants to interrogate, and both have no side effects in the modes
# exposed here. Every flag is enumerated with a validation spec. smoke=True marks a
# call safe to run in a test (no network, no writes).
def _build_allowlist() -> dict:
    al: dict = {}

    ytdlp = _resolve("yt-dlp")
    if ytdlp:
        al["yt-dlp"] = {
            "argv0": ytdlp,
            "desc": "The yt-dlp download engine (read-only introspection only).",
            "flags": {
                "--version": {"type": "bool"},
                "--list-extractors": {"type": "bool"},
                "--dump-json": {"type": "bool"},
                # a URL to introspect; str, bounded, no control chars. Note: this does
                # NOT download -- pair it with --dump-json / --simulate at the call site.
                "--simulate": {"type": "bool"},
                "url": {"type": "str", "positional": True, "max_len": 2048,
                        "scheme": ["http", "https"]},
            },
            "smoke": True,
            "smoke_flags": {"--version": True},
        }

    ffprobe = _resolve("ffprobe")
    if ffprobe:
        al["ffprobe"] = {
            "argv0": ffprobe,
            "desc": "ffprobe media inspection (read-only).",
            "flags": {
                "-version": {"type": "bool"},
                "-hide_banner": {"type": "bool"},
                "-loglevel": {"type": "enum",
                              "choices": ["quiet", "error", "warning", "info"]},
                "input": {"type": "path", "positional": True},
            },
            "smoke": True,
            "smoke_flags": {"-version": True},
        }

    return al


ALLOWLIST = _build_allowlist()


# ── validation ────────────────────────────────────────────────────────────────
class BridgeError(ValueError):
    """A validation failure -> HTTP 400. The message is safe to return."""


def _validate_value(flag: str, spec: dict, value):
    t = spec["type"]
    if t == "bool":
        if value not in (True, False, None, "", "true", "false"):
            raise BridgeError("flag %s is a boolean; it takes no value" % flag)
        return None  # bool flags contribute the flag token only, never a value
    if t == "int":
        try:
            iv = int(value)
        except (TypeError, ValueError):
            raise BridgeError("flag %s expects an integer" % flag)
        lo, hi = spec.get("min", 0), spec.get("max", 2**31)
        if not (lo <= iv <= hi):
            raise BridgeError("flag %s out of range [%s,%s]" % (flag, lo, hi))
        return str(iv)
    if t == "enum":
        if value not in spec["choices"]:
            raise BridgeError("flag %s must be one of %s" % (flag, spec["choices"]))
        return str(value)
    if t == "str":
        sv = "" if value is None else str(value)
        if len(sv) > spec.get("max_len", 512):
            raise BridgeError("flag %s value too long" % flag)
        if _CTRL.search(sv):
            raise BridgeError("flag %s value has control characters" % flag)
        # v3.66.747 (audit R14) -- OPTION-INJECTION GUARDS on positional values.
        # A positional whose value looks like an option is passed straight to
        # the tool as one (build_argv also inserts a `--` separator, but this is
        # the belt to that suspenders -- refuse the value, don't just fence it).
        if spec.get("positional") and sv.startswith("-"):
            raise BridgeError(
                "flag %s positional value may not begin with '-' "
                "(option-injection guard)" % flag)
        # A scheme allowlist for URL-bearing positionals mirrors the rigor the
        # `path` type already has. Opt-in via spec["scheme"]; a positional
        # declaring a scheme list is a URL and nothing else is accepted.
        schemes = spec.get("scheme")
        if schemes:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(sv)
            except (ValueError, TypeError):
                raise BridgeError("flag %s is not a valid URL" % flag)
            if parsed.scheme.lower() not in schemes or not parsed.netloc:
                raise BridgeError(
                    "flag %s must be a URL with scheme in %s" % (flag, schemes))
        return sv
    if t == "path":
        sv = "" if value is None else str(value)
        if _CTRL.search(sv):
            raise BridgeError("flag %s path has control characters" % flag)
        # resolve and require containment under an allowed root -- kills traversal and
        # absolute escape in one check.
        rp = os.path.realpath(sv)
        if not any(rp == r or rp.startswith(r.rstrip("/") + "/")
                   for r in _ALLOWED_PATH_ROOTS):
            raise BridgeError("flag %s path is outside the allowed roots" % flag)
        return rp
    raise BridgeError("flag %s has an unknown type in the allowlist" % flag)


def build_argv(tool: str, flags: dict) -> list:
    """Turn a validated (tool, flags) request into an argv LIST. Raises BridgeError on
    anything the allowlist does not explicitly permit. This function never touches a
    shell and never reads a path from the request as argv0."""
    entry = ALLOWLIST.get(tool)
    if entry is None:
        raise BridgeError("tool '%s' is not allowed" % tool)
    argv = [entry["argv0"]]
    positionals = []
    for flag, value in (flags or {}).items():
        spec = entry["flags"].get(flag)
        if spec is None:
            raise BridgeError("flag '%s' is not allowed for %s" % (flag, tool))
        rendered = _validate_value(flag, spec, value)
        if spec.get("positional"):
            if rendered is not None:
                positionals.append(rendered)
            continue
        if spec["type"] == "bool":
            if value in (True, "true"):
                argv.append(flag)
        else:
            argv.extend([flag, rendered])
    # v3.66.747 (audit R14) -- END-OF-OPTIONS SEPARATOR. Everything after `--`
    # is a positional operand the tool must not parse as an option, even if a
    # flag-shaped value ever slipped past validation. The validation guard
    # (leading-dash reject) is the belt; this is the suspenders. Only emitted
    # when there are positionals, so tools without any keep a clean argv.
    if positionals:
        argv.append("--")
    argv.extend(positionals)
    return argv


def run(tool: str, flags: dict) -> dict:
    """Validate, then execute with NO shell, a hard timeout, and capped output.
    Returns {returncode, stdout, stderr, argv, timed_out}."""
    argv = build_argv(tool, flags)  # raises BridgeError (-> 400) on any policy violation
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            shell=_USES_SHELL,  # False, pinned
            timeout=TIMEOUT_S,
            cwd=_ALLOWED_PATH_ROOTS[0] if os.path.isdir(_ALLOWED_PATH_ROOTS[0]) else "/tmp",
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},  # minimal, no inherited secrets
        )
        return {
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[:MAX_OUTPUT_BYTES],
            "stderr": (proc.stderr or "")[:MAX_OUTPUT_BYTES],
            "argv": argv,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "timed out after %ds" % TIMEOUT_S,
                "argv": argv, "timed_out": True}


def available() -> list:
    """The allowlist, as a listing an operator surface can render."""
    return [
        {"name": name, "desc": e.get("desc", ""),
         "flags": [{"name": f, **{k: v for k, v in s.items() if k != "type"},
                    "type": s["type"]} for f, s in e["flags"].items()]}
        for name, e in sorted(ALLOWLIST.items())
    ]
