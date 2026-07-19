"""bulk_downloader.site_templates -- decomposed TEMPLATES data package.

templates.py is a thin re-export shim over this package (ADD-only; never named
`templates/` -- Flask template_folder + preflight isfile collision, H-08).

TEMPLATES is re-assembled by concatenating the per-family ITEMS slices in their
ORIGINAL index order -- the concatenation order is the binding contract."""

from ._data_players import ITEMS as __data_players
from ._data_cms import ITEMS as __data_cms
from ._data_studios_a import ITEMS as __data_studios_a
from ._data_heuristics import ITEMS as __data_heuristics
from ._data_studios_b import ITEMS as __data_studios_b
from ._data_tubes import ITEMS as __data_tubes
from ._data_mainstream import ITEMS as __data_mainstream

TEMPLATES = __data_players + __data_cms + __data_studios_a + __data_heuristics + __data_studios_b + __data_tubes + __data_mainstream

from .accessors import get, list_templates, suggest_for_url  # noqa: E402

__all__ = ["TEMPLATES", "get", "list_templates", "suggest_for_url"]
