import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title (the one line)", () => {
    render(<EmptyState title="No history yet" />);
    expect(screen.getByText("No history yet")).toBeInTheDocument();
  });

  it("renders the explanation hint when given", () => {
    render(<EmptyState title="No sites yet" hint="Add a site to start a capture." />);
    expect(screen.getByText("Add a site to start a capture.")).toBeInTheDocument();
  });

  it("renders a marker icon by default (svg present)", () => {
    const { container } = render(<EmptyState title="Empty" />);
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("uses a provided marker icon", () => {
    const { container } = render(<EmptyState icon={Inbox} title="Empty" />);
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("renders a primary action button and fires its onClick", () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="No sites yet"
        action={{ label: "Add your first site", onClick }}
      />,
    );
    const btn = screen.getByRole("button", { name: /add your first site/i });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders no action button when no action is given", () => {
    render(<EmptyState title="Nothing yet" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("accepts a custom action node", () => {
    render(
      <EmptyState
        title="Empty"
        action={<a href="/sites">go to sites</a>}
      />,
    );
    expect(screen.getByText("go to sites")).toBeInTheDocument();
  });

  it("compact variant still renders title + hint", () => {
    render(<EmptyState compact title="All clear" hint="Nothing needs attention." />);
    expect(screen.getByText("All clear")).toBeInTheDocument();
    expect(screen.getByText("Nothing needs attention.")).toBeInTheDocument();
  });
});
