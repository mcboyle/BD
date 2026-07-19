import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

// Cut 6.4 — URL-encoded shareable view state. A single filter/sort/group key is
// mirrored into the query string so a view is bookmarkable and replayable.
// ZERO persistence: the URL is the only store — nothing is written to
// localStorage/sessionStorage. Setting the value back to its default removes the
// param so a clean view yields a clean URL.
export function useUrlState(
  key: string,
  defaultValue: string,
): [string, (next: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? defaultValue;

  const setValue = useCallback(
    (next: string) => {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next === defaultValue || next === "") p.delete(key);
          else p.set(key, next);
          return p;
        },
        { replace: true },
      );
    },
    [key, defaultValue, setParams],
  );

  return [value, setValue];
}
