import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelSelect } from "./ModelSelect";

// Cut 7 (7.1) — ModelSelect: an editable combobox (native <input list> +
// <datalist>) for the AI model fields. It SUGGESTS detected models while
// preserving free-text entry (long hf.co/<org>/<repo>:<quant> tags), so it must
// stay usable even with zero options and never block the surrounding save.

function getInput(): HTMLInputElement {
  return screen.getByRole("combobox") as HTMLInputElement;
}

describe("ModelSelect (Cut 7 / 7.1)", () => {
  it("renders the current value", () => {
    render(<ModelSelect value="qwen2.5:7b" options={[]} onChange={() => {}} />);
    expect(getInput()).toHaveValue("qwen2.5:7b");
  });

  it("exposes each option inside a datalist", () => {
    const { container } = render(
      <ModelSelect value="" options={["qwen2.5:7b", "qwen2.5vl:7b"]} onChange={() => {}} />,
    );
    const opts = container.querySelectorAll("datalist option");
    const values = Array.from(opts).map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["qwen2.5:7b", "qwen2.5vl:7b"]);
    // input is bound to the datalist via list=
    const list = container.querySelector("datalist")?.id;
    expect(getInput().getAttribute("list")).toBe(list);
  });

  it("fires onChange with a custom (non-listed) value", () => {
    const onChange = vi.fn();
    render(<ModelSelect value="" options={["qwen2.5:7b"]} onChange={onChange} />);
    fireEvent.change(getInput(), { target: { value: "hf.co/org/repo:Q4_K_M" } });
    expect(onChange).toHaveBeenCalledWith("hf.co/org/repo:Q4_K_M");
  });

  it("honors disabled", () => {
    render(<ModelSelect value="x" options={[]} onChange={() => {}} disabled />);
    expect(getInput()).toBeDisabled();
  });

  it("stays editable with empty options", () => {
    const onChange = vi.fn();
    render(<ModelSelect value="" options={[]} onChange={onChange} />);
    expect(getInput()).not.toBeDisabled();
    fireEvent.change(getInput(), { target: { value: "anything" } });
    expect(onChange).toHaveBeenCalledWith("anything");
  });

  it("shows a hint while loading", () => {
    render(<ModelSelect value="" options={[]} onChange={() => {}} loading />);
    expect(screen.getByText(/detecting|loading/i)).toBeInTheDocument();
  });
});
