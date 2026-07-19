import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// Cut 1 substrate — useDirtyForm: one draft-vs-saved engine. Drives sticky
// save/discard, changed-markers, and disabled-until-changed across Settings AND
// every form page. Cut 1 ships the scaffold only; no page adopts it yet.
//
//   values       — the current draft
//   setValue     — update one field in the draft
//   changedKeys  — keys whose draft value differs from the saved baseline
//   isDirty      — changedKeys.length > 0
//   reset        — discard the draft back to the saved baseline
//   markSaved    — adopt the current draft as the new saved baseline
//
// Equality is shallow per key via Object.is, so setting a field back to its
// saved value clears it from changedKeys (no false-dirty).

export interface DirtyForm<T extends Record<string, unknown>> {
  values: T;
  setValue: <K extends keyof T>(key: K, value: T[K]) => void;
  changedKeys: (keyof T)[];
  isDirty: boolean;
  reset: () => void;
  markSaved: () => void;
}

export function useDirtyForm<T extends Record<string, unknown>>(
  initial: T,
): DirtyForm<T> {
  const [saved, setSaved] = useState<T>({ ...initial });
  const [values, setValues] = useState<T>({ ...initial });

  // Mirror of `values` for markSaved, which must read the latest draft without
  // nesting setState updaters.
  const valuesRef = useRef(values);
  useEffect(() => {
    valuesRef.current = values;
  }, [values]);

  const setValue = useCallback(
    <K extends keyof T>(key: K, value: T[K]) => {
      setValues((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const reset = useCallback(() => {
    setValues({ ...saved });
  }, [saved]);

  const markSaved = useCallback(() => {
    setSaved({ ...valuesRef.current });
  }, []);

  const changedKeys = useMemo(() => {
    const keys = new Set<keyof T>([
      ...(Object.keys(values) as (keyof T)[]),
      ...(Object.keys(saved) as (keyof T)[]),
    ]);
    const out: (keyof T)[] = [];
    keys.forEach((k) => {
      if (!Object.is(values[k], saved[k])) out.push(k);
    });
    return out;
  }, [values, saved]);

  return {
    values,
    setValue,
    changedKeys,
    isDirty: changedKeys.length > 0,
    reset,
    markSaved,
  };
}
