import * as React from "react";

import { Input } from "@/components/ui/input";

// Cut 7 (7.1) — ModelSelect: an editable combobox for the AI model fields.
//
// Native <input list> + <datalist>: zero deps, accessible, and — critically —
// it SUGGESTS the detected models while still accepting free text. Model tags
// can be long (`hf.co/<org>/<repo>:<quant>`) and detection fails open
// (INV-003), so the field must never trap the operator into a dropdown. When
// `options` is empty it is just a plain text input; when detection is in
// flight, a small hint shows. It never blocks the surrounding save.

export interface ModelSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  id?: string;
  disabled?: boolean;
  loading?: boolean;
  placeholder?: string;
}

let _seq = 0;

export function ModelSelect({
  value,
  onChange,
  options,
  id,
  disabled,
  loading,
  placeholder,
}: ModelSelectProps) {
  // Stable, unique datalist id per instance (id collisions would merge lists).
  const listId = React.useMemo(() => `model-options-${id ?? ++_seq}`, [id]);
  return (
    <div>
      <Input
        id={id}
        type="text"
        role="combobox"
        list={listId}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
      <datalist id={listId}>
        {options.map((opt) => (
          <option key={opt} value={opt} />
        ))}
      </datalist>
      {loading ? (
        <p className="mt-1 text-[11px] text-ink-3">Detecting available models…</p>
      ) : null}
    </div>
  );
}
