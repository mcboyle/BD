// T7 / row 188 -- THE (R) WRITE-ONLY RULE, EXERCISED.
//
// The scan this replaces asserted `'useState("")' in route`. Row 188 named the
// evasion and it is reproduced verbatim as mutant M1: seed the token field from
// the GET payload and TWO OTHER useState("") sites in the same file survive as
// decoys, so the substring is still there and the gate still passes while the
// operator's Telegram bot token is rendered into the DOM.
//
// THE POISONED-CACHE ORDERING IS THE WHOLE CATCHER. useState(initializer) runs
// on FIRST RENDER ONLY. installApiFixtures resolves asynchronously, so a seeded
// initializer would observe `undefined`, fall back to "", and the field would be
// empty EVEN ON THE MUTATED TREE -- the catcher would pass on a broken tree and
// this spec would be decorative. So every render below preloads the query cache
// BEFORE mounting, and the first test asserts the poisoned payload was actually
// consumed before any emptiness is asserted anywhere.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of Notifications.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import { freshQueryClient, installApiFixtures } from "@/test/wiredGateHarness";

// Values a correct SPA must never render. Distinctive enough that a substring
// search over the DOM is unambiguous.
const TG_SECRET = "GET-TG-SECRET-8Q";
const APPRISE_SECRET = "tgram://GET-APPRISE-SECRET-8Q/1";

// A DELIBERATELY LEAKY GET. The real backend masks these (and
// test_t7_backend_apprise_get_masks_urls still proves that in-process); this
// spec asks the complementary question -- if the payload DID carry the secret,
// would the SPA put it in an input? -- so the fixture must carry it.
const POISONED_APPRISE = {
  settings: {
    notify_apprise_urls_set: true,
    notify_apprise_urls_count: 2,
    notify_apprise_urls: APPRISE_SECRET,
  },
};
const POISONED_TG = {
  settings: { tg_bot_token_set: true, tg_bot_token: TG_SECRET },
};

const FIXTURES = {
  "/api/notify/apprise/settings": POISONED_APPRISE,
  "/api/tg/status": { available: true, running: false, allowlist_size: 0 },
  "/api/tg/settings": POISONED_TG,
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
  "POST /api/notify/apprise/settings": { settings: {} },
  "POST /api/tg/settings": { settings: {} },
};

// Query keys copied from src/hooks/useNotificationsData.ts.
function poisonedClient(): QueryClient {
  const qc = freshQueryClient();
  qc.setQueryData(["notify", "apprise", "settings"], POISONED_APPRISE);
  qc.setQueryData(["tg", "settings"], POISONED_TG);
  qc.setQueryData(["tg", "status"], {
    available: true,
    running: false,
    allowlist_size: 0,
  });
  qc.setQueryData(["alerts", "active", 24], { alerts: [] });
  return qc;
}

function renderPoisoned() {
  return render(
    <QueryClientProvider client={poisonedClient()}>
      <MemoryRouter initialEntries={["/notifications"]}>
        <Notifications />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function tokenInput(): HTMLElement {
  return screen.getByLabelText("Telegram bot token");
}

function appriseInput(): HTMLElement {
  return screen.getByPlaceholderText(/one apprise URL per line/);
}

async function driveConfirmed(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await user.click(screen.getByRole("button", { name }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

function lastBody(path: string): Record<string, unknown> {
  const call = [...apiPostMock.mock.calls].reverse().find((c) => c[0] === path);
  expect(call, `no POST to ${path} was observed`).toBeTruthy();
  return (call as unknown[])[1] as Record<string, unknown>;
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("T7 write-only secrets", () => {
  it("PRECONDITION: the poisoned GET payload is really consumed and rendered masked", () => {
    renderPoisoned();
    // Without this, every "the field is empty" assertion below could pass
    // because the payload never arrived at all.
    expect(screen.getByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();
    expect(screen.getByText(/token set/)).toBeInTheDocument();
  });

  it("never seeds either write-only input from the preloaded GET payload", () => {
    renderPoisoned();
    // toHaveValue reads the DOM PROPERTY. See the negative control below for why
    // an innerHTML sweep is not admissible as the catcher for this claim.
    expect(tokenInput()).toHaveValue("");
    expect(appriseInput()).toHaveValue("");
  });

  it("NEGATIVE CONTROL: the value assertion rejects a seeded field, and an HTML sweep does not", async () => {
    // PART 1. A component committing exactly mutant M1's violation -- reading
    // the token out of the same cached GET payload and seeding useState with it.
    // The catcher used above is thereby proved to have discriminating power,
    // without touching the real tree.
    function SeededField() {
      const q = useQuery<{ settings?: { tg_bot_token?: string } }>({
        queryKey: ["tg", "settings"],
        queryFn: () => Promise.resolve(POISONED_TG),
      });
      const [token] = useState(q.data?.settings?.tg_bot_token ?? "");
      return <input aria-label="Telegram bot token" readOnly value={token} />;
    }
    const seeded = render(
      <QueryClientProvider client={poisonedClient()}>
        <SeededField />
      </QueryClientProvider>,
    );
    expect(tokenInput()).toHaveValue(TG_SECRET);
    // MEASURED, NOT ASSUMED, AND IT CONTRADICTS THE OBVIOUS FOLKLORE. The
    // received markup here is
    //   <input aria-label="Telegram bot token" readonly="" value="GET-TG-...">
    // so this React/jsdom pair DOES mirror a controlled input's value into the
    // `value` ATTRIBUTE, on the first render and on later updates alike. The
    // planning note for this cut asserted the opposite ("a seeded field leaves
    // innerHTML clean") and it is wrong on this tree; pinned here so the claim
    // is re-measured rather than re-inherited.
    expect(document.body.innerHTML).toContain(TG_SECRET);
    seeded.unmount();

    // PART 2. WHY toHaveValue IS STILL THE CATCHER AND THE SWEEP IS NOT. The
    // sweep is wrong in the OTHER direction: it fires on any occurrence of the
    // text anywhere in the document, including page copy that names the secret
    // while the write-only input is correctly empty. That is a gate that cries
    // wolf on a correct implementation, which CLAUDE.md counts as a soundness
    // bug rather than a safe default.
    function CorrectButChatty() {
      const [token] = useState("");
      return (
        <>
          <input aria-label="Telegram bot token" readOnly value={token} />
          <p>{`Rejected token ${TG_SECRET}: not a valid bot token`}</p>
        </>
      );
    }
    render(<CorrectButChatty />);

    expect(tokenInput()).toHaveValue("");
    expect(document.body.innerHTML).toContain(TG_SECRET);
  });

  it("clears the apprise field only after the save POST carried it", async () => {
    const user = userEvent.setup();
    renderPoisoned();

    await user.type(appriseInput(), APPRISE_SECRET);
    // PRECONDITION: typing worked, so "the field is empty" cannot pass vacuously.
    expect(appriseInput()).toHaveValue(APPRISE_SECRET);

    await driveConfirmed(user, "Save apprise");
    await waitFor(() =>
      expect(lastBody("/api/notify/apprise/settings").notify_apprise_urls).toBe(
        APPRISE_SECRET,
      ),
    );
    await waitFor(() => expect(appriseInput()).toHaveValue(""));
  });

  it("clears the telegram token only after the save POST carried it", async () => {
    const user = userEvent.setup();
    renderPoisoned();

    await user.type(tokenInput(), TG_SECRET);
    expect(tokenInput()).toHaveValue(TG_SECRET);

    await driveConfirmed(user, "Save telegram");
    await waitFor(() =>
      expect(lastBody("/api/tg/settings").tg_bot_token).toBe(TG_SECRET),
    );
    await waitFor(() => expect(tokenInput()).toHaveValue(""));
  });

  it("OVER-SENSITIVITY CONTROL: a blank save omits both secrets and keeps the masked metadata", async () => {
    // THE CORRECT-INPUT CASE THAT MUST KEEP PASSING. "Write-only" does not mean
    // "always write": a blank field means KEEP THE EXISTING SECRET, so a save
    // with nothing typed must omit the key entirely. An over-correction that
    // sent tg_bot_token unconditionally would wipe the operator's stored token
    // on any unrelated settings save -- that is tracked mutant M6, whose named
    // control is this spec.
    const user = userEvent.setup();
    renderPoisoned();
    expect(tokenInput()).toHaveValue("");
    expect(appriseInput()).toHaveValue("");

    await driveConfirmed(user, "Save apprise");
    await driveConfirmed(user, "Save telegram");

    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(2));
    const apprise = lastBody("/api/notify/apprise/settings");
    const tg = lastBody("/api/tg/settings");

    expect(apprise).not.toHaveProperty("notify_apprise_urls");
    expect(apprise).toHaveProperty("notify_apprise_enabled");
    expect(tg).not.toHaveProperty("tg_bot_token");
    expect(tg).toHaveProperty("tg_bot_enabled");

    // and the page still shows what it is allowed to show
    expect(screen.getByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();
    expect(screen.getByText(/token set/)).toBeInTheDocument();
  });
});
