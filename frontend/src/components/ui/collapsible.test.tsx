import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Collapsible } from "./collapsible";

// Slice 4a — Collapsible gains persistence + active-route awareness so the
// desktop nav groups remember their open/closed state across navigations and
// auto-surface the group containing the current route.

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("Collapsible — persistence (persistKey)", () => {
  it("persists the open state to localStorage when toggled", () => {
    render(
      <Collapsible title="Network" persistKey="nav-network">
        <div>vpn-link</div>
      </Collapsible>,
    );
    // Closed by default → body not mounted.
    expect(screen.queryByText("vpn-link")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /network/i }));
    expect(screen.getByText("vpn-link")).toBeInTheDocument();
    expect(window.localStorage.getItem("bd-collapsible:nav-network")).toBe("1");
  });

  it("restores the stored open state on mount", () => {
    window.localStorage.setItem("bd-collapsible:nav-network", "1");
    render(
      <Collapsible title="Network" persistKey="nav-network">
        <div>vpn-link</div>
      </Collapsible>,
    );
    // Stored "1" wins over the default-closed → body mounted on first render.
    expect(screen.getByText("vpn-link")).toBeInTheDocument();
  });
});

describe("Collapsible — active-route awareness", () => {
  it("forceOpen renders the body even when stored/closed", () => {
    window.localStorage.setItem("bd-collapsible:nav-network", "0");
    render(
      <Collapsible title="Network" persistKey="nav-network" forceOpen>
        <div>vpn-link</div>
      </Collapsible>,
    );
    expect(screen.getByText("vpn-link")).toBeInTheDocument();
  });

  it("active marks the header with data-active for styling", () => {
    render(
      <Collapsible title="Network" active>
        <div>vpn-link</div>
      </Collapsible>,
    );
    const header = screen.getByRole("button", { name: /network/i });
    expect(header).toHaveAttribute("data-active", "true");
  });

  it("no data-active when not active", () => {
    render(
      <Collapsible title="Network">
        <div>vpn-link</div>
      </Collapsible>,
    );
    const header = screen.getByRole("button", { name: /network/i });
    expect(header).not.toHaveAttribute("data-active", "true");
  });
});
