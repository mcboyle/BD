// dev-paired-session-scope: on a paired device /api/dev/enabled says yes but the
// dev guard answers /api/dev/discover with 403 "dev tools need the master token on
// a paired device". The Dev console shows that reason instead of an empty runner.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  apiGet: apiGetMock,
  apiPost: apiPostMock,
}));

import { DevToolsSection } from "@/components/sections/DevToolsSection";
import { ApiError } from "@/lib/api-client";
import { renderWired } from "@/test/wiredGateHarness";

const PAIRED_REFUSAL = "dev tools need the master token on a paired device";

function install(discover: () => Promise<unknown>) {
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/api/dev/enabled") return Promise.resolve({ enabled: true });
    if (path === "/api/dev/discover") return discover();
    return Promise.resolve({});
  });
  apiPostMock.mockResolvedValue({ ok: true });
}

describe("DevToolsSection on a paired device", () => {
  beforeEach(() => {
    cleanup();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
  });

  it("shows the dev guard's paired-device reason instead of the runner", async () => {
    install(() =>
      Promise.reject(
        new ApiError("GET /api/dev/discover → 403", 403, {
          error: PAIRED_REFUSAL,
        }),
      ),
    );
    renderWired(<DevToolsSection />, "/settings/advanced");

    expect(await screen.findByRole("alert")).toHaveTextContent(PAIRED_REFUSAL);
    expect(screen.queryByLabelText("dev run target")).not.toBeInTheDocument();
  });

  it("control: a master-token session still gets the runner", async () => {
    install(() => Promise.resolve({ files: ["tests/test_fixture.py"] }));
    renderWired(<DevToolsSection />, "/settings/advanced");

    expect(await screen.findByText(/1 files discovered/)).toBeInTheDocument();
    expect(screen.getByLabelText("dev run target")).toBeInTheDocument();
    expect(screen.queryByText(PAIRED_REFUSAL)).not.toBeInTheDocument();
  });
});
