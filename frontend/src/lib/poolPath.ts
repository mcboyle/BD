// Compose a per-site action suffix that may require a trailing index segment.
//
// A site-action descriptor's `suffix` is already the FULL action path
// (e.g. "account_pool/reset"). When the action takes an index we append ONLY the
// index segment:  account_pool/reset/<idx>.
//
// v3.66.336 bug it fixes: SiteActions previously appended the action verb a SECOND
// time for indexed actions — `${suffix}/reset/${idx}` — producing the doubled
// segment account_pool/reset/reset/<idx>, which has no matching Flask route and
// 404s. The suffix already ends in the verb, so the index alone is appended here.
export function actionSuffixWithIdx(
  suffix: string,
  needsIdx: boolean,
  idx: string,
): string {
  return needsIdx ? `${suffix}/${encodeURIComponent(idx)}` : suffix;
}
