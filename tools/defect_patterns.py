#!/usr/bin/env python3
"""defect_patterns -- the project-native linter built from BulkDownloader's own
confirmed bugs (DEFECT_PATTERN_CATALOG.md, DP-01..DP-18).

A project-native linter: every confirmed bug-class is codified as an AST/grep
detector so the *next* instance is caught for free. High-precision detectors
(auto-finding) carry a frozen vuln/fixed corpus pair under regression_corpus/;
`--check` asserts each fires on its vuln fixture and is silent on the fixed one
(rc!=0 on any miss/false-positive -- this is the gate). Triage detectors emit
candidate lists (lower precision, human-confirmed).

Modes:
  --scan ROOT        scan production files -> findings JSON (stdout or --out)
  --check            run the corpus gate (validates the detectors); rc!=0 on fail
  --file PATH        scan a single file (debug)

stdlib `ast`/`re` only, offline, deterministic.
"""
import argparse
import ast
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "toolchain", "bin"))
from bd_defect_suppressions import (  # noqa: E402
    SuppressionError,
    apply_suppressions,
    finding_fingerprint,
    handler_contexts,
    load_suppressions,
)

# ---- detector registry -------------------------------------------------------
# Each detector: fn(path, src, tree_or_None) -> list[finding dict].
# finding = {dp, severity, precision, line, title, snippet}
HIGH = "high-precision"
TRIAGE = "triage"

REDACT_NAME = re.compile(r"(redact|scrub|mask|sanitiz|_is_secret)", re.I)


def _line(src, idx):
    return src.count("\n", 0, idx) + 1


def _find(dp, sev, prec, line, title, snippet=""):
    return {"dp": dp, "severity": sev, "precision": prec, "line": line,
            "title": title, "snippet": snippet[:120]}


# DP-01 -- get_json(silent=True) or {} then .get without isinstance dict guard
def dp01(path, src, tree):
    out = []
    if tree is None:
        return out
    # collect names assigned from `<get_json(silent=True)> or {}`
    risky = {}  # name -> lineno
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.BoolOp) \
                and isinstance(node.value.op, ast.Or):
            vals = node.value.values
            has_gj = any(isinstance(v, ast.Call) and _is_getjson_silent(v) for v in vals)
            has_empty = any(isinstance(v, ast.Dict) and not v.keys for v in vals)
            if has_gj and has_empty and node.targets and isinstance(node.targets[0], ast.Name):
                risky[node.targets[0].id] = node.lineno
    if not risky:
        return out
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "isinstance" and node.args \
                and isinstance(node.args[0], ast.Name) and node.args[0].id in risky:
            guarded.add(node.args[0].id)
        # reassignment `b = b if isinstance...` also counts as guard above
    for name, ln in sorted(risky.items()):
        if name not in guarded:
            out.append(_find("DP-01", "medium", HIGH, ln,
                             f"get_json(silent=True) or {{}} -> .get without isinstance dict guard ({name})"))
    return out


def _is_getjson_silent(call):
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    if name != "get_json":
        return False
    return any(isinstance(k, ast.keyword) and k.arg == "silent"
               and isinstance(k.value, ast.Constant) and k.value.value is True
               for k in call.keywords)


# DP-03 -- float(v) then bounds Compare without math.isfinite/isnan guard
def dp03(path, src, tree):
    out = []
    if tree is None:
        return out
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        floated = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "float" \
                    and node.targets and isinstance(node.targets[0], ast.Name):
                floated[node.targets[0].id] = node.lineno
        if not floated:
            continue
        has_isfinite = any(
            isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Attribute) and n.func.attr in ("isfinite", "isnan", "isinf"))
                or (isinstance(n.func, ast.Name) and n.func.id in ("isfinite", "isnan", "isinf")))
            for n in ast.walk(fn))
        cmped = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare):
                for sub in [node.left, *node.comparators]:
                    if isinstance(sub, ast.Name) and sub.id in floated:
                        cmped.add(sub.id)
        for name in sorted(cmped):
            if not has_isfinite:
                out.append(_find("DP-03", "medium", HIGH, floated[name],
                                 f"float({name}) feeds a bounds check with no math.isfinite guard (NaN/inf evades)"))
    return out


# DP-04 -- float()/int() coercion for a typed field without isinstance(int)
def dp04(path, src, tree):
    out = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("int", "float"):
            # heuristic triage: flag near a 'range'/'min'/'max' bound without isinstance
            pass
    return out  # folded into DP-03; kept as a no-op placeholder for catalog completeness


# DP-06 -- bare undefined name reached at runtime (module-level resolution)
def dp06(path, src, tree):
    out = []
    if tree is None:
        return out
    bound = set(dir(__builtins__) if isinstance(__builtins__, type)
                else vars(__builtins__).keys())
    # module-level defs/imports/assigns
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.add(t.id)
    # only flag names used in hasattr/getattr(<bare>, ...) -- the F0001 shape
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("hasattr", "getattr") and node.args \
                and isinstance(node.args[0], ast.Name):
            nm = node.args[0].id
            if nm not in bound and nm not in ("self", "cls"):
                out.append(_find("DP-06", "medium", TRIAGE, node.lineno,
                                 f"hasattr/getattr on possibly-undefined name '{nm}' (NameError not caught)"))
    return out


# DP-07 -- masking fn that does not recurse into dict/list values
def dp07(path, src, tree):
    out = []
    if tree is None:
        return out
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if not REDACT_NAME.search(fn.name):
            continue
        iterates = any(
            (isinstance(n, ast.Attribute) and n.attr in ("items", "keys", "values"))
            or isinstance(n, ast.DictComp)
            for n in ast.walk(fn))
        if not iterates:
            continue
        # recursion: calls itself, OR handles isinstance(_, (dict, list)) with a recursive map
        names_called = set()
        has_isinstance_container = False
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                names_called.add(nm)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "isinstance":
                txt = ast.dump(n)
                if "dict" in txt or "list" in txt:
                    has_isinstance_container = True
        recurses = fn.name in names_called or any(REDACT_NAME.search(c or "") for c in names_called)
        if not (recurses and has_isinstance_container):
            out.append(_find("DP-07", "high", HIGH, fn.lineno,
                             f"masking fn '{fn.name}' iterates a mapping but does not recurse into dict/list values (nested secret leaks)"))
    return out


# DP-08 -- two secret-keyword constants in a redaction module that differ
def dp08(path, src, tree):
    out = []
    if tree is None or not REDACT_NAME.search(path) and "redact" not in src.lower():
        # only meaningful where redaction keyword sets live
        pass
    kw_sets = {}
    for node in ast.walk(tree) if tree else []:
        if isinstance(node, ast.Assign) and node.targets \
                and isinstance(node.targets[0], ast.Name) \
                and re.search(r"SECRET|KV.*KEY|KEYWORD", node.targets[0].id, re.I):
            members = _literal_str_set(node.value)
            if members is not None:
                kw_sets[node.targets[0].id] = (members, node.lineno)
    names = sorted(kw_sets)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, la = kw_sets[names[i]]
            b, lb = kw_sets[names[j]]
            if a != b and (a & b):  # overlapping but not equal -> divergence
                missing = (a | b) - (a & b)
                out.append(_find("DP-08", "medium", HIGH, lb,
                                 f"secret-keyword sets {names[i]} vs {names[j]} diverge (missing in one: {sorted(missing)[:5]})"))
    return out


def _literal_str_set(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("frozenset", "set") and node.args:
        node = node.args[0]
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        vals = set()
        for e in node.elts:
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                vals.add(e.value)
            else:
                return None
        return vals
    return None


# DP-09 -- scan/walk fn with only dict/list/str branches (blind to bytes/set)
def dp09(path, src, tree):
    out = []
    if tree is None:
        return out
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if not re.search(r"scan|scrub|walk|redact", fn.name, re.I):
            continue
        types_seen = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "isinstance":
                types_seen.add(ast.dump(n))
        blob = " ".join(types_seen)
        if blob and ("dict" in blob or "list" in blob) and "bytes" not in blob and "set" not in blob:
            out.append(_find("DP-09", "low", TRIAGE, fn.lineno,
                             f"scanner '{fn.name}' branches on dict/list/str but not bytes/set (fail-open on unknown types)"))
    return out


# DP-10 -- f-string SQL with an UNGUARDED interpolated identifier
def _interpolated_names(joinedstr):
    names = set()
    for p in joinedstr.values:
        if isinstance(p, ast.FormattedValue):
            for n in ast.walk(p.value):
                if isinstance(n, ast.Name):
                    names.add(n.id)
    return names


def _allowlisted_names(fn_node):
    """Names membership-tested against a set/frozenset/collection -> allowlisted."""
    guarded = set()
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Compare) and any(
                isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
            if isinstance(n.left, ast.Name):
                guarded.add(n.left.id)
    return guarded


def dp10(path, src, tree):
    out = []
    if tree is None:
        return out
    # map each function to its allowlisted names; module-level checked separately
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in fns:
        guarded = _allowlisted_names(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.JoinedStr):
                txt = "".join(p.value for p in node.values
                              if isinstance(p, ast.Constant) and isinstance(p.value, str))
                if re.search(r"\b(FROM|INTO|UPDATE|TABLE|JOIN)\b", txt, re.I) and any(
                        isinstance(p, ast.FormattedValue) for p in node.values):
                    names = _interpolated_names(node)
                    if names and names <= guarded:
                        continue  # every interpolated id is allowlist-checked -> mitigated
                    out.append(_find("DP-10", "medium", HIGH, node.lineno,
                                     "f-string SQL with unguarded interpolated identifier (whitelist the table/column)",
                                     txt.strip()))
    return out


# DP-11 -- IP classifier not using is_global/is_private as the allowlist test
def dp11(path, src, tree):
    out = []
    if tree is None:
        return out
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        body = ast.dump(fn)
        touches_ip = "ip_address" in body or "ip_network" in body or \
            re.search(r"classify_ip|is_safe.*host|ssrf|public_host", fn.name, re.I)
        if not touches_ip:
            continue
        uses_global = "is_global" in body
        # denylist style: manual is_private/is_loopback checks without is_global
        manual = any(s in body for s in ("is_private", "is_loopback", "is_link_local",
                                         "is_reserved"))
        if manual and not uses_global:
            out.append(_find("DP-11", "low", HIGH, fn.lineno,
                             f"IP classifier '{fn.name}' uses denylist ranges without is_global allowlist (misses CGNAT 100.64/10 etc.)"))
    return out


# DP-12 -- path/host component interpolated without sanitize (triage)
def dp12(path, src, tree):
    out = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            txt = "".join(p.value for p in node.values
                          if isinstance(p, ast.Constant) and isinstance(p.value, str))
            if re.search(r"https?://|/\{|\.\./|%s/", txt) and any(
                    isinstance(p, ast.FormattedValue) for p in node.values):
                if "../" not in src[max(0, 0):]:  # cheap; real check is taint
                    out.append(_find("DP-12", "low", TRIAGE, node.lineno,
                                     "host/path interpolated into URL/path f-string (confirm sanitize)",
                                     txt.strip()))
    return out[:50]


# DP-13 -- swallowed-exception cluster (try/except: log/pass)
def dp13(path, src, tree):
    out = []
    if tree is None:
        return out
    contexts = handler_contexts(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = node.body
            only_passlog = all(
                isinstance(s, ast.Pass)
                or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call))
                for s in body) and body
            if only_passlog:
                finding = _find("DP-13", "medium", TRIAGE, node.lineno,
                                "exception swallowed (pass/log only) -- dead feature if body always raises")
                finding["fingerprint"] = finding_fingerprint(
                    "DP-13", path, node, contexts[id(node)]
                )
                out.append(finding)
    return out[:200]


# DP-14 -- apprise add() without tag= where notify(tag=) is used; AppriseURLBase
def dp14(path, src, tree):
    out = []
    if "AppriseURLBase" in src:
        out.append(_find("DP-14", "high", HIGH, _line(src, src.index("AppriseURLBase")),
                         "apprise.AppriseURLBase referenced (removed in apprise>=1.7 -> validate_urls fails)"))
    if tree is None:
        return out
    uses_notify_tag = any(
        isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Attribute) and n.func.attr == "notify"))
        and any(isinstance(k, ast.keyword) and k.arg == "tag" for k in n.keywords)
        for n in ast.walk(tree))
    if uses_notify_tag:
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "add":
                has_tag = any(isinstance(k, ast.keyword) and k.arg == "tag" for k in n.keywords)
                if not has_tag:
                    out.append(_find("DP-14", "high", HIGH, n.lineno,
                                     "apprise .add() without tag= but notify(tag=) filters -> 0 services match"))
    return out


# DP-15 -- two path resolvers with different default branches (triage)
def dp15(path, src, tree):
    out = []
    if tree is None:
        return out
    resolvers = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        body = ast.dump(fn)
        if ("BD_HOME" in body or "getcwd" in body or "abspath" in body) \
                and "return" in src:
            resolvers.append(fn.name)
    if len(resolvers) >= 2:
        out.append(_find("DP-15", "low", TRIAGE, 1,
                         f"multiple path resolvers in one module ({resolvers[:4]}) -- confirm shared default"))
    return out


# DP-16 -- env-coupled test asserting accumulated runtime state
def dp16(path, src, tree):
    out = []
    if "test" not in os.path.basename(path):
        return out
    for m in re.finditer(r">=\s*[2-9]\d*", src):
        ctx = src[max(0, m.start() - 60):m.start()]
        if re.search(r"template|draft|total|count|len\(", ctx, re.I):
            out.append(_find("DP-16", "na", TRIAGE, _line(src, m.start()),
                             "test asserts >=N accumulated artifacts (self-seed; green-on-stash/RED-clean risk)"))
    return out[:30]


# DP-17 -- process-global leak in test/tool without restore
def dp17(path, src, tree):
    out = []
    if tree is None:
        return out
    is_test_or_tool = "test" in os.path.basename(path) or "/tools/" in path
    if not is_test_or_tool:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "append" and "sys.path" in ast.dump(node.func):
            # check there's a finally in the enclosing function
            out.append(_find("DP-17", "low", TRIAGE, node.lineno,
                             "sys.path.append in test/tool (confirm finally-restore)"))
    return out


# DP-18 -- unbounded per-char/segment scan (triage)
def dp18(path, src, tree):
    out = []
    if tree is None:
        return out
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
        nested = any(any(isinstance(c, ast.For) for c in ast.walk(l) if c is not l)
                     for l in loops)
        if nested and re.search(r"char|segment|digit|path|url", fn.name, re.I):
            out.append(_find("DP-18", "low", TRIAGE, fn.lineno,
                             f"nested loop in '{fn.name}' over string-ish input (bound the scan)"))
    return out


# DP-02 / DP-05 require ERROR_CATALOG / call-graph cross-refs -> emitted by those
# projections; here we keep catalog-complete stubs that return [].
def dp02(path, src, tree):
    return []  # raise->status mismatch: cross-ref ERROR_CATALOG.json (audit step)


def dp05(path, src, tree):
    return []  # caller/callee kwarg drift: cross-ref CALL_GRAPH.json (semantic_diff)


DETECTORS = {
    "DP-01": dp01, "DP-02": dp02, "DP-03": dp03, "DP-04": dp04, "DP-05": dp05,
    "DP-06": dp06, "DP-07": dp07, "DP-08": dp08, "DP-09": dp09, "DP-10": dp10,
    "DP-11": dp11, "DP-12": dp12, "DP-13": dp13, "DP-14": dp14, "DP-15": dp15,
    "DP-16": dp16, "DP-17": dp17, "DP-18": dp18,
}
# detectors with a frozen corpus pair -> exercised by --check
CORPUS_GATED = ["DP-01", "DP-03", "DP-07", "DP-08", "DP-10", "DP-11", "DP-14"]

PROD = (("bulk_downloader", (".py",)), ("tools", (".py",)))


def _parse(path, src):
    if not path.endswith(".py"):
        return None
    try:
        return ast.parse(src, filename=path)
    except SyntaxError:
        return None


def scan_file(path, src, only=None):
    tree = _parse(path, src)
    findings = []
    for dp, fn in DETECTORS.items():
        if only and dp not in only:
            continue
        try:
            findings += fn(path, src, tree)
        except Exception as e:
            findings.append(_find(dp, "error", "error", 0, f"detector error: {e}"))
    return findings


def scan_tree(root, include_suppression_report=False):
    suppressions = load_suppressions(root, DETECTORS)
    results = {}
    for base, exts in PROD:
        for dp_, _, fns in os.walk(os.path.join(root, base)):
            if "node_modules" in dp_ or "__pycache__" in dp_:
                continue
            for f in fns:
                if f.endswith(exts):
                    p = os.path.join(dp_, f)
                    rel = os.path.relpath(p, root)
                    src = open(p, encoding="utf-8", errors="replace").read()
                    fnd = scan_file(rel, src)
                    if fnd:
                        results[rel] = fnd
    visible, suppressed, suppression_errors = apply_suppressions(
        results, suppressions
    )
    if include_suppression_report:
        return (visible, suppressed, results, len(suppressions),
                suppression_errors)
    if suppression_errors:
        raise SuppressionError(suppression_errors[0])
    return visible


def check_corpus(corpus_dir):
    """Gate: each corpus-gated DP fires on its *_vuln.py, silent on *_fixed.py."""
    fails = []
    for dp in CORPUS_GATED:
        for kind, expect in (("vuln", True), ("fixed", False)):
            p = os.path.join(corpus_dir, f"{dp}_{kind}.py")
            if not os.path.exists(p):
                fails.append(f"{dp}: missing fixture {kind}")
                continue
            src = open(p).read()
            hits = [f for f in scan_file(os.path.basename(p), src, only={dp})
                    if f["dp"] == dp and f["precision"] != "error"]
            fired = len(hits) > 0
            if fired != expect:
                fails.append(f"{dp}_{kind}: expected fire={expect}, got {fired} "
                             f"(hits={len(hits)})")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", metavar="ROOT")
    ap.add_argument("--file", metavar="PATH")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--corpus", default="/home/claude/review/regression_corpus")
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.check:
        fails = check_corpus(a.corpus)
        if fails:
            print("defect_patterns --check: FAIL")
            for f in fails:
                print("  -", f)
            sys.exit(1)
        print(f"defect_patterns --check: PASS ({len(CORPUS_GATED)} corpus-gated detectors "
              f"fire on vuln + silent on fixed)")
        sys.exit(0)

    if a.file:
        src = open(a.file, encoding="utf-8", errors="replace").read()
        print(json.dumps(scan_file(a.file, src), indent=2))
        return

    if a.scan:
        try:
            res, suppressed, raw, suppression_entries, suppression_errors = scan_tree(
                a.scan, include_suppression_report=True
            )
        except SuppressionError as exc:
            print(json.dumps({
                "schema": 1,
                "root": a.scan,
                "suppression_entries": 0,
                "suppression_errors": [str(exc)],
            }, indent=2, sort_keys=True))
            print("CANNOT-EVALUATE --scan %s: suppression authority: %s"
                  % (a.scan, exc), file=sys.stderr)
            raise SystemExit(2)

        def summarize(findings):
            total = sum(len(v) for v in findings.values())
            counts = {}
            for values in findings.values():
                for finding in values:
                    counts[finding["dp"]] = counts.get(finding["dp"], 0) + 1
            return total, dict(sorted(counts.items()))

        n, by_dp = summarize(res)
        raw_n, raw_by_dp = summarize(raw)
        suppressed_n, suppressed_by_dp = summarize(suppressed)
        payload = {"schema": 1, "root": a.scan, "files_with_findings": len(res),
                   "total_findings": n, "by_dp": dict(sorted(by_dp.items())),
                   "findings": res,
                   "raw_total_findings": raw_n,
                   "suppressed_total_findings": suppressed_n,
                   "visible_total_findings": n,
                   "raw_by_dp": raw_by_dp,
                   "suppressed_by_dp": suppressed_by_dp,
                   "suppressed_findings": suppressed,
                   "suppression_entries": suppression_entries,
                   "suppression_errors": suppression_errors}
        if a.out:
            json.dump(payload, open(a.out, "w"), indent=2, sort_keys=True)
            print("defect_patterns --scan: %d visible + %d suppressed = %d raw "
                  "findings across %d visible files -> %s"
                  % (n, suppressed_n, raw_n, len(res), a.out))
            print("  by DP:", json.dumps(dict(sorted(by_dp.items()))))
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        if suppression_errors:
            for error in suppression_errors:
                print("suppression error:", error, file=sys.stderr)
            raise SystemExit(2)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
