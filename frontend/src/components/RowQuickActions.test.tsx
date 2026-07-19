import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Cut 6.6 — inline row quick-actions (hover) for queue rows. Surfaces
// pause/resume/capture-now affordances that call existing mutation endpoints
// via the provided handlers. Labelled for a11y (icon-only buttons need names).
import { RowQuickActions } from "./RowQuickActions";

describe("RowQuickActions (Cut 6.6)", () => {
  it("renders the provided quick actions as labelled buttons", () => {
    render(
      <RowQuickActions
        onPause={() => {}}
        onResume={() => {}}
        onCaptureNow={() => {}}
        paused={false}
      />,
    );
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /capture now/i })).toBeInTheDocument();
  });

  it("clicking pause fires onPause", () => {
    const onPause = vi.fn();
    render(
      <RowQuickActions onPause={onPause} onResume={() => {}} onCaptureNow={() => {}} paused={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(onPause).toHaveBeenCalledTimes(1);
  });

  it("shows resume (not pause) when the row is paused", () => {
    render(
      <RowQuickActions onPause={() => {}} onResume={() => {}} onCaptureNow={() => {}} paused={true} />,
    );
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^pause$/i })).toBeNull();
  });
});
