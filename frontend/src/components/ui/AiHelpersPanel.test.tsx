import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AiHelpersPanel } from "@/components/ui/AiHelpersPanel";
import { AiReanalyzeSection } from "@/components/ui/AiReanalyzeSection";

// v3.66.752 — the dark ai cluster gets a GUI (2 pure helpers + reanalyze).
//
// THE LOAD-BEARING TESTS:
//  1. AI-off gating: both helpers answer HTTP 200 {ok:false} when AI is
//     disabled — the controls read /api/ai/status (already wired) and
//     DISABLE rather than firing into a disabled backend.
//  2. normalize's `via` provenance: ok:true + resolution:null is a REAL
//     "no-match" answer and must be said — not rendered as blank success.
//  3. reanalyze's had_screenshot=false means TEXT-ONLY analysis — a weaker
//     answer the panel says out loud.
//  4. Empty inputs never fire (classify on "" wastes a model call;
//     normalize on "" is the meaningless via:"empty" shape).

function withProviders(node: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function json(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

function installFetch(opts: {
  aiEnabled?: boolean;
  normalizeBody?: unknown;
  classifyBody?: unknown;
  reanalyzeBody?: unknown;
  calls?: { url: string; init?: RequestInit }[];
}) {
  const calls = opts.calls ?? [];
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (method !== "GET") calls.push({ url, init });
      if (url.includes("/api/ai/status"))
        return json({ enabled: opts.aiEnabled ?? true });
      if (url.includes("/api/ai/classify"))
        return json(
          opts.classifyBody ?? {
            ok: true,
            role: "pass_field",
            confidence: 92,
            reasoning: "password input by name and type",
            provider: "ollama",
          },
        );
      if (url.includes("/api/ai/normalize_resolution"))
        return json(
          opts.normalizeBody ?? {
            ok: true,
            resolution: "2160p",
            label: "4K",
            width: 3840,
            height: 2160,
            confidence: 95,
            via: "regex",
          },
        );
      if (url.includes("/ai_reanalyze"))
        return json(
          opts.reanalyzeBody ?? {
            ok: true,
            suggestions: [
              {
                selector: "a.download-hd",
                role: "trigger_selectors",
                confidence: 80,
                reasoning: "prominent download anchor",
              },
            ],
            had_screenshot: false,
            tried_count: 2,
            event_count: 15,
          },
        );
      return json({ ok: true });
    });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("AiHelpersPanel wires the two pure ai helpers", () => {
  it("disables both helpers with the reason when AI is off, and fires nothing", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch({ aiEnabled: false, calls });
    const user = userEvent.setup();
    withProviders(<AiHelpersPanel />);

    expect(
      await screen.findByText(/AI assist is disabled — enable it/),
    ).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Classify an element"),
      "<input type=password>",
    );
    const btn = screen.getByRole("button", { name: "Classify" });
    expect(btn).toBeDisabled();
    // no POST left the panel — it never fires into a disabled backend
    expect(calls).toHaveLength(0);
  });

  it("keeps buttons disabled on empty inputs (no wasted model calls)", async () => {
    installFetch({});
    withProviders(<AiHelpersPanel />);

    await screen.findByText("AI on");
    expect(screen.getByRole("button", { name: "Classify" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Normalize" })).toBeDisabled();
  });

  it("classifies and shows role + confidence + reasoning", async () => {
    installFetch({});
    const user = userEvent.setup();
    withProviders(<AiHelpersPanel />);

    await screen.findByText("AI on");
    await user.type(
      screen.getByLabelText("Classify an element"),
      '<input type="password">',
    );
    await user.click(screen.getByRole("button", { name: "Classify" }));

    const out = await screen.findByTestId("classify-result");
    expect(out).toHaveTextContent("pass_field");
    expect(out).toHaveTextContent("92%");
    expect(out).toHaveTextContent("password input by name and type");
  });

  // ---- THE PROVENANCE RULE THIS PANEL EXISTS TO GET RIGHT ---------------
  it("says 'no match' out loud when ok:true carries resolution:null", async () => {
    installFetch({
      normalizeBody: {
        ok: true,
        resolution: null,
        label: null,
        width: 0,
        height: 0,
        confidence: 0,
        via: "no-match",
      },
    });
    const user = userEvent.setup();
    withProviders(<AiHelpersPanel />);

    await screen.findByText("AI on");
    await user.type(
      screen.getByLabelText("Read a resolution from a filename"),
      "mystery_clip.mp4",
    );
    await user.click(screen.getByRole("button", { name: "Normalize" }));

    const out = await screen.findByTestId("normalize-result");
    // a null result is a real answer, not blank success
    expect(out).toHaveTextContent(/No resolution recognized/);
    expect(out).toHaveTextContent("no-match");
  });

  it("renders a regex hit with its provenance", async () => {
    installFetch({});
    const user = userEvent.setup();
    withProviders(<AiHelpersPanel />);

    await screen.findByText("AI on");
    await user.type(
      screen.getByLabelText("Read a resolution from a filename"),
      "clip_4k.mp4",
    );
    await user.click(screen.getByRole("button", { name: "Normalize" }));

    const out = await screen.findByTestId("normalize-result");
    expect(out).toHaveTextContent("2160p");
    expect(out).toHaveTextContent("via regex");
  });
});

describe("AiReanalyzeSection wires the site-scoped reanalyze", () => {
  it("POSTs {url} to /api/sites/<sid>/ai_reanalyze for the row's job", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch({ calls });
    const user = userEvent.setup();
    withProviders(
      <AiReanalyzeSection sid="alpha" url="https://x.example/item/1" />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Ask AI about this failure" }),
    );
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const post = calls[0];
    expect(post.url).toContain("/api/sites/alpha/ai_reanalyze");
    expect(JSON.parse(post.init!.body as string)).toMatchObject({
      url: "https://x.example/item/1",
    });
  });

  // ---- text-only analysis is a weaker answer and must be said -----------
  it("says the analysis was TEXT-ONLY when had_screenshot is false", async () => {
    installFetch({});
    const user = userEvent.setup();
    withProviders(
      <AiReanalyzeSection sid="alpha" url="https://x.example/item/1" />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Ask AI about this failure" }),
    );
    expect(await screen.findByText(/Text-only analysis/)).toBeInTheDocument();
    expect(screen.getByText(/a.download-hd/)).toBeInTheDocument();
    expect(screen.getByText(/Proposals only/)).toBeInTheDocument();
  });

  it("disables the ask button when AI is off", async () => {
    installFetch({ aiEnabled: false });
    withProviders(
      <AiReanalyzeSection sid="alpha" url="https://x.example/item/1" />,
    );

    expect(
      await screen.findByRole("button", { name: "Ask AI about this failure" }),
    ).toBeDisabled();
  });

  it("renders the endpoint's refusal (url not in queue) as an error", async () => {
    installFetch({
      reanalyzeBody: { ok: false, error: "url not in queue" },
    });
    const user = userEvent.setup();
    withProviders(
      <AiReanalyzeSection sid="alpha" url="https://x.example/item/1" />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Ask AI about this failure" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "url not in queue",
    );
  });
});
