// body_types.mjs -- what body does each frontend control ACTUALLY send?
//
// bd-body-contract could only prove the sliver where a control passes a LITERAL {}. That
// sliver alone held two dead controls (724 "Delete ALL jobs", 726 "Start import") -- each
// posting {} to an endpoint that demands a body, each failing 100% of the time behind a
// confirm gate, each scored WIRED by every ledger we own.
//
// The other ~133 call sites pass the body as a VARIABLE:
//
//     apiPost<BulkEnqueueResult>("/api/bulk/enqueue", req)   // req: BulkEnqueueRequest
//
// A regex cannot see into `req`. Four attempts to guess at it produced 7, then 99, then 36
// false positives -- every time because the denominator did not contain the question.
//
// The TYPE CHECKER contains it. `apiPost(path, payload: unknown)` declares nothing useful,
// but the ARGUMENT EXPRESSION at each call site has an inferred type, and the checker will
// hand it over. So: ask the compiler what the body IS, synthesize a type-correct sample,
// and let the Python side REPLAY it against the real app. Derive; do not assert.
//
// Emits JSON: [{file, fn, path, sample, keys, unknownType}]
//   sample      -- a type-directed body we can actually POST
//   unknownType -- true when the checker could not resolve it. That is an HONEST UNKNOWN
//                  and it stays UNKNOWN downstream. It is never quietly treated as OK.
import ts from "typescript";
import path from "node:path";

// v3.66.743 — ROOT must be ABSOLUTE. With a relative ROOT ('.') the tsconfig
// parse hands createProgram RELATIVE root-file names ('src/App.tsx'), and the
// walk's `fileName.includes("/src/")` filter — written for absolute names —
// silently drops every root file, leaving only the ~50 files that happened to
// be re-reached through import resolution under an absolute name. The
// extractor then reports its (stale) call-site count with a straight face,
// and the in-sync gate compares the committed artifact to a fresh run of the
// same blind scan: two artifacts agreeing on the same under-scan.
const ROOT = path.resolve(process.argv[2] || ".");
const CONFIG = path.join(ROOT, "tsconfig.json");
const MUTATORS = new Set(["apiPost", "apiPut", "apiPatch", "apiDelete"]);

const cfgFile = ts.readConfigFile(CONFIG, ts.sys.readFile);
const cfg = ts.parseJsonConfigFileContent(cfgFile.config, ts.sys, ROOT);
const program = ts.createProgram(cfg.fileNames, cfg.options);
const checker = program.getTypeChecker();

// A route argument is usually a template literal: `/api/sites/${sid}/bulk_pause`.
// Keep the literal segments and mark the holes -- the Python side substitutes a probe id.
function routeText(node) {
  if (ts.isStringLiteral(node)) return node.text;
  if (ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) {
    let out = node.head.text;
    for (const span of node.templateSpans) out += "${}" + span.literal.text;
    return out;
  }
  return null;
}

const seen = new Set();

// Type-directed sample synthesis. The point is a body the SERVER will accept if the
// control is sound -- so a 400 means the CONTROL is wrong, not that our placeholder was.
// (The previous probe filled every key with null and "found" 99 dead controls. All mine.)
function sample(type, depth = 0) {
  if (depth > 3) return "x";
  const f = type.flags;
  if (f & ts.TypeFlags.StringLike) return "x";
  if (f & ts.TypeFlags.NumberLike) return 1;
  if (f & ts.TypeFlags.BooleanLike) return false;
  if (f & ts.TypeFlags.Null) return null;
  if (type.isUnion()) {
    // A union of string literals is an enum: pick a REAL member, not a made-up string.
    const lit = type.types.find((t) => t.isStringLiteral());
    if (lit) return lit.value;
    const nonNull = type.types.find(
      (t) => !(t.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Null)),
    );
    return nonNull ? sample(nonNull, depth + 1) : "x";
  }
  const sym = type.getSymbol();
  if (sym && sym.getName() === "Array") {
    const arg = checker.getTypeArguments(type)[0];
    return arg ? [sample(arg, depth + 1)] : ["x"];
  }
  // Index-signature types (Record<string, X>) have NO properties -- falling through to the
  // scalar default emitted `flags: "x"` for an object param, which the server rightly
  // refused, and the probe then blamed the CONTROL. It was my placeholder. Emit {}.
  const idx = checker.getIndexInfosOfType ? checker.getIndexInfosOfType(type) : [];
  const props = type.getProperties();
  if (!props.length && idx && idx.length) return {};
  if (props.length) {
    const o = {};
    for (const p of props) {
      const pt = checker.getTypeOfSymbolAtLocation(p, p.valueDeclaration ?? p.declarations?.[0]);
      o[p.getName()] = sample(pt, depth + 1);
    }
    return o;
  }
  return "x";
}

const out = [];
for (const sf of program.getSourceFiles()) {
  if (sf.isDeclarationFile) continue;
  if (!sf.fileName.includes("/src/")) continue;
  if (/\.test\.tsx?$/.test(sf.fileName)) continue;

  ts.forEachChild(sf, function walk(node) {
    if (ts.isCallExpression(node)) {
      const name = ts.isIdentifier(node.expression)
        ? node.expression.text
        : ts.isPropertyAccessExpression(node.expression)
          ? node.expression.name.text
          : null;
      if (name && MUTATORS.has(name) && node.arguments.length >= 1) {
        const route = routeText(node.arguments[0]);
        if (route && route.startsWith("/")) {
          const bodyArg = node.arguments[1];
          let body = null;
          let unknownType = false;
          if (!bodyArg) {
            body = {};
          } else {
            const t = checker.getTypeAtLocation(bodyArg);
            const props = t.getProperties();
            if (t.flags & ts.TypeFlags.Unknown || t.flags & ts.TypeFlags.Any) {
              unknownType = true;
              body = null;
            } else if (!props.length && !(t.flags & ts.TypeFlags.Object)) {
              unknownType = true;
              body = null;
            } else if (!props.length &&
                       checker.getIndexInfosOfType &&
                       checker.getIndexInfosOfType(t).length) {
              // Record<string, unknown> at the TOP LEVEL is an OPEN DICT: its keys are
              // whatever the caller passes, and they are NOT knowable statically. Emitting
              // {} for it made two sound controls (marketplace/import + preview) look like
              // they send an empty body -- the SAME conflation of "no keys" with "keys I
              // cannot see" that has produced a false positive at every previous attempt.
              // An open dict is UNKNOWN. Say so.
              unknownType = true;
              body = null;
            } else {
              body = sample(t);
              if (typeof body !== "object" || body === null || Array.isArray(body)) {
                unknownType = true;
                body = null;
              }
            }
          }
          const rel = path.relative(ROOT, sf.fileName);
          const key = rel + "|" + name + "|" + route;
          if (!seen.has(key)) {
            seen.add(key);
            out.push({
              file: rel,
              fn: name,
              path: route,
              sample: body,
              keys: body ? Object.keys(body) : [],
              unknownType,
            });
          }
        }
      }
    }
    ts.forEachChild(node, walk);
  });
}

process.stdout.write(JSON.stringify(out, null, 1));
