import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// v3.66.498 O1 — the auto-GUI form renderer. Drives each control type off the
// normalized form model (the server-side plugins.plugin_config_schemas output).
import {
  PluginConfigForm,
  type PluginConfigField,
} from "./PluginConfigForm";

const FIELDS: PluginConfigField[] = [
  { name: "endpoint", type: "text", label: "Endpoint URL", default: "https://x", required: true, enum: [], help: "Where to POST" },
  { name: "retries", type: "number", label: "retries", default: 3, required: false, enum: [] },
  { name: "verbose", type: "checkbox", label: "Verbose logging", default: false, required: false, enum: [] },
  { name: "mode", type: "select", label: "mode", default: "safe", required: false, enum: ["fast", "safe"] },
];

describe("PluginConfigForm (O1)", () => {
  it("renders a control per field with labels and required marker", () => {
    render(<PluginConfigForm fields={FIELDS} values={{}} onChange={() => {}} />);
    expect(screen.getByLabelText(/Endpoint URL/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Verbose logging/)).toBeInTheDocument();
    // required field shows the asterisk
    expect(screen.getByText("*")).toBeInTheDocument();
  });

  it("falls back to the field default when no value is set", () => {
    render(<PluginConfigForm fields={FIELDS} values={{}} onChange={() => {}} />);
    expect(screen.getByLabelText(/Endpoint URL/)).toHaveValue("https://x");
    expect(screen.getByLabelText(/Verbose logging/)).not.toBeChecked();
  });

  it("renders enum choices as a select", () => {
    render(<PluginConfigForm fields={FIELDS} values={{}} onChange={() => {}} />);
    const sel = screen.getByLabelText("mode") as HTMLSelectElement;
    expect(sel.tagName).toBe("SELECT");
    expect(sel.value).toBe("safe");
    expect(screen.getByRole("option", { name: "fast" })).toBeInTheDocument();
  });

  it("emits onChange with the typed value", () => {
    const onChange = vi.fn();
    render(<PluginConfigForm fields={FIELDS} values={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/Endpoint URL/), {
      target: { value: "https://new" },
    });
    expect(onChange).toHaveBeenCalledWith("endpoint", "https://new");
    fireEvent.click(screen.getByLabelText(/Verbose logging/));
    expect(onChange).toHaveBeenCalledWith("verbose", true);
  });

  it("shows an empty-state when there are no fields", () => {
    render(<PluginConfigForm fields={[]} values={{}} onChange={() => {}} />);
    expect(screen.getByText(/No configurable options/)).toBeInTheDocument();
  });
});
