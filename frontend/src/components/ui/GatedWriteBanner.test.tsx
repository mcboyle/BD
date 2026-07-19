import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GatedWriteBanner } from "./GatedWriteBanner";

// Polish pass item 2 — the gated-write note is now COMPACT: a single amber
// line (role=note) with the full detail behind a Details disclosure and a
// per-session dismiss. Safety meaning + full text preserved, weight reduced.

describe("GatedWriteBanner (compact)", () => {
  it("renders a compact note (role=note) with the default lead + review line", () => {
    render(
      <GatedWriteBanner>Destructive actions require confirmation.</GatedWriteBanner>,
    );
    expect(screen.getByRole("note")).toBeInTheDocument();
    expect(screen.getByText("Gated writes enabled")).toBeInTheDocument();
    expect(
      screen.getByText(/Review required before changes apply/),
    ).toBeInTheDocument();
  });

  it("keeps the full detail behind Details (hidden until expanded)", () => {
    render(
      <GatedWriteBanner>Destructive actions require confirmation.</GatedWriteBanner>,
    );
    // collapsed: detail not in the document
    expect(
      screen.queryByText("Destructive actions require confirmation."),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Details"));
    // expanded: detail now shown
    expect(
      screen.getByText("Destructive actions require confirmation."),
    ).toBeInTheDocument();
  });

  it("accepts a title override", () => {
    render(<GatedWriteBanner title="Operator control surface">body</GatedWriteBanner>);
    expect(screen.getByText("Operator control surface")).toBeInTheDocument();
  });

  it("is per-session dismissible", () => {
    render(
      <GatedWriteBanner dismissKey="test-dismiss-1">x</GatedWriteBanner>,
    );
    expect(screen.getByRole("note")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Dismiss for this session"));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("uses the themed amber surface (not raw amber-950)", () => {
    const { container } = render(<GatedWriteBanner>x</GatedWriteBanner>);
    const root = container.querySelector('[role="note"]');
    expect(root?.className).not.toContain("amber-950");
    expect(root?.className).toContain("amber-soft");
  });

  // Convergence #4 — the chip tier reduces visual weight only: the full
  // safety text stays behind Details and per-session dismiss still works.
  it("chip level keeps the full detail behind Details", () => {
    render(
      <GatedWriteBanner level="chip">
        Destructive actions require confirmation.
      </GatedWriteBanner>,
    );
    expect(screen.getByRole("note")).toBeInTheDocument();
    // collapsed: detail hidden
    expect(
      screen.queryByText("Destructive actions require confirmation."),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Details"));
    expect(
      screen.getByText("Destructive actions require confirmation."),
    ).toBeInTheDocument();
  });

  it("chip level is per-session dismissible", () => {
    render(
      <GatedWriteBanner level="chip" dismissKey="test-chip-dismiss">
        x
      </GatedWriteBanner>,
    );
    expect(screen.getByRole("note")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Dismiss for this session"));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  // Cut 1: `shape` is the routeRisk-aligned alias for `level` (call-sites pass
  // routeRisk(path).bannerShape). Back-compat: `level` still works; `shape`
  // wins when both are given. The full banner carries a "Review required..."
  // subtitle the chip omits — use that to distinguish the two renders.
  it("shape='chip' renders the chip (no full-bar subtitle)", () => {
    render(
      <GatedWriteBanner shape="chip" dismissKey="test-shape-chip">
        chip via shape
      </GatedWriteBanner>,
    );
    expect(screen.getByRole("note")).toBeInTheDocument();
    expect(
      screen.queryByText(/Review required before changes apply/),
    ).not.toBeInTheDocument();
  });

  it("shape overrides level when both are provided", () => {
    render(
      <GatedWriteBanner shape="chip" level="full" dismissKey="test-shape-wins">
        x
      </GatedWriteBanner>,
    );
    // shape='chip' wins -> the full-bar subtitle must NOT render.
    expect(
      screen.queryByText(/Review required before changes apply/),
    ).not.toBeInTheDocument();
  });
});
