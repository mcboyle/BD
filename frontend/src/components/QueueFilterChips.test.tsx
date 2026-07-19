import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Cut 6.6 — removable filter chips for the Queue. Each active filter renders as
// a chip with a remove affordance; removing one fires onRemove(key) so the page
// can drop it from the URL-encoded view (Cut 6.4).
import { QueueFilterChips } from "./QueueFilterChips";

type Chip = { key: string; label: string };

describe("QueueFilterChips (Cut 6.6)", () => {
  it("renders one chip per active filter", () => {
    const chips: Chip[] = [
      { key: "status", label: "Status: failed" },
      { key: "site", label: "Site: example" },
    ];
    render(<QueueFilterChips chips={chips} onRemove={() => {}} />);
    expect(screen.getByText("Status: failed")).toBeInTheDocument();
    expect(screen.getByText("Site: example")).toBeInTheDocument();
  });

  it("removing a chip fires onRemove with its key", () => {
    const onRemove = vi.fn();
    render(
      <QueueFilterChips
        chips={[{ key: "status", label: "Status: failed" }]}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /remove status/i }));
    expect(onRemove).toHaveBeenCalledWith("status");
  });

  it("renders nothing when there are no active filters", () => {
    const { container } = render(<QueueFilterChips chips={[]} onRemove={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
