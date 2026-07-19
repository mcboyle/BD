import { cn } from "@/lib/utils";

// Cut 6.1 — Dashboard version-truth panel. Compares the FE-loaded build stamp
// (import.meta.env.VITE_BUILD_STAMP, threaded in as `buildStamp`) against the
// backend's reported build sha (`/api/health` -> build.sha).
//
// SPEC-CRITICAL: an ABSENT FE stamp (null) is the normal state for any build
// that didn't bake VITE_BUILD_STAMP (dev tree, render harness). In that case we
// show the versions and make NO mismatch/stale claim — we cannot know. A claim
// is only made when BOTH the FE stamp and the backend sha are present.
//
// Read-only: this panel never renders a deploy/restart/rebuild control.

export interface BuildInfo {
  sha: string;
  built_at: string;
}

export function BuildTruthPanel({
  version,
  build,
  buildStamp,
}: {
  version: string;
  build?: BuildInfo;
  buildStamp: string | null;
}) {
  const backendSha = build?.sha ?? null;
  const status: "match" | "stale" | null =
    buildStamp && backendSha ? (buildStamp === backendSha ? "match" : "stale") : null;

  const short = (s: string) => (s.length > 12 ? s.slice(0, 12) : s);

  return (
    <section
      className="rounded-md hairline bg-surface p-4 text-sm"
      aria-label="Build version"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-ink-3">Version</span>
        <span className="font-semibold tabular-nums text-ink">{version}</span>
      </div>

      {backendSha ? (
        <div className="mt-1 flex items-baseline justify-between gap-2">
          <span className="text-ink-3">Backend build</span>
          <code className="text-xs text-ink-2">{short(backendSha)}</code>
        </div>
      ) : null}

      {buildStamp ? (
        <div className="mt-1 flex items-baseline justify-between gap-2">
          <span className="text-ink-3">Loaded UI</span>
          <code className="text-xs text-ink-2">{short(buildStamp)}</code>
        </div>
      ) : null}

      {status ? (
        <p
          className={cn(
            "mt-2 text-xs",
            status === "match" ? "text-green" : "text-amber-dim",
          )}
        >
          {status === "match"
            ? "UI up to date with the backend build."
            : "Loaded UI is stale — reload to pick up the latest build."}
        </p>
      ) : null}
    </section>
  );
}
