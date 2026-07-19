import { useEffect, useState } from "react";

// Debounce any value. Returns the input value once it's been stable
// for `delay_ms`. Used by the Add Site wizard to debounce calls to
// /api/sites/validate so we don't hammer the endpoint on every
// keystroke.

export function useDebouncedValue<T>(value: T, delay_ms: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay_ms);
    return () => clearTimeout(t);
  }, [value, delay_ms]);
  return debounced;
}
