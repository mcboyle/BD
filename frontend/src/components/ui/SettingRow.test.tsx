import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { SettingRow } from "../SettingSection";

// P6-8: SettingRow badges a setting whose saved value differs from the shipped
// default. The badge is presentational (no behavior change) and only shows when
// `modified` is true.

describe("SettingRow modified badge (P6-8)", () => {
  it("shows a 'modified' badge when modified=true", () => {
    render(<SettingRow label="Endpoint" control={<input />} modified />);
    expect(screen.getByText("modified")).toBeInTheDocument();
    expect(screen.getByText("Endpoint")).toBeInTheDocument();
  });

  it("does not show the badge by default", () => {
    render(<SettingRow label="Endpoint" control={<input />} />);
    expect(screen.queryByText("modified")).not.toBeInTheDocument();
  });

  it("does not show the badge when modified=false", () => {
    render(<SettingRow label="Endpoint" control={<input />} modified={false} />);
    expect(screen.queryByText("modified")).not.toBeInTheDocument();
  });
});
