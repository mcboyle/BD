import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";

// Cut 6.1 — Dashboard version-truth panel.
// Compares the FE-loaded build stamp (import.meta.env.VITE_BUILD_STAMP, passed
// in as `buildStamp`) against the backend's reported build sha (health.build.sha).
// SPEC-CRITICAL: when the FE stamp is ABSENT (null — the dev/render-harness band,
// and any build that didn't bake VITE_BUILD_STAMP), the panel must show the
// versions WITHOUT making any mismatch/stale claim. Read-only, no deploy controls.
import { BuildTruthPanel } from "./BuildTruthPanel";

type Build = { sha: string; built_at: string } | undefined;

function mount(opts: { version: string; build: Build; buildStamp: string | null }) {
  return render(
    <BuildTruthPanel
      version={opts.version}
      build={opts.build}
      buildStamp={opts.buildStamp}
    />,
  );
}

describe("BuildTruthPanel (Cut 6.1)", () => {
  it("absent FE stamp -> shows versions, makes NO mismatch/stale claim", () => {
    mount({ version: "3.66.376", build: { sha: "2b90cfbb", built_at: "x" }, buildStamp: null });
    expect(screen.getByText(/3\.66\.376/)).toBeInTheDocument();
    expect(screen.queryByText(/mismatch/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument();
  });

  it("stamp === backend sha -> reports an up-to-date/match state", () => {
    mount({ version: "3.66.376", build: { sha: "2b90cfbb", built_at: "x" }, buildStamp: "2b90cfbb" });
    expect(screen.getByText(/match|up to date/i)).toBeInTheDocument();
    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument();
  });

  it("stamp !== backend sha -> reports a stale-FE advisory", () => {
    mount({ version: "3.66.376", build: { sha: "2b90cfbb", built_at: "x" }, buildStamp: "deadbeef" });
    expect(screen.getByText(/stale|mismatch|reload/i)).toBeInTheDocument();
  });

  it("backend build absent -> versions only, no claim, never throws", () => {
    mount({ version: "3.66.376", build: undefined, buildStamp: null });
    expect(screen.getByText(/3\.66\.376/)).toBeInTheDocument();
    expect(screen.queryByText(/mismatch/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument();
  });

  it("is read-only — exposes no deploy/restart/rebuild control", () => {
    mount({ version: "3.66.376", build: { sha: "2b90cfbb", built_at: "x" }, buildStamp: "deadbeef" });
    expect(screen.queryByRole("button", { name: /deploy|restart|rebuild/i })).toBeNull();
  });
});
