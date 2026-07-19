import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PathValidator } from "./PathValidator";

// Cut 4 — inline read-only path diagnosis over GET /api/storage/validate.

function mockValidate(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response),
    ),
  );
}
function mount(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PathValidator path={path} debounceMs={0} />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("PathValidator", () => {
  it("reports a writable directory with free space", async () => {
    mockValidate({
      ok: true, path: "/tmp/dl", exists: true, is_dir: true, writable: true,
      free_bytes: 5 * 1024 * 1024 * 1024, problems: [], suggested_fix: null,
    });
    mount("/tmp/dl");
    expect(await screen.findByText(/writable/i)).toBeTruthy();
  });

  it("surfaces problems and the suggested fix for a missing path", async () => {
    mockValidate({
      ok: true, path: "/no/such/dir", exists: false, is_dir: false, writable: false,
      free_bytes: null, problems: ["path does not exist"],
      suggested_fix: "create the directory '/no/such/dir'",
    });
    mount("/no/such/dir");
    expect(await screen.findByText(/path does not exist/i)).toBeTruthy();
    expect(screen.getByText(/create the directory/i)).toBeTruthy();
  });
});
