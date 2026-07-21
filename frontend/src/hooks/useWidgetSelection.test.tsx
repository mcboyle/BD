import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useWidgetSelection } from "./useWidgetSelection";

function SelectionPeers() {
  const first = useWidgetSelection();
  const second = useWidgetSelection();

  return (
    <>
      <button type="button" onClick={() => first.add("lib_top_studio")}>
        Add from first hook
      </button>
      <output data-testid="second-selection">
        {second.ids.includes("lib_top_studio") ? "selected" : "missing"}
      </output>
    </>
  );
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

it("syncs two same-tab hooks even when localStorage persistence throws", async () => {
  window.localStorage.clear();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("storage blocked", "QuotaExceededError");
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <SelectionPeers />
    </QueryClientProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Add from first hook" }));

  await waitFor(() => {
    expect(screen.getByTestId("second-selection")).toHaveTextContent("selected");
  });
});
