import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { Library } from "@/routes/Library";
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

// O1671 R8b F019: clicking a Library item title must open a preview/detail
// dialog (live 3.66.1754: nothing happened and the URL did not change).
const FIXTURES = {
  "/api/library/browse?limit=200": {
    ok: true,
    rows: [
      {
        id: 7,
        title: "Fixture movie",
        path: "/media/lib/fixture-movie.mp4",
        rating: 4,
        watched: true,
        tags: ["drama", "4k"],
      },
      { id: 8, title: "Other movie", path: "/media/lib/other.mp4" },
    ],
  },
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": {},
  "/api/scene_score/bottom?limit=20": {},
  "/api/live/status": { ok: true, available: false },
  "/api/live/recordings": { ok: true, recordings: [] },
};

beforeEach(() => {
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("F019 Library item preview", () => {
  it("opens a detail dialog for the clicked item title, read-only", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");

    await user.click(await screen.findByText("Fixture movie"));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Fixture movie" })).toBeInTheDocument();
    expect(within(dialog).getByText("/media/lib/fixture-movie.mp4")).toBeInTheDocument();
    expect(within(dialog).getByText("drama, 4k")).toBeInTheDocument();
    expect(within(dialog).queryByText("/media/lib/other.mp4")).toBeNull();
    // Opening a preview is a read: no write endpoint fires.
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(apiDeleteMock).not.toHaveBeenCalled();

    // DialogContent also renders an icon "Close"; use the footer text button.
    const closeBtn = within(dialog)
      .getAllByRole("button", { name: "Close" })
      .find((b) => b.textContent === "Close");
    await user.click(closeBtn as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("exposes the title as a keyboard-operable button", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");

    const title = await screen.findByRole("button", { name: "Other movie" });
    title.focus();
    await user.keyboard("{Enter}");

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("/media/lib/other.mp4")).toBeInTheDocument();
  });
});
