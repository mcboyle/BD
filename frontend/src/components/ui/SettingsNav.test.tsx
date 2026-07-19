import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsNav } from "./SettingsNav";

// Cut 5 — SettingsNav (mini-ToC): lists ALL page sections; changed-markers fire
// only on config-backed sections passed in `changedSections`.

const sections = [
  { id: "downloads", label: "Downloads" },
  { id: "ai-assist", label: "AI assist" },
  { id: "system", label: "System" },
];

describe("SettingsNav (Cut 5)", () => {
  it("lists every provided section label", () => {
    render(<SettingsNav sections={sections} changedSections={new Set()} onNavigate={() => {}} />);
    for (const s of sections) expect(screen.getByText(s.label)).toBeInTheDocument();
  });

  it("renders a changed-marker only on sections in changedSections", () => {
    render(
      <SettingsNav
        sections={sections}
        changedSections={new Set(["downloads"])}
        onNavigate={() => {}}
      />
    );
    const markers = screen.getAllByTestId("settingsnav-changed-marker");
    expect(markers).toHaveLength(1);
  });

  it("calls onNavigate(id) when a section is clicked", () => {
    const onNavigate = vi.fn();
    render(<SettingsNav sections={sections} changedSections={new Set()} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByText("AI assist"));
    expect(onNavigate).toHaveBeenCalledWith("ai-assist");
  });

  it("chips variant lists all sections and marks changed ones", () => {
    const onNavigate = vi.fn();
    render(
      <SettingsNav
        sections={sections}
        changedSections={new Set(["downloads"])}
        onNavigate={onNavigate}
        variant="chips"
      />
    );
    for (const s of sections) expect(screen.getByText(s.label)).toBeInTheDocument();
    expect(screen.getAllByTestId("settingsnav-changed-marker")).toHaveLength(1);
    fireEvent.click(screen.getByText("System"));
    expect(onNavigate).toHaveBeenCalledWith("system");
  });
});
