# shadcn/ui primitives

Pre-staged in U1 per the operator's lock-in answer. The 10 components
chosen are the ones U3–U7 are known to need; copying them in now
prevents `npx shadcn add` friction across the next 6 sessions.

## Components present

| Component       | First user (planned unit) | Mockup surface                              |
|-----------------|---------------------------|---------------------------------------------|
| `button`        | U2                        | Pause All, Resolve, + Add Site, ...         |
| `card`          | U3                        | Every dashboard widget, every site row      |
| `input`         | U6                        | All forms (cookie_file et al. — Tier 1 #8)  |
| `badge`         | U2                        | Status pills (NEEDS ATTENTION, FAILED, ...) |
| `skeleton`      | U3                        | Loading states (Tier 1 #6)                  |
| `dialog`        | U6                        | "Click error → see full stack" modal        |
| `tabs`          | U4                        | Sites tab filter, Activity tab time range   |
| `dropdown-menu` | U4                        | (…) overflow menus on rows                  |
| `command`       | U5                        | ⌘K command palette (Tier 1 #2)              |

## Why no Toast component file

Tier 1 #3 (toast notifications) ships through `sonner`, not via a
shadcn wrapper. `sonner` exports `<Toaster />` (mounted in `main.tsx`)
and a `toast` function — call it directly:

```ts
import { toast } from "sonner";
toast.success("Site updated");
toast.error("HTTP 503 from /api/sites");
```

Wrapping it in `components/ui/toast.tsx` would just re-export; skipped.

## Customisations from stock shadcn

Every file is rebound to the `UI_TOKENS.md` palette. Stock shadcn uses
HSL CSS vars with neutral greys; we substitute the project tokens
(`bg`, `surface`, `ink`, `primary`, `green`, `amber`, `red`,
`hairline`) and the `0.5px` hairline border (mockup signature).

Where shadcn defaults conflict with `UI_TOKENS.md`, **tokens win** —
matches the rule in the design narrative.

## Adding a component later

If U3+ needs one of the components not pre-staged (`select`,
`tooltip`, `popover`, `sheet`, etc.), add it by hand following the
shadcn pattern: copy from <https://ui.shadcn.com/docs/components/X>,
swap colour tokens for the project's, drop the file in this folder.
Don't run `npx shadcn init` — it would clobber the customisations.
