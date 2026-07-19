"""bulk_downloader.learn_impl -- decomposed learn package (recorder/selector/memory).

learn.py is a thin ADD-only re-export shim over this package (R1 shim-over-rm).
Full surface (14 public + 10 private fns + 12 consts incl RECORDER_JS/TEACH_OVERLAY_JS)
re-exported here and on the shim; other modules do `from .learn import RECORDER_JS`,
`from .learn import merge_learned`, and `import learn as _learn`."""

from ._assets import (
    RECORDER_JS,
    TEACH_OVERLAY_JS,
)
from .selectors import (
    _CSS_SAFE_RE,
    _CSS_IN_JS_RE,
    _STABLE_CLASS_KW,
    _SUBMIT_TEXT_KW,
    _DL_URL_ATTRS,
    _DL_URL_EXT_RE,
    _css_escape_ident,
    _css_escape_attr_value,
    _looks_hashed,
    synthesize_selectors,
    _is_submit_shaped,
    _which_url_attr,
    _synthesize_download_row_selector,
)
from .memory import (
    _DD_MAX_PROVIDERS,
    _DD_MAX_TOP_TYPES,
    _DD_MAX_PENDING,
    _dd_init_block,
    _dd_now_iso,
    _dd_prune_dict,
    record_deep_detect_outcome,
    deep_detect_site_memory,
    record_post_reveal_decision,
    record_auto_submit_decision,
    _pending_why,
    record_pending_approvals,
    pending_approvals,
    make_provider_cache_writer,
)
from .classify import (
    _NON_TEXT_INPUT_TYPES,
    install_recorder,
    install_teach_overlay,
    harvest_recordings,
    classify_login,
    classify_download,
    merge_learned,
)

__all__ = [
    "install_recorder",
    "install_teach_overlay",
    "harvest_recordings",
    "synthesize_selectors",
    "classify_login",
    "classify_download",
    "merge_learned",
    "record_deep_detect_outcome",
    "deep_detect_site_memory",
    "record_post_reveal_decision",
    "record_auto_submit_decision",
    "record_pending_approvals",
    "pending_approvals",
    "make_provider_cache_writer",
    "RECORDER_JS",
    "TEACH_OVERLAY_JS",
]
