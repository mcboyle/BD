#!/usr/bin/env python3
"""Hash the runtime content selected by fixed-width source-test windows."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests/source_window_hashes.json"


class _Aggregate:
    def __init__(self, paths):
        self.paths = paths

    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self.paths)


def _tracked_tests():
    raw = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "tests/*.py"],
                         check=True, capture_output=True).stdout.decode()
    return [p for p in raw.split("\0") if p]


def _attr_key(node):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(value, env) for value in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(value, env) for value in node.elts)
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        gen = node.generators[0]
        values = []
        for item in _eval(gen.iter, env):
            local = dict(env)
            if isinstance(gen.target, ast.Name):
                local[gen.target.id] = item
            values.append(_eval(node.elt, local))
        return values
    if isinstance(node, ast.Name):
        return env[node.id]
    key = _attr_key(node)
    if key and key in env:
        return env[key]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _eval(node.left, env) / _eval(node.right, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval(node.left, env) + _eval(node.right, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        return _eval(node.left, env) - _eval(node.right, env)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, env)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = _eval(node.left, env), _eval(node.comparators[0], env)
        return {ast.Gt: left > right, ast.GtE: left >= right,
                ast.Lt: left < right, ast.LtE: left <= right,
                ast.Eq: left == right, ast.NotEq: left != right}[type(node.ops[0])]
    if isinstance(node, ast.IfExp):
        return _eval(node.body if _eval(node.test, env) else node.orelse, env)
    if isinstance(node, ast.Subscript):
        return _eval(node.value, env)[_eval(node.slice, env)]
    if isinstance(node, ast.Slice):
        return slice(_eval(node.lower, env) if node.lower else None,
                     _eval(node.upper, env) if node.upper else None,
                     _eval(node.step, env) if node.step else None)
    if isinstance(node, ast.Attribute):
        return getattr(_eval(node.value, env), node.attr)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"max", "min"}:
            return (max if node.func.id == "max" else min)(*[_eval(a, env) for a in node.args])
        if isinstance(node.func, ast.Name) and node.func.id == "Path":
            return Path(_eval(node.args[0], env))
        if isinstance(node.func, ast.Name) and node.func.id == "sorted":
            return sorted(_eval(node.args[0], env))
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            return open(_eval(node.args[0], env), **{
                k.arg: _eval(k.value, env) for k in node.keywords})
        if (isinstance(node.func, ast.Name)
                and node.func.id in {"_login_impl_src", "_learn_impl_src"}):
            directory = ROOT / "bulk_downloader" / node.func.id.removeprefix("_").removesuffix("_src")
            return "\n".join(p.read_text(encoding="utf-8")
                             for p in sorted(directory.glob("*.py")))
        if isinstance(node.func, ast.Name) and "AggregateSrc" in node.func.id:
            paths = [_eval(a, env) for a in node.args]
            if len(paths) == 1 and isinstance(paths[0], Path) and paths[0].is_dir():
                pkg = paths[0]
                stem = "app" if "App" in node.func.id else "runner"
                paths = [pkg / f"{stem}.py"] + sorted(pkg.glob(f"{stem}_*.py"))
            return _Aggregate(paths)
        if (isinstance(node.func, ast.Name) and isinstance(env.get(node.func.id),
                                                         (ast.FunctionDef, ast.AsyncFunctionDef))):
            local = dict(env)
            fn = env[node.func.id]
            for param, arg in zip(fn.args.args, node.args):
                local[param.arg] = _eval(arg, env)
            for stmt in fn.body:
                _assign(stmt, local)
                if (isinstance(stmt, ast.Return)
                        or (isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Yield))):
                    value = stmt.value.value if isinstance(stmt, ast.Expr) else stmt.value
                    try:
                        return _eval(value, local)
                    except (KeyError, ValueError, TypeError, OSError, AttributeError,
                            IndexError):
                        pass
        if (isinstance(node.func, ast.Name) and not node.args and not node.keywords
                and node.func.id.startswith("_") and callable(env.get(node.func.id))):
            return env[node.func.id]()
        if isinstance(node.func, ast.Attribute):
            obj = _eval(node.func.value, env)
            args = [_eval(a, env) for a in node.args]
            kwargs = {k.arg: _eval(k.value, env) for k in node.keywords}
            if node.func.attr in {"read_text", "read", "find", "index", "resolve",
                                  "glob", "join", "dirname", "abspath"}:
                return getattr(obj, node.func.attr)(*args, **kwargs)
    raise ValueError(ast.unparse(node))


def _assign(stmt, env):
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        return
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    value_node = stmt.value
    try:
        value = _eval(value_node, env)
    except (KeyError, ValueError, TypeError, OSError, AttributeError, IndexError):
        return
    for target in targets:
        if isinstance(target, ast.Name):
            env[target.id] = value
        else:
            key = _attr_key(target)
            if key:
                env[key] = value


def _fixture_values(tree, base):
    values = {}
    pending = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for _ in range(3):
        for fn in pending:
            env = dict(base, **values)
            for stmt in fn.body:
                _assign(stmt, env)
                if isinstance(stmt, ast.Return):
                    try:
                        values[fn.name] = _eval(stmt.value, env)
                    except (KeyError, ValueError, TypeError, OSError, AttributeError,
                            IndexError):
                        pass
    return values


def _is_window(node):
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Slice):
        return False
    up = node.slice.upper
    return (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Add)
            and isinstance(up.right, ast.Constant) and isinstance(up.right.value, int)
            and up.right.value >= 100)


def _source_corpora():
    corpora = []
    for rel in subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "bulk_downloader/*.py",
             "frontend/src/*", "capture.sh"], check=True,
            capture_output=True).stdout.decode().split("\0"):
        if rel and (ROOT / rel).is_file():
            try:
                corpora.append((rel, (ROOT / rel).read_text(encoding="utf-8",
                                                               errors="replace")))
            except OSError:
                pass
    app = [text for rel, text in corpora if rel == "bulk_downloader/app.py"
           or rel.startswith("bulk_downloader/app_")]
    runner = [text for rel, text in corpora if rel == "bulk_downloader/runner.py"
              or rel.startswith("bulk_downloader/runner_")]
    corpora += [("<app aggregate>", "\n".join(app)),
                ("<runner aggregate>", "\n".join(runner))]
    return corpora


def _infer_source(fn, name, corpora):
    needles = []
    for call in ast.walk(fn):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == name and call.func.attr in {"find", "index"}
                and call.args and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)):
            needles.append(call.args[0].value)
    if not needles:
        return None
    matches = [(rel, text) for rel, text in corpora
               if all(needle in text for needle in needles)]
    aggregates = [item for item in matches if item[0].startswith("<")]
    if aggregates:
        matches = aggregates
    if len(matches) == 1:
        return matches[0][1]
    return None


def build():
    records, unresolved = [], []
    corpora = _source_corpora()
    for rel in _tracked_tests():
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        base = {"ROOT": ROOT, "REPO": ROOT, "REPO_ROOT": ROOT,
                "_REPO": ROOT, "__file__": str(ROOT / rel), "os": os}
        for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef,
                                                          ast.AsyncFunctionDef))]:
            base[fn.name] = fn
        for stmt in tree.body:
            _assign(stmt, base)
        # Class setup assignments provide the one self.<source> window family.
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                for stmt in sorted((n for n in ast.walk(fn)
                                    if isinstance(n, (ast.Assign, ast.AnnAssign))),
                                   key=lambda n: n.lineno):
                    _assign(stmt, base)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            env = dict(base)
            env.setdefault("self.runner_src", dict(corpora).get("<runner aggregate>"))
            for common in ("src", "app_src", "runner_src"):
                inferred = _infer_source(fn, common, corpora)
                if inferred is not None:
                    env[common] = inferred
            if "app_src" not in env:
                env["app_src"] = dict(corpora).get("<app aggregate>")
            for window in [n for n in ast.walk(fn) if _is_window(n)]:
                if isinstance(window.value, ast.Name):
                    name = window.value.id
                    inferred = _infer_source(fn, name, corpora)
                    if inferred is not None:
                        env[name] = inferred
            for arg in fn.args.args:
                if arg.arg in base:
                    value = base[arg.arg]
                    if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        try:
                            value = _eval(ast.Call(func=ast.Name(id=arg.arg),
                                                   args=[], keywords=[]), base)
                        except (KeyError, ValueError, TypeError, OSError,
                                AttributeError, IndexError, RecursionError):
                            pass
                    env[arg.arg] = value
                if arg.arg in {"src", "app_src", "runner_src"}:
                    inferred = _infer_source(fn, arg.arg, corpora)
                    if inferred is not None:
                        env[arg.arg] = inferred
            ordinal = Counter()
            events = [n for n in ast.walk(fn)
                      if isinstance(n, (ast.Assign, ast.AnnAssign)) or _is_window(n)]
            events.sort(key=lambda n: (n.lineno, 0 if isinstance(
                n, (ast.Assign, ast.AnnAssign)) else 1))
            for node in events:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    _assign(node, env)
                    continue
                if _is_window(node):
                    expr = ast.unparse(node)
                    ordinal[expr] += 1
                    key = f"{rel}::{fn.name}::{expr}::{ordinal[expr]}"
                    try:
                        content = _eval(node, env)
                        if not isinstance(content, (str, bytes)):
                            raise ValueError("window is not text/bytes")
                    except (KeyError, ValueError, TypeError, OSError, AttributeError,
                            IndexError) as exc:
                        unresolved.append({"key": key, "reason": str(exc)})
                        continue
                    raw = content.encode() if isinstance(content, str) else content
                    records.append({"key": key, "bytes": len(raw),
                                    "sha256": hashlib.sha256(raw).hexdigest()})
    if not records:
        raise RuntimeError("UNKNOWN: zero fixed source windows resolved")
    return {"schema_version": 1, "algorithm": "sha256",
            "resolved": len(records), "unresolved": unresolved,
            "windows": sorted(records, key=lambda r: r["key"])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    data = build()
    if data["unresolved"]:
        print(json.dumps(data["unresolved"], indent=2), file=sys.stderr)
        return 2
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.check:
        actual = OUT.read_text() if OUT.is_file() else ""
        if actual != rendered:
            print("STALE: fixed source-window contents changed", file=sys.stderr)
            return 1
        print(f"PASS: {data['resolved']} source-window content hashes match")
        return 0
    OUT.write_text(rendered)
    print(f"wrote {OUT.relative_to(ROOT)} ({data['resolved']} windows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
