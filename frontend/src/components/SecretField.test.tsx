import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SecretField } from "./SecretField";

// Slice 4c: SecretField is the single write-only secret input. The sweep
// converts the raw <Input type="password"> sites across Settings / Backup /
// Notifications / SiteSettings / AddSiteWizard to it, so it must forward the
// per-site className and keyboard handler the call sites need — while keeping
// the always-password / never-autofill / aria-labelled contract.

describe("SecretField contract (Slice 4c)", () => {
  it("is always a password input that browsers won't autofill, with an aria-label", () => {
    render(<SecretField value="" onChange={() => {}} ariaLabel="Master password" />);
    const input = screen.getByLabelText("Master password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "new-password");
  });

  it("forwards a caller className (so width/spacing per-site is preserved)", () => {
    render(
      <SecretField value="" onChange={() => {}} ariaLabel="API key" className="w-64" />,
    );
    expect(screen.getByLabelText("API key")).toHaveClass("w-64");
  });

  it("reports the typed value via onChange(value)", () => {
    const onChange = vi.fn();
    render(<SecretField value="" onChange={onChange} ariaLabel="Token" />);
    fireEvent.change(screen.getByLabelText("Token"), { target: { value: "abc" } });
    expect(onChange).toHaveBeenCalledWith("abc");
  });

  it("forwards onKeyDown (enter-to-submit call sites)", () => {
    const onKeyDown = vi.fn();
    render(
      <SecretField value="" onChange={() => {}} ariaLabel="Unlock" onKeyDown={onKeyDown} />,
    );
    fireEvent.keyDown(screen.getByLabelText("Unlock"), { key: "Enter" });
    expect(onKeyDown).toHaveBeenCalled();
  });
});
