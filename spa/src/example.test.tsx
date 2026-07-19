// Minimal example test to verify vitest + react-testing-library wire-up.
// Delete or replace with real BulkDL UI tests.
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

function HelloButton({ name }: { name: string }) {
  return <button>Hi, {name}</button>;
}

describe("HelloButton", () => {
  it("renders the name", () => {
    render(<HelloButton name="Matt" />);
    expect(screen.getByRole("button")).toHaveTextContent("Hi, Matt");
  });
});
