VERDICT: PATCH
BASE: 6600f4500dd4294b0c9346dfd8d20e533d5b97db
TREE: 5a92239d7955f91375815faf24928cef7809a75c
SOURCE: row722-rebase-6add188b-cx-hi3 f63492910802cfc81fb626463b2f3f6828fca871

REBASE: Applied the source tree three-way to t15b. Resolved submit.py by
retaining both row775/772 rejection handling and row722 transitional/challenge
handling; the row772 HTML rejection probe runs only when row722's rendered
surface is unavailable. Regenerated source_window_hashes.json using
bd-regen-order --work. The row703 policy map uses the current t15b locations.

GREEN:
- Focused rebase suite: 272 passed in 221.10s (row722, row775, row772, source
  windows).
- row703 policy map: 12 passed.
- row722 anonymous-surface plus row772 rejection contracts: 19 passed.
- After re-freezing the intentional graph edges on the rebased t15b tree,
  tests/test_import_graph_no_new_edges.py::test_no_new_edges and
  tests/test_row1435_band_verdict_transfer.py::test_real_git_rebase_keeps_one_content_digest_when_all_dispositions_hold passed (pytest lastfailed cache empty).

PRECUT: initial final-tree run rc=3: 3,140 passed, 2 failed, 8 skipped,
1 xpassed. The two failures were the intended import edges absent from the
frozen baseline; this user-directed t15b rebase re-froze the baseline (4,522
edges) and passed only those two rerun failures. Fleet metric ratchets remain
outside this mechanical rebase: coupling_ratio 0.38 -> 0.383 and
defect_DP_total 1367 -> 1397.
