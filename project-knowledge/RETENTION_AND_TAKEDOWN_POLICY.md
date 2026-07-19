<!-- verified-against: v3.66.593 -->
# RETENTION & TAKEDOWN POLICY — BulkDownloader (@v3.66.593)

<!-- Records the operator's Bucket-C retention decisions. This is a PERSONAL, LOCAL-ONLY archive; the -->
<!-- Bucket-A no-redistribution + local-only lines hold, which is what makes "keep forever" defensible. -->
<!-- §3 is the one non-negotiable floor beneath the keep-forever default; everything else is operator preference. -->

## The decisions (operator, @593)

| Question | Choice |
|---|---|
| Content removed at source (creator pulls it / taken down) | **Keep the local copy — never auto-delete** |
| Default retention window | **Keep forever** |
| One-action creator purge tool | **Not built — manual delete is sufficient** |

## 1. Default policy

BD is a **permanent personal archive**. Archived content is retained indefinitely and is **not**
auto-deleted for any routine reason, including the content being removed, delisted, or made private
at its source. There is no time-based expiry and no automated mirroring of source-side removals.
Deletion is **operator-initiated and manual**.

This is defensible precisely because of the standing Bucket-A guarantees, which are unchanged:
- **Local-only.** Captures never leave the host; nothing is redistributed.
- **No third-party exposure.** The archive is single-operator on `stash`; there is no sharing surface.
- Retention is therefore a decision about *the operator's own local data*, not about exposing anyone.

## 2. Manual deletion must be COMPLETE (the requirement that makes "manual is sufficient" true)

"Manual delete is fine" only holds if a manual delete is *thorough*. When the operator deletes an
item, BD must purge **every** copy/derivative in one action, not just the primary file:
- the media file(s) on disk,
- the `downloader_history` / library DB row(s),
- any thumbnails, montages, montage frames, or preview artifacts,
- any cached capture (`.wacz`) / manifest bodies / recognizer artifacts tied to it,
- any queue rows referencing it.

**Recommended (small) build:** a `delete_archived(item)` that does this complete purge + a
confirmation that nothing dangling remains. Without it, "manual delete" risks leaving derived copies
behind — which would defeat the point on the one occasion it actually matters (§3). This is a
low-risk feature, not a policy change, and it is the piece that makes the operator's chosen
manual-only stance genuinely adequate.

## 3. The floor beneath "keep forever" (non-negotiable — not a preference)

"Keep forever / never auto-delete" is a valid default **for lawfully-archivable content the operator
legitimately has access to** — which is the charter's whole frame. It is **not** absolute. There is
one category where retention is not a preference question at all:

**Content that is unlawful to possess or was published non-consensually must be deleted promptly and
completely, regardless of the keep-forever default.** This includes anything involving a minor, any
non-consensual intimate imagery, and anything subject to a legal removal obligation (e.g. a valid
court order). For this category the default does not apply; prompt, complete manual deletion (§2) is
required, not optional.

This is stated not to second-guess the operator's archive but because a retention policy that omitted
it would be incomplete. It also aligns with the charter's own frame — the tool is for archiving
content the operator can *legitimately* access; content that is unlawful to hold was never in that
set. Note that "removed at source" occasionally carries this signal (a takedown for consent/legal
reasons, not creator whim), so a source-side removal is worth a second look even though BD won't act
on it automatically.

## 4. Optional posture the operator declined (recorded, revisitable)
- **Flag + notify on source removal** — was offered; operator chose "keep, no notification." Revisit
  only if the operator later wants awareness of source-side takedowns (it would surface the §3 signal
  without changing the keep default).
- **Dedicated one-action creator purge** — declined; the complete manual delete (§2) covers the same
  ground item-by-item. Revisit if bulk removal for a specific creator ever becomes needed.

## 5. Unchanged
Bucket-A trio (no circumvention, no redistribution, credential floor), new-host approval, and
politeness/rate-limiting are orthogonal to retention and remain in force.
