import { it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// scs-fix-r2 F5b (bughunt spa-cockpit-settings). "Watch interval" clamped to its
// min of 5 on every keystroke, so selecting the field and typing "12" showed
// (and kept) 52: the "1" became 5 before the "2" arrived. The field now keeps
// what is typed and clamps when it loses focus; -5 still ends up as 5.

const DIAG = "BD-SCS-F5B-CLAMP-ON-TYPE";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/global_config")) return Promise.resolve(jsonResponse({ watch_interval_sec: 37 }));
      if (url.includes("/api/csrf")) return Promise.resolve(jsonResponse({ csrf_token: "t" }));
      return new Promise<Response>(() => {});
    }),
  );
});

async function watchBox(): Promise<HTMLInputElement> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Filter settings"), { target: { value: "Watch interval" } });
  return (await screen.findByDisplayValue("37")) as HTMLInputElement;
}

async function typeInto(box: HTMLInputElement, text: string) {
  const user = userEvent.setup();
  await user.tripleClick(box);
  await user.keyboard(text);
}

it("typing 12 over the value keeps 12, before and after blur", async () => {
  const box = await watchBox();
  await typeInto(box, "12");
  expect(box.value, `${DIAG}: typed 12 shows ${box.value}`).toBe("12");
  fireEvent.blur(box);
  expect(box.value, `${DIAG}: typed 12 committed as ${box.value}`).toBe("12");
});

it("a value below the minimum still clamps to 5 once the field loses focus", async () => {
  const box = await watchBox();
  fireEvent.change(box, { target: { value: "-5" } });
  fireEvent.blur(box);
  expect(box.value).toBe("5");
});

it("control: typing a legal 60 shows 60", async () => {
  const box = await watchBox();
  await typeInto(box, "60");
  fireEvent.blur(box);
  expect(box.value).toBe("60");
});
