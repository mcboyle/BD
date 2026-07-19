// Parse the Path-allowlist editor (one root per line) into the array the
// /api/global_config save expects. Roots must be absolute and free of "..".
// A blank value means an empty allowlist (any absolute non-traversing path).
export function parsePathAllowlist(
  raw: string,
): { roots: string[] } | { error: string } {
  const roots = raw
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const bad = roots.find((r) => !r.startsWith("/") || r.includes(".."));
  if (bad) {
    return { error: `Each root must be an absolute path with no "..": ${bad}` };
  }
  return { roots };
}
