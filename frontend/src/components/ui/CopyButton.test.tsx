import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CopyButton } from "./CopyButton";

// Cut 5 — CopyButton: copies paths/env values; REFUSES secret fields
// (paths/env only, never secrets).

const writeText = vi.fn();
beforeEach(() => {
  writeText.mockReset();
  Object.assign(navigator, { clipboard: { writeText } });
});

describe("CopyButton (Cut 5)", () => {
  it("copies the value on click", () => {
    render(<CopyButton value="/srv/downloads" />);
    fireEvent.click(screen.getByRole("button"));
    expect(writeText).toHaveBeenCalledWith("/srv/downloads");
  });

  it("refuses secret fields: disabled and never writes to the clipboard", () => {
    render(<CopyButton value="should-not-copy" secret />);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(writeText).not.toHaveBeenCalled();
  });
});
