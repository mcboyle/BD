"""bulk_downloader.templates -- thin re-export shim over site_templates/.

The 91-element TEMPLATES catalog + the 3 accessors were decomposed into the
site_templates/ package @v3.66.448 (DECOMP-LEAF cut 1). This module stays a FILE
(not a package) so Flask's template_folder="templates" never re-collides with a
bulk_downloader/templates/ Jinja dir and test_preflight_templates_module_present
(isfile assert) stays green. ADD-only: nothing is deleted on deploy.

Public surface preserved for `from . import templates as _tpls` consumers:
TEMPLATES, get, list_templates, suggest_for_url."""

from .site_templates import (  # noqa: F401
    TEMPLATES,
    get,
    list_templates,
    suggest_for_url,
)

__all__ = ["TEMPLATES", "get", "list_templates", "suggest_for_url"]
