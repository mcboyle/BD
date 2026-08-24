import type { ReactElement, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Mock } from "vitest";
import App from "@/App";

type ApiFixtures = Record<string, unknown>;

export function installApiFixtures(
  apiGet: Mock,
  apiPost: Mock,
  fixtures: ApiFixtures = {},
) {
  apiGet.mockImplementation((path: string) =>
    Promise.resolve(fixtures[path] ?? {}),
  );
  apiPost.mockImplementation((path: string) =>
    Promise.resolve(fixtures[`POST ${path}`] ?? { ok: true }),
  );
}

export function freshQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function WiredProviders({
  children,
  path = "/",
}: {
  children: ReactNode;
  path?: string;
}) {
  const queryClient = freshQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

export function renderWired(node: ReactElement, path = "/") {
  // NOTE ON WHAT THIS DOES AND DOES NOT PROVE. `path` seeds MemoryRouter's
  // initialEntries; it does NOT gate rendering, because `node` is placed
  // directly inside the router with no <Routes>/<Route> of its own. That is the
  // right tool for "does this component call its endpoints", and the WRONG tool
  // for "is this component reachable at that URL" -- an evasion that unhooks or
  // repaths the <Route> binding in App.tsx passes this untouched. Use
  // renderAppAt() for the binding. An adversarial review of the first draft of
  // these specs caught tests named "through their real routes" that used this.
  return render(<WiredProviders path={path}>{node}</WiredProviders>);
}

export function renderAppAt(path: string) {
  // THE REAL ROUTE TABLE, not a hand-placed component. App.tsx owns 43 <Route>
  // bindings and this renders whichever one `path` actually resolves to, so a
  // binding that is removed, repathed, or made conditional shows up here as an
  // absent screen rather than as a still-passing component test.
  const queryClient = freshQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function renderWiredHook<Result>(callback: () => Result) {
  return renderHook(callback, {
    wrapper: ({ children }) => <WiredProviders>{children}</WiredProviders>,
  });
}

export function calledPaths(mock: Mock): string[] {
  return mock.mock.calls.map(([path]) => String(path));
}
