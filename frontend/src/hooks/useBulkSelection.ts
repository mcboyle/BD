import { useCallback, useMemo, useState } from "react";

// Generic bulk-selection state. The contained set holds IDs as strings.
// Used by Sites (sid) and Queue (composite "sid|url" key — the consumer
// composes the key, the hook stays type-flat).
//
// Why string-keyed: TypeScript Set<T> with a union type tends to widen
// inconveniently when used across components. A string key with the
// composition done at the call site is simpler and matches how
// useQueueStream stores progress (Map<url, pct>).
export function useBulkSelection(allIds: readonly string[] = []) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  const has = useCallback((id: string) => selected.has(id), [selected]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const setOne = useCallback((id: string, on: boolean) => {
    setSelected((prev) => {
      if (on === prev.has(id)) return prev;
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setSelected((prev) => (prev.size === 0 ? prev : new Set()));
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(allIds));
  }, [allIds]);

  // Prune any selected IDs that aren't in the current allIds list.
  // Used after the data refetches and a previously-selected row is
  // gone — we don't want a stale selection to act on a deleted row
  // when the operator hits Pause.
  const pruneTo = useCallback((ids: readonly string[]) => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const allowed = new Set(ids);
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (allowed.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, []);

  const ids = useMemo(() => Array.from(selected), [selected]);

  return {
    has,
    toggle,
    setOne,
    clear,
    selectAll,
    pruneTo,
    ids,
    size: selected.size,
    isAllSelected: allIds.length > 0 && selected.size === allIds.length,
  };
}
