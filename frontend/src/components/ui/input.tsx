import * as React from "react";

import { cn } from "@/lib/utils";

// Input is load-bearing for the Tier 1 #8 form-validation work that
// motivates the whole project (cookie_file bug class). The styling
// here is the default state; error states get a ring-red overlay
// applied by the U6 form components, not here.
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "hairline flex h-10 w-full rounded-md bg-surface px-3 py-2 text-sm",
          "placeholder:text-ink-3",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "file:border-0 file:bg-transparent file:text-sm file:font-medium",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

export { Input };
