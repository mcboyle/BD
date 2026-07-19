import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BottomTabBar } from "./BottomTabBar";

// Slice 4b — mobile "More" overflow. The 5 frozen tabs stay; a 6th "More"
// button opens a bottom-sheet drawer exposing the grouped nav (the long tail
// that was ⌘K-only on mobile). Drawer must live OUTSIDE the backdrop-blur bar
// (footgun[2]: backdrop-filter creates a containing block, which would trap a
// position:fixed drawer to the bar instead of the viewport).

function renderBar() {
  return render(
    <MemoryRouter>
      <BottomTabBar />
    </MemoryRouter>,
  );
}

describe("BottomTabBar — frozen 5 tabs preserved", () => {
  it("still renders all 5 primary tabs", () => {
    renderBar();
    for (const name of ["Home", "Sites", "Queue", "Activity", "Settings"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });
});

describe("BottomTabBar — More overflow drawer", () => {
  it("renders a More button", () => {
    renderBar();
    expect(screen.getByRole("button", { name: /more/i })).toBeInTheDocument();
  });

  it("opens the drawer with the grouped nav on click", () => {
    renderBar();
    // closed initially — a grouped route is not present
    expect(screen.queryByRole("link", { name: "Capture" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    // group label + items now visible
    expect(screen.getByText("Network & security")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Capture" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "VPN" })).toBeInTheDocument();
  });

  it("closes the drawer when a destination is chosen", () => {
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("link", { name: "Capture" }));
    expect(screen.queryByRole("link", { name: "Capture" })).not.toBeInTheDocument();
  });

  it("closes the drawer on Escape", () => {
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    expect(screen.getByRole("link", { name: "VPN" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("link", { name: "VPN" })).not.toBeInTheDocument();
  });

  it("renders the drawer OUTSIDE the backdrop-blur bar (footgun[2])", () => {
    const { container } = renderBar();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    // The blurred nav must NOT contain the drawer's grouped links.
    const blurred = container.querySelector("[class*='backdrop-blur']");
    expect(blurred).toBeTruthy();
    const capture = screen.getByRole("link", { name: "Capture" });
    expect(blurred!.contains(capture)).toBe(false);
  });
});
