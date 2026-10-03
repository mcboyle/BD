import { useEffect } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SitesV2 } from "@/lib/api-types";
import { CommandPalette } from "./CommandPalette";

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
});

afterEach(cleanup);

function LocationProbe({ onVisit }: { onVisit: (path: string) => void }) {
  const location = useLocation();
  useEffect(() => {
    onVisit(`${location.pathname}${location.search}${location.hash}`);
  }, [location, onVisit]);
  return null;
}

function openPalette(siteId = "abc") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData<SitesV2>(["sites-v2"], {
    ok: true,
    count: 1,
    ts: 0,
    sites: [{
      site_id: siteId,
      name: "Jump target",
      avatar_color: "#123456",
      state: "idle",
      auth_state: "ok",
      captcha_pending: false,
      downloaded_total: 0,
      active_workers: 0,
      last_event_ts: 0,
      last_event_age: "",
    }],
  });
  const onVisit = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/start"]}>
        <CommandPalette />
        <LocationProbe onVisit={onVisit} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(onVisit).toHaveBeenCalledExactlyOnceWith("/start");
  onVisit.mockClear();
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText("Jump target")).toBeInTheDocument();
  return onVisit;
}

describe("CommandPalette site jumps", () => {
  it.each([
    ["abc", "/sites/abc"],
    ["a/b?c#d% e", "/sites/a%2Fb%3Fc%23d%25%20e"],
    ["føø", "/sites/f%C3%B8%C3%B8"],
  ])("selecting site %s preserves its encoded id", (siteId, expectedPath) => {
    const onVisit = openPalette(siteId);
    fireEvent.click(screen.getByText("Jump target"));
    expect(onVisit, "site jump must navigate to the selected site detail").toHaveBeenCalledExactlyOnceWith(expectedPath);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    ["Sites", "/sites"],
    ["Queue", "/queue"],
    ["Capture", "/settings#capture"],
  ])("negative control: %s retains its destination", (label, expectedPath) => {
    const onVisit = openPalette();
    fireEvent.click(screen.getByText(label, { exact: true }));
    expect(onVisit).toHaveBeenCalledExactlyOnceWith(expectedPath);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    console.info(`SITEJUMP-CONTROL ${label} ${JSON.stringify(onVisit.mock.calls)}`);
  });
});
