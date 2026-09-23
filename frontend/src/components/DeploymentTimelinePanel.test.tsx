import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  ApiError: class extends Error {},
}));

import { DeploymentTimelinePanel } from "./DeploymentTimelinePanel";

// The shapes GET /api/deploy/timeline returns after a boot replaced a revision
// (deployment_timeline.record_boot_revision): newest first.
const ACTIVE = {
  deployment_id: "dep-new", revision: "3.66.1655+abc123def456", target_env: "production",
  state: "active", created_at: 1790150000, updated_at: 1790150000, progress_percent: 100,
};
const PREVIOUS = {
  deployment_id: "dep-old", revision: "3.66.1654+0123456789ab", target_env: "production",
  state: "rolled_back", created_at: 1790000000, updated_at: 1790150000, progress_percent: 100,
};
const entry = (ts: number, kind: string, severity: string, description: string, title = `Deployment (production): ${kind}`) =>
  ({ ts, source: "deployment_rollout", kind, severity, title, description, link: "/api/deploy/timeline" });

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DeploymentTimelinePanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
});

describe("DeploymentTimelinePanel (row 1057)", () => {
  it("shows the running revision, the revision history and the rollout events from GET /api/deploy/timeline", async () => {
    apiGetMock.mockResolvedValue({
      active_deployment: ACTIVE,
      deployments: [ACTIVE, PREVIOUS],
      timeline: [
        entry(1790150002, "rollback_completed", "warn", "Replaced by revision 3.66.1655+abc123def456"),
        entry(1790150001, "rollout_completed", "info", "Revision 3.66.1655+abc123def456 started on this host"),
        entry(1790150000, "rollout_failed", "error", "", "Deployment 3.66.1656 (production): rollout_failed"),
      ],
    });
    mount();
    const running = await screen.findByText(/^Running/);
    expect(running).toHaveTextContent("Running 3.66.1655+abc123def456 in production since");
    expect(running).toHaveAttribute("data-state", "active");
    expect(apiGetMock).toHaveBeenCalledWith("/api/deploy/timeline");

    const history = within(screen.getByRole("list", { name: "Revision history" })).getAllByRole("listitem");
    expect(history.map((li) => li.getAttribute("data-state"))).toEqual(["active", "rolled_back"]);
    expect(history[1]).toHaveTextContent("3.66.1654+0123456789ab");
    expect(history[1]).toHaveTextContent("production · rolled back ·");

    const events = within(screen.getByRole("list", { name: "Rollout events" })).getAllByRole("listitem");
    expect(events.map((li) => li.getAttribute("data-severity"))).toEqual(["warn", "info", "error"]);
    expect(events[0]).toHaveTextContent("Replaced by revision 3.66.1655+abc123def456");
    expect(events[0]).toHaveClass("text-amber-dim");
    expect(events[1]).toHaveAttribute("class", "text-xs"); // info: no warn/error colour
    // an event without a message falls back to its title
    expect(events[2]).toHaveTextContent("Deployment 3.66.1656 (production): rollout_failed");
    expect(events[2]).toHaveClass("text-danger");
  });

  it("says no deployment is recorded yet when the timeline is empty", async () => {
    apiGetMock.mockResolvedValue({ active_deployment: null, deployments: [], timeline: [] });
    mount();
    expect(await screen.findByText(/No active deployment recorded yet/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("treats a payload without the lists as an empty timeline", async () => {
    apiGetMock.mockResolvedValue({});
    mount();
    expect(await screen.findByText(/No active deployment recorded yet/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("reports a failed read instead of showing an empty timeline", async () => {
    apiGetMock.mockRejectedValue(new Error("GET /api/deploy/timeline → 500"));
    mount();
    expect(
      await screen.findByText("Could not read the deployment timeline: GET /api/deploy/timeline → 500"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No active deployment recorded yet/)).not.toBeInTheDocument();
  });

  it("lists only the ten newest revisions and events", async () => {
    const deployments = Array.from({ length: 12 }, (_, i) => ({ ...PREVIOUS, deployment_id: `dep-${i}`, revision: `r${i}` }));
    const timeline = Array.from({ length: 12 }, (_, i) => entry(1790150000 - i, "stage_updated", "info", `event ${i}`));
    apiGetMock.mockResolvedValue({ active_deployment: null, deployments, timeline });
    mount();
    const history = await screen.findByRole("list", { name: "Revision history" });
    expect(within(history).getAllByRole("listitem")).toHaveLength(10);
    expect(within(history).getByText("r0")).toBeInTheDocument();
    expect(within(history).queryByText("r10")).not.toBeInTheDocument();
    const events = screen.getByRole("list", { name: "Rollout events" });
    expect(within(events).getAllByRole("listitem")).toHaveLength(10);
    expect(within(events).queryByText(/event 10/)).not.toBeInTheDocument();
  });
});
