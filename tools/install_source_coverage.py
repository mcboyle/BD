"""Census local Linux install launch inputs without executing an installer.

This is a bounded launch recognizer, not a Bash interpreter. Unknown variables
in a recognized local launch fail closed. Optional branches are reviewed per
owner and pinned to their live control-flow block, never globally by filename.
"""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path
import re
import subprocess


POLICY = Path(__file__).with_name("linux_install_sources.json")
ATOM = r'''(?:"[^"]*"|'[^']*'|[^\s;&|()<>]+)'''
PYTHON = r'''(?:(?:[\w./-]+/)?python[\d.]*|"\$(?:VPYTHON|VENV_PY|PYEXE)")'''


def _code(text):
    return "\n".join(line for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))


def _launches(text, owner, root_variables=()):
    """Return live local source expressions, including service launch inputs."""
    directory = posixpath.dirname(owner)
    for spelling in ('$(dirname "$0")', '$(dirname "${BASH_SOURCE[0]}")'):
        text = text.replace(spelling, "@ROOT@/" + directory)
    aliases = {name: "@ROOT@" for name in root_variables}
    for match in re.finditer(r"(?m)^\s*([A-Za-z_]\w*)=(" + ATOM + r")", text):
        if match[1] not in root_variables:
            aliases[match[1]] = match[2].strip("\"'")
    calls = re.findall(r"(?m)^converge_reqs\s+(" + ATOM + ")", text)
    expressions = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"\s*(?:echo|printf|record|note|die)\b", line):
            continue
        source = re.match(r"\s*(?:source|\.)\s+(" + ATOM + ")", line)
        if source:
            expressions.append((source[1], number, False))
        for match in re.finditer(r"(?:\bbash|\"\$BASH\")\s+(" + ATOM + ")", line):
            target = match[1].strip("\"'")
            if target.endswith(".sh") or target.startswith("$"):
                expressions.append((target, number, False))
        for match in re.finditer(PYTHON + r"\s+(" + ATOM + ")", line):
            prefix = line[:match.start()].strip()
            if (prefix and not prefix.endswith(("(", "|", ";", "&&", "||"))
                    and not re.fullmatch(r"(?:(?:if|elif|exec|sudo|!|\$SUDO)\s*)+", prefix)):
                continue  # an interpreter passed as another command's argument
            target = match[1]
            if target == "-m":
                module = re.match(r"\s+(bulk_downloader[.\w]+)", line[match.end():])
                if module:
                    expressions.append((module[1].replace(".", "/") + ".py", number, False))
            elif target.strip("\"'").endswith(".py") or target.strip("\"'").startswith("$"):
                expressions.append((target, number, False))
        if re.search(r"\bpip\s+install\b", line):
            requirements = re.search(r"(?:^|\s)-r\s+(" + ATOM + ")", line)
            if requirements:
                target = requirements[1].strip("\"'")
                expressions.extend((item, number, False) for item in
                                   (calls if target == "$_req_file" else [target]))
        service = re.match(r"ExecStart(?:Pre)?=(-?)(.*)", line)
        if service:
            tokens = re.findall(ATOM, service[2])
            if tokens and tokens[0].endswith(".sh"):
                expressions.append((tokens[0], number, service[1] == "-"))
            elif len(tokens) > 1 and tokens[1].endswith(".py"):
                expressions.append((tokens[1], number, service[1] == "-"))
            elif len(tokens) > 2 and tokens[1] == "-m" and tokens[2].startswith("bulk_downloader."):
                expressions.append((tokens[2].replace(".", "/") + ".py", number, service[1] == "-"))
        if owner == "SETUP.md":
            copy = re.match(r"cp\s+(" + ATOM + ")", line)
            if copy:
                expressions.append((copy[1], number, False))
        if re.search(r"\bnpm\s+(?:ci|install|run\s+build)\b", line):
            prefix = re.search(r"--prefix\s+(" + ATOM + ")", line)
            if (prefix and prefix[1].strip("\"'") == "$_vtmp"
                    and aliases.get("_vtmp") == "$(mktemp -d)"):
                continue  # generated temporary vendor download, not a repo build
            directory = prefix[1].strip("\"'") if prefix else "frontend"
            expressions.extend((item, number, False) for item in
                               (directory + "/package.json", directory + "/package-lock.json"))
    return expressions, aliases


def _resolve(expression, aliases):
    value = expression.strip("\"'")
    for _ in range(12):
        updated = re.sub(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)",
                         lambda m: aliases.get(m[1] or m[2], m[0]), value)
        if updated == value:
            break
        value = updated
    if any(char in value for char in "$`~"):
        raise ValueError("unresolved local source " + expression)
    value = value.removeprefix("@ROOT@/")
    normalized = posixpath.normpath(value)
    if normalized in ("", ".", "..") or normalized.startswith(("../", "/")):
        raise ValueError("local source escapes checkout: " + expression)
    return normalized


def audit(root, entries=None, tracked=None):
    root = Path(root).resolve()
    policy = json.loads(POLICY.read_text())
    default_entries = entries is None
    entries = list(policy["entries"] if default_entries else entries)
    result = {"entries": entries, "sources": [], "excluded": [], "errors": []}
    if not entries:
        result["errors"].append("supported Linux entry set is empty")
        return result
    if tracked is None:
        try:
            tracked = set(subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "-z"], text=True,
                stderr=subprocess.PIPE).split("\0"))
        except subprocess.CalledProcessError:
            result["errors"].append(f"cannot read tracked sources from {root}")
            return result
    tracked = set(tracked)
    seen, pending = set(), list(entries)
    optional = {(item["owner"], item["target"]): item for item in policy["optional"]}

    def check(owner, target):
        path = root / target
        if target not in tracked:
            result["errors"].append(f"{owner}: required source is untracked: {target}")
            return False
        if not path.is_file():
            result["errors"].append(f"{owner}: required source is missing: {target}")
            return False
        try:
            resolved = path.resolve().relative_to(root).as_posix()
        except ValueError:
            resolved = "<outside checkout>"
        if resolved not in tracked:
            result["errors"].append(f"{owner}: required source resolves outside tracked files: {target} -> {resolved}")
            return False
        return True

    while pending:
        owner = pending.pop(0)
        if owner in seen:
            continue
        seen.add(owner)
        if not check(owner, owner):
            continue
        text = (root / owner).read_text()
        if owner == "SETUP.md":
            match = re.search(r"(?ms)^## Linux / macOS\n(.*?)(?=^## |\Z)", text)
            if not match:
                result["errors"].append("SETUP.md: Linux manual entry is unresolved")
                continue
            text = match[1]
        root_variables = policy["root_variables"].get(owner, {})
        for name, expected in root_variables.items():
            assignments = [line.strip() for line in text.splitlines()
                           if re.match(r"\s*" + re.escape(name) + "=", line)]
            if assignments != expected:
                result["errors"].append(f"{owner}: root binding changed for {name}; source resolution requires review")
        expressions, aliases = _launches(text, owner, root_variables)
        targets = {}
        for expression, number, service_optional in expressions:
            bare = expression.strip("\"'")
            if owner == "SETUP.md" and bare == "venv/bin/activate":
                result["excluded"].append(dict(owner=owner, target=bare, reason="generated venv activation"))
                continue
            if (owner == "scripts/deploy.sh" and bare == "$vault_unlock_hook"
                    and aliases.get("vault_unlock_hook") == "${HOME:-}/bd-vault-unlock.sh"):
                result["excluded"].append(dict(owner=owner, target=bare, reason="external operator hook"))
                continue
            try:
                targets.setdefault(_resolve(expression, aliases), []).append(
                    (expression, number, service_optional))
            except ValueError as exc:
                result["errors"].append(f"{owner}: {exc}")
                result["sources"].append(dict(owner=owner, target=expression,
                                              required=True, resolved=False))
        for target, occurrences in sorted(targets.items()):
            branch = optional.get((owner, target))
            guarded_lines = set()
            if branch:
                match = re.search(branch["guard_regex"], text)
                digest = hashlib.sha256(_code(match[0]).encode()).hexdigest() if match else None
                if digest != branch["guard_sha256"]:
                    result["errors"].append(f"{owner}: optional branch changed for {target}; review requiredness")
                else:
                    first = text[:match.start()].count("\n") + 1
                    guarded_lines = set(range(first, first + match[0].count("\n") + 1))
            required = any(not service_optional and number not in guarded_lines
                           for _, number, service_optional in occurrences)
            result["sources"].append(dict(owner=owner, target=target, required=required,
                                          occurrences=[dict(expression=expression, line=number)
                                                       for expression, number, _ in occurrences]))
            if required and check(owner, target) and target.endswith(".sh"):
                pending.append(target)
    if not result["sources"]:
        result["errors"].append("supported Linux entries expose no local launch sources")
    if default_entries:
        for owner, reference in policy["entries"].items():
            if not check(owner, reference["document"]):
                continue
            if reference["anchor"] not in (root / reference["document"]).read_text():
                result["errors"].append(f"{owner}: supported entry reference is unresolved")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = audit(args.root)
    print(json.dumps(result, indent=2))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
