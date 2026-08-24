import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, fetchMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
  });
  return {
    apiGetMock: vi.fn(),
    apiPostMock: vi.fn(),
    fetchMock: vi.fn(),
    toastMock: toast,
  };
});

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMock }));

import { ApprovalGate } from "@/components/ApprovalGate";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
} from "@/test/wiredGateHarness";

const SID = "fixture.test";
const AUTO_KEY = "auto-key-fixture";
const REVEAL_URL = "https://fixture.test/reveal";
const PENDING = [
  {
    surface: "auto_submit",
    key: AUTO_KEY,
    kind: "cf-turnstile",
    why: "challenge marker",
    at: "2026-08-24T00:00:00Z",
  },
  {
    surface: "post_reveal",
    key: REVEAL_URL,
    kind: "captcha",
    why: "post reveal challenge",
    at: "2026-08-24T00:00:00Z",
  },
];

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  fetchMock.mockReset();
  toastMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    json: vi.fn().mockResolvedValue({ ok: true }),
  });
  vi.stubGlobal("fetch", fetchMock);
  installApiFixtures(apiGetMock, apiPostMock, {
    [`/api/sites/${SID}/pending_approvals`]: {
      ok: true,
      pending: PENDING,
      count: PENDING.length,
    },
    [`POST /api/sites/${SID}/auto_submit_decision`]: { ok: true },
    [`POST /api/sites/${SID}/post_reveal_decision`]: { ok: true },
  });
});

function confirmLatestToast() {
  const options = toastMock.mock.calls.at(-1)?.[1];
  expect(options?.action?.onClick).toBeTypeOf("function");
  return act(async () => {
    options.action.onClick();
  });
}

describe("T11 approval caller runtime wiring", () => {
  it("loads pending surfaces and performs no decision during render", async () => {
    renderWired(<ApprovalGate siteId={SID} />, `/sites/${SID}`);

    expect(await screen.findByText("Approval needed (2)")).toBeInTheDocument();
    expect(calledPaths(apiGetMock)).toContain(
      `/api/sites/${SID}/pending_approvals`,
    );
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("confirms an auto-submit approval through apiPost with the exact body", async () => {
    const user = userEvent.setup();
    renderWired(<ApprovalGate siteId={SID} />, `/sites/${SID}`);
    await screen.findByText("Approval needed (2)");

    await user.click(
      screen.getByRole("button", {
        name: "Approve Login form / page blocker for this site",
      }),
    );
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();

    await confirmLatestToast();
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        `/api/sites/${SID}/auto_submit_decision`,
        { key: AUTO_KEY, decision: "approve" },
      ),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("confirms a post-reveal decline through apiPost with the exact body", async () => {
    const user = userEvent.setup();
    renderWired(<ApprovalGate siteId={SID} />, `/sites/${SID}`);
    await screen.findByText("Approval needed (2)");

    await user.click(
      screen.getByRole("button", {
        name: "Decline Two-step POST reveal for this site",
      }),
    );
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();

    await confirmLatestToast();
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        `/api/sites/${SID}/post_reveal_decision`,
        { action_url: REVEAL_URL, decision: "decline" },
      ),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
