/**
 * Repository-wide raw-fetch CSRF boundary, parsed as TypeScript/TSX.
 *
 * This is the unavoidable static floor for a global absence claim: a runtime
 * route sweep cannot prove that an unvisited product branch contains no raw
 * state-changing fetch.  The retired Python regex was case-sensitive, required
 * `method:` with no intervening whitespace, searched comments as code, and
 * stopped after 400 characters.  TypeScript's parser removes those spelling
 * evasions: comments and strings are not call expressions, whitespace and
 * quote style have no meaning, and method values are case-normalized.
 * Identifier indirection local to the same source file is resolved for fetch,
 * the options object, computed option keys, and method values; unresolved
 * options/Request shapes fail closed as UNKNOWN offenders rather than
 * certifying an unmeasured call.
 *
 * DECLARED EVASION SURFACE. This floor recognizes direct global/member
 * `fetch(...)` calls and `Request` constructors, including local const aliases,
 * object spreads, shorthand `method`, and string concatenation. It does not
 * build a whole-program data-flow graph: values returned from functions,
 * imported aliases of fetch/options, mutation after declaration, and methods
 * computed from non-constant runtime data remain outside its proof. Such a
 * shape is reported UNKNOWN when it reaches a visible fetch call; an imported
 * alias that no longer spells `fetch` is the residual blind spot. XHR,
 * sendBeacon, service workers, and nonstandard methods are separate transport
 * surfaces; every known method except GET/HEAD/OPTIONS is conservatively
 * treated as state-changing.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = resolve(process.cwd(), "src");
const SPEC_FILE_RE = /\.(test|spec)\.tsx?$/;
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

type Resolution =
  | { kind: "absent" }
  | { kind: "known"; value: string }
  | { kind: "unknown"; reason: string };

type Finding = { file: string; line: number; method: string };

function sourceFiles(root: string): string[] {
  const out: string[] = [];
  const visit = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = resolve(dir, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (/\.tsx?$/.test(entry.name)) out.push(path);
    }
  };
  visit(root);
  return out.sort();
}

function unwrap(node: ts.Expression): ts.Expression {
  let current = node;
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isTypeAssertionExpression(current) ||
    ts.isNonNullExpression(current) ||
    ts.isSatisfiesExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

function bindingsOf(sf: ts.SourceFile): Map<string, ts.Expression> {
  const bindings = new Map<string, ts.Expression>();
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      bindings.set(node.name.text, node.initializer);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return bindings;
}

function propertyName(node: ts.PropertyName): string | null {
  if (ts.isIdentifier(node) || ts.isStringLiteralLike(node)) return node.text;
  if (ts.isComputedPropertyName(node) && ts.isStringLiteralLike(unwrap(node.expression))) {
    return (unwrap(node.expression) as ts.StringLiteralLike).text;
  }
  return null;
}

function normalizedName(value: string): string {
  return value.replace(/[\s_]+/g, "").toLocaleLowerCase("en-US");
}

function stringValue(
  expression: ts.Expression,
  bindings: Map<string, ts.Expression>,
  seen: Set<string>,
): Resolution {
  const node = unwrap(expression);
  if (ts.isStringLiteralLike(node)) return { kind: "known", value: node.text };
  if (ts.isIdentifier(node)) {
    if (seen.has(node.text)) return { kind: "unknown", reason: `cyclic ${node.text}` };
    const target = bindings.get(node.text);
    if (!target) return { kind: "unknown", reason: `unresolved ${node.text}` };
    const next = new Set(seen);
    next.add(node.text);
    return stringValue(target, bindings, next);
  }
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = stringValue(node.left, bindings, new Set(seen));
    const right = stringValue(node.right, bindings, new Set(seen));
    if (left.kind === "known" && right.kind === "known") {
      return { kind: "known", value: left.value + right.value };
    }
    return { kind: "unknown", reason: "non-constant string concatenation" };
  }
  return { kind: "unknown", reason: ts.SyntaxKind[node.kind] };
}

function methodFromOptions(
  expression: ts.Expression | undefined,
  bindings: Map<string, ts.Expression>,
  seen: Set<string> = new Set(),
): Resolution {
  if (!expression) return { kind: "absent" };
  const node = unwrap(expression);
  if (node.kind === ts.SyntaxKind.UndefinedKeyword || node.kind === ts.SyntaxKind.NullKeyword) {
    return { kind: "absent" };
  }
  if (ts.isIdentifier(node)) {
    if (seen.has(node.text)) return { kind: "unknown", reason: `cyclic ${node.text}` };
    const target = bindings.get(node.text);
    if (!target) return { kind: "unknown", reason: `unresolved options ${node.text}` };
    const next = new Set(seen);
    next.add(node.text);
    return methodFromOptions(target, bindings, next);
  }
  if (!ts.isObjectLiteralExpression(node)) {
    return { kind: "unknown", reason: `options are ${ts.SyntaxKind[node.kind]}` };
  }

  let result: Resolution = { kind: "absent" };
  for (const property of node.properties) {
    if (ts.isSpreadAssignment(property)) {
      const spread = methodFromOptions(property.expression, bindings, new Set(seen));
      if (spread.kind !== "absent") result = spread;
      continue;
    }
    if (ts.isShorthandPropertyAssignment(property)) {
      if (normalizedName(property.name.text) === "method") {
        result = stringValue(property.name, bindings, new Set(seen));
      }
      continue;
    }
    if (!ts.isPropertyAssignment(property)) continue;
    let name = propertyName(property.name);
    if (name === null && ts.isComputedPropertyName(property.name)) {
      const computed = stringValue(property.name.expression, bindings, new Set(seen));
      if (computed.kind === "unknown") {
        result = { kind: "unknown", reason: `computed option key: ${computed.reason}` };
        continue;
      }
      if (computed.kind === "known") name = computed.value;
    }
    if (name !== null && normalizedName(name) === "method") {
      result = stringValue(property.initializer, bindings, new Set(seen));
    }
  }
  return result;
}

function isFetchExpression(
  expression: ts.Expression,
  bindings: Map<string, ts.Expression>,
  seen: Set<string> = new Set(),
): boolean {
  const callee = unwrap(expression);
  if (ts.isIdentifier(callee)) {
    if (callee.text === "fetch") return true;
    if (seen.has(callee.text)) return false;
    const target = bindings.get(callee.text);
    if (!target) return false;
    const next = new Set(seen);
    next.add(callee.text);
    return isFetchExpression(target, bindings, next);
  }
  if (ts.isPropertyAccessExpression(callee)) return callee.name.text === "fetch";
  if (ts.isElementAccessExpression(callee) && callee.argumentExpression) {
    const key = stringValue(callee.argumentExpression, bindings, new Set());
    return key.kind === "known" && key.value === "fetch";
  }
  return false;
}

function requestMethod(
  expression: ts.Expression | undefined,
  bindings: Map<string, ts.Expression>,
  seen: Set<string> = new Set(),
): Resolution {
  if (!expression) return { kind: "absent" };
  const node = unwrap(expression);
  if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) {
    return { kind: "absent" };
  }
  if (ts.isIdentifier(node)) {
    if (seen.has(node.text)) return { kind: "unknown", reason: `cyclic ${node.text}` };
    const target = bindings.get(node.text);
    if (!target) return { kind: "unknown", reason: `unresolved request ${node.text}` };
    const next = new Set(seen);
    next.add(node.text);
    return requestMethod(target, bindings, next);
  }
  if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "Request") {
    return methodFromOptions(node.arguments?.[1], bindings, new Set(seen));
  }
  return { kind: "unknown", reason: `request input is ${ts.SyntaxKind[node.kind]}` };
}

function findingsIn(source: string, file = "fixture.tsx"): Finding[] {
  const kind = file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
  const parseDiagnostics = (sf as ts.SourceFile & { parseDiagnostics?: readonly ts.Diagnostic[] })
    .parseDiagnostics ?? [];
  expect(parseDiagnostics, `${file} did not parse`).toEqual([]);
  const bindings = bindingsOf(sf);
  const findings: Finding[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && isFetchExpression(node.expression, bindings)) {
      let method = methodFromOptions(node.arguments[1], bindings);
      if (method.kind === "absent") method = requestMethod(node.arguments[0], bindings);
      if (method.kind === "unknown") {
        const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
        findings.push({ file, line: pos.line + 1, method: `UNKNOWN: ${method.reason}` });
      } else if (method.kind === "known") {
        const normalized = method.value.replace(/[\s_]+/g, "").toLocaleUpperCase("en-US");
        if (!SAFE_METHODS.has(normalized)) {
          const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
          findings.push({ file, line: pos.line + 1, method: normalized });
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return findings;
}

function specImportsIn(source: string, file = "fixture.tsx"): string[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const imports: string[] = [];
  const record = (value: ts.Expression | undefined) => {
    if (value && ts.isStringLiteralLike(value) && /\.(test|spec)(?:\.tsx?)?$/.test(value.text)) {
      imports.push(value.text);
    }
  };
  const visit = (node: ts.Node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      record(node.moduleSpecifier);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword
    ) {
      record(node.arguments[0]);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return imports;
}

describe("raw state-changing fetch boundary", () => {
  it("defines a nonzero product/spec denominator independent of the verdict", () => {
    const all = sourceFiles(SRC);
    const product = all.filter((path) => !SPEC_FILE_RE.test(path));
    const excluded = all.filter((path) => SPEC_FILE_RE.test(path));
    expect(all.length).toBe(product.length + excluded.length);
    expect(product.length).toBeGreaterThan(50);
    expect(excluded.length).toBeGreaterThan(0);
    expect(product.some((path) => path.endsWith("/lib/api-client.ts"))).toBe(true);
  });

  it("catches lowercase and spacing/mixed-case state-changing methods", () => {
    const source = [
      'fetch("/api/lower", { method: "post", body: "{}" });',
      "fetch('/api/spaced', { method : 'DeLeTe', body: '{}' });",
    ].join("\n");
    expect((source.match(/fetch/g) ?? []).length).toBe(2);
    expect(findingsIn(source).map((f) => f.method)).toEqual(["POST", "DELETE"]);
  });

  it("catches local fetch/options aliases, computed keys, spreads, and Request indirection", () => {
    const source = `
      const verb = "pa" + "tch";
      const method = verb;
      const base = { method };
      const options = { ...base, body: "{}" };
      fetch("/api/options", options);
      const rawFetch = window.fetch;
      const methodKey = "me" + "thod";
      rawFetch("/api/alias", { [methodKey]: "delete", body: "{}" });
      const request = new Request("/api/request", { method: "put" });
      window.fetch(request);
    `;
    expect(findingsIn(source).map((f) => f.method)).toEqual(["PATCH", "DELETE", "PUT"]);
  });

  it("reports unresolved visible method indirection as UNKNOWN, never OK", () => {
    const source = `
      declare const methodFromRuntime: string;
      fetch("/api/unmeasured", { method: methodFromRuntime, body: "{}" });
      declare const optionKeyFromRuntime: string;
      fetch("/api/unmeasured-key", { [optionKeyFromRuntime]: "post", body: "{}" });
    `;
    const findings = findingsIn(source);
    expect(findings).toHaveLength(2);
    expect(findings[0].method).toMatch(/^UNKNOWN: unresolved methodFromRuntime$/);
    expect(findings[1].method).toMatch(
      /^UNKNOWN: computed option key: unresolved optionKeyFromRuntime$/,
    );
  });

  it("negative control ignores comments, prose strings, and explicit safe methods", () => {
    const source = `
      // fetch("/api/comment", { method: "POST" });
      const prose = 'fetch("/api/string", { method: "DELETE" })';
      fetch("/api/default");
      fetch("/api/get", { method: "get" });
      fetch("/api/head", { method: \`HEAD\` });
      void prose;
    `;
    expect(findingsIn(source)).toEqual([]);
  });

  it("finds no state-changing raw fetch in the complete SPA product population", () => {
    const product = sourceFiles(SRC).filter((path) => !SPEC_FILE_RE.test(path));
    expect(product.length).toBeGreaterThan(50);
    const findings: Finding[] = [];
    for (const path of product) {
      if (path.endsWith("/lib/api-client.ts")) continue;
      findings.push(...findingsIn(readFileSync(path, "utf-8"), path.slice(SRC.length + 1)));
    }
    expect(findings).toEqual([]);
  });

  it("keeps excluded spec modules outside the shipped product graph", () => {
    const planted = [
      'import x from "./one.test";',
      'export * from "./two.spec";',
      'const three = import("./three.test");',
      'const four = import("./four.spec.tsx");',
    ].join("\n");
    expect(specImportsIn(planted)).toEqual([
      "./one.test", "./two.spec", "./three.test", "./four.spec.tsx",
    ]);
    expect(specImportsIn('import x from "./ordinary";')).toEqual([]);

    const offenders: string[] = [];
    const product = sourceFiles(SRC).filter((path) => !SPEC_FILE_RE.test(path));
    expect(product.length).toBeGreaterThan(50);
    for (const path of product) {
      if (specImportsIn(readFileSync(path, "utf-8"), path).length > 0) {
        offenders.push(path.slice(SRC.length + 1));
      }
    }
    expect(offenders).toEqual([]);
  });
});
