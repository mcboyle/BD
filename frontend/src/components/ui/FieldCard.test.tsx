import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { FieldCard } from "./FieldCard";

// Slice 4c.4: the shared labeled-field-card. Consolidates the per-page ad-hoc
// label/hint markup (seeded from AddSiteWizard's Field) and adds an inline
// validation slot: when `error` is set it replaces the hint with a role=alert
// message. Presentational only — pages own their validation logic.

describe("FieldCard (Slice 4c.4)", () => {
  it("renders the label and a hint when no error", () => {
    render(
      <FieldCard label="Watch folder" hint="Where new files land">
        <input aria-label="Watch folder input" />
      </FieldCard>,
    );
    expect(screen.getByText("Watch folder")).toBeInTheDocument();
    expect(screen.getByText("Where new files land")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an inline error (role=alert) and suppresses the hint when error is set", () => {
    render(
      <FieldCard label="Endpoint" hint="The base URL" error="Must start with https://">
        <input aria-label="Endpoint input" />
      </FieldCard>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Must start with https://");
    expect(screen.queryByText("The base URL")).not.toBeInTheDocument();
  });

  it("marks required fields", () => {
    render(
      <FieldCard label="Name" required>
        <input aria-label="Name input" />
      </FieldCard>,
    );
    // label text + asterisk
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("*")).toBeInTheDocument();
  });
});
