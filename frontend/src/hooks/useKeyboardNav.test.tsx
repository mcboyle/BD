import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useKeyboardNav } from "./useKeyboardNav";

// Cut 2 — useKeyboardNav: a global power-user keyboard layer.
//   g then <letter>  -> jump to a route (g h Home, g s Sites, g q Queue,
//                       g a Activity, g t seTtings)
//   /                -> focus the page filter (calls onFilter)
//   ?                -> open the shortcuts sheet (calls onShowHelp)
// Safety invariants (mirror useKeyboardShortcut): INERT while a text input is
// focused and INERT while `dialogOpen` is true, so it never hijacks typing or
// fires under a modal. The g-prefix primes a short window; a non-mapped second
// key cancels it. j/k row nav is an OPT-IN registration (onRowNav), not wired
// here.

function press(key: string, target?: EventTarget) {
  act(() => {
    const e = new KeyboardEvent("keydown", { key, bubbles: true });
    if (target) Object.defineProperty(e, "target", { value: target });
    window.dispatchEvent(e);
  });
}

let navigate: ReturnType<typeof vi.fn>;
let onFilter: ReturnType<typeof vi.fn>;
let onShowHelp: ReturnType<typeof vi.fn>;

function mount(opts: { dialogOpen?: boolean } = {}) {
  navigate = vi.fn();
  onFilter = vi.fn();
  onShowHelp = vi.fn();
  return renderHook(() =>
    useKeyboardNav({
      navigate,
      onFilter,
      onShowHelp,
      dialogOpen: opts.dialogOpen ?? false,
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useKeyboardNav (Cut 2)", () => {
  it("g then s navigates to /sites", () => {
    mount();
    press("g");
    press("s");
    expect(navigate).toHaveBeenCalledWith("/sites");
  });

  it("g then h navigates home", () => {
    mount();
    press("g");
    press("h");
    expect(navigate).toHaveBeenCalledWith("/");
  });

  it("g then q navigates to /queue", () => {
    mount();
    press("g");
    press("q");
    expect(navigate).toHaveBeenCalledWith("/queue");
  });

  it("a non-mapped key after g cancels the sequence (no nav)", () => {
    mount();
    press("g");
    press("z");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("'/' calls onFilter", () => {
    mount();
    press("/");
    expect(onFilter).toHaveBeenCalled();
  });

  it("'?' calls onShowHelp", () => {
    mount();
    press("?");
    expect(onShowHelp).toHaveBeenCalled();
  });

  it("is inert while a text input is focused", () => {
    mount();
    const input = document.createElement("input");
    press("g", input);
    press("s", input);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("is inert while a dialog is open", () => {
    mount({ dialogOpen: true });
    press("g");
    press("s");
    expect(navigate).not.toHaveBeenCalled();
    press("?");
    expect(onShowHelp).not.toHaveBeenCalled();
  });
});
