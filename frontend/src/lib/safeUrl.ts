// F-FE06-01 — shared scheme-allowlist gate for URLs placed in an <a href>.
// React does NOT sanitize href, so a `javascript:` / `data:` / `vbscript:` URL
// arriving as data (e.g. a review-queue item's URL) would execute on click.
// Only http(s) is safe to link; anything else must be shown as inert text.
export function isHttpUrl(url: unknown): boolean {
  if (typeof url !== "string") return false;
  // Trim surrounding whitespace, then require the string to *start* with an
  // http(s):// scheme. Leading whitespace/control chars cannot smuggle a
  // dangerous scheme past the allowlist because the result still won't begin
  // with http(s)://.
  return /^https?:\/\//i.test(url.trim());
}
