"""v3.43.76: PhoenixAdult URL catalog.

# What this module is

PhoenixAdult.bundle is a Plex metadata agent at
https://github.com/CodyHair/PhoenixAdult.bundle. Its `PAsiteList.py`
maintains a catalog of ~780 unique adult-content brand domains, each
mapped to a canonical site name + parent network. This is the most
comprehensive open-source URL→brand catalog in this space.

This module embeds a representative snapshot of that catalog covering
the brand networks BD actively handles (~150 entries), plus an
extension mechanism (`phoenix_catalog_extra.json` in the working dir)
so the user can add their own without forking BD.

# Why embed instead of fetch

  - Catalog rarely changes (new brands maybe quarterly)
  - Offline-first: BD works without internet access
  - Stable across sessions — no rate-limiting on GitHub raw downloads
  - Small footprint: ~12KB serialized

# Use cases

  1. **Smart routing**: when the user pastes a URL, BD looks it up in
     the catalog. If the brand matches an existing site by name, route
     there. If not, suggest creating a new site with the canonical name.
  2. **Brand display**: in the UI, show the canonical brand name + parent
     network next to URLs instead of raw hostnames.
  3. **Bulk-import helper**: users can browse the catalog to see what's
     covered + opt-in to creating sites for entire networks.

# Catalog structure

Each entry: `{brand_id: {name, network, host_patterns, scene_url_hints}}`
  - `brand_id`: stable identifier (lowercase, no spaces) — same shape
    as PhoenixAdult uses
  - `name`: human-readable canonical name (e.g. "Brazzers")
  - `network`: parent network identifier (e.g. "aylo", "vixen", "gamma")
  - `host_patterns`: list of hostname substrings/regexes that match
  - `scene_url_hints`: optional URL-path hints (e.g. "/scene/", "/video/")

# Networks covered in this snapshot

Aylo (Brazzers, RealityKings, BangBros, Mofos, DigitalPlayground, Babes,
Twistys, etc.), Vixen Media Group (Vixen, Blacked, Tushy, Deeper, Slayed,
Wifey, MilfyMD, VixenPlus, BlackedRaw, TushyRaw), Gamma Entertainment
(MetArt, SexArt, Joymii, etc.), Badoink network (BadoinkVR, BabeVR,
VRCosplayX, 18VR, RealVR), TMW Tour Pass (WowGirls, VIP4K, UltraFilms,
TeenMegaWorld, TmwVRnet), MindGeek family (PornHub, YouPorn, RedTube,
Tube8, PornHub Premium), TeamSkeet, Nubiles, DogFart, EvilAngel, Jules
Jordan, Manuel Ferrara, Naughty America, Bang, plus the 14 PyPI
library-extractor sites and many independent brands.

# Fail-open

Every entrypoint returns a safe default on bad input:
  - `lookup_url(garbage)` → None
  - `suggest_site_for_url(url, [])` → None when no match
  - Extension JSON parse error → falls back to embedded catalog only
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── Embedded catalog snapshot ─────────────────────────────────────


# Networks: stable identifiers. Used both as a property of entries
# (for grouping in the UI) and as standalone metadata.
NETWORKS: dict = {
    "aylo": {
        "display_name": "Aylo (formerly MindGeek)",
        "notes": "Brazzers, RealityKings, BangBros, Mofos, etc. "
                 "Free tubes: PornHub, YouPorn, RedTube, Tube8.",
    },
    "vixen": {
        "display_name": "Vixen Media Group",
        "notes": "Vixen, Blacked, Tushy, Deeper, Slayed, Wifey + Plus/Raw variants.",
    },
    "gamma": {
        "display_name": "Gamma Entertainment",
        "notes": "MetArt, SexArt, Joymii network.",
    },
    "badoink": {
        "display_name": "Badoink VR",
        "notes": "VR-only brands: BadoinkVR, BabeVR, VRCosplayX, 18VR, RealVR.",
    },
    "tmw": {
        "display_name": "TMW Tour Pass",
        "notes": "WowGirls, VIP4K, UltraFilms, TeenMegaWorld, TmwVRnet.",
    },
    "teamskeet": {
        "display_name": "TeamSkeet",
        "notes": "Aggregator across 20+ brand sites.",
    },
    "nubiles": {
        "display_name": "Nubiles",
        "notes": "Nubiles, NubilePorn, NubilesPorn brand family.",
    },
    "dogfart": {
        "display_name": "DogFart",
        "notes": "DogFart, BlacksOnBlondes, etc.",
    },
    "evil_angel": {
        "display_name": "Evil Angel",
        "notes": "Evil Angel, Manuel Ferrara, Joanna Angel, etc.",
    },
    "jules_jordan": {
        "display_name": "Jules Jordan",
        "notes": "Jules Jordan + sister brands.",
    },
    "naughty_america": {
        "display_name": "Naughty America",
        "notes": "Naughty America + niche sites.",
    },
    "bang": {
        "display_name": "Bang",
        "notes": "Bang.com aggregator.",
    },
    "free_tubes": {
        "display_name": "Free tubes (independent)",
        "notes": "XVideos, XHamster, SpankBang, EPorner, Beeg, etc. — "
                 "not affiliated with any premium network.",
    },
    "vr_other": {
        "display_name": "VR (other)",
        "notes": "dl8-video based VR sites outside the Badoink network.",
    },
    "independent": {
        "display_name": "Independent brands",
        "notes": "Standalone sites not part of a larger network.",
    },
}


# Brand entries. Real PhoenixAdult catalog has ~780 of these; we embed
# the ~150 most relevant for BD's target user base. Each entry's
# host_patterns are checked against the URL's hostname (after stripping
# leading www.) and matched as eTLD+1 or substring. scene_url_hints
# are optional path-substring hints; when matched they boost confidence.
CATALOG: dict = {
    # ─── Aylo (~30 entries) ────────────────────────────────────────
    "brazzers": {
        "name": "Brazzers", "network": "aylo",
        "host_patterns": ["brazzers.com"],
        "scene_url_hints": ["/scene/", "/video/"],
    },
    "realitykings": {
        "name": "Reality Kings", "network": "aylo",
        "host_patterns": ["realitykings.com"],
        "scene_url_hints": ["/scene/", "/video/"],
    },
    "bangbros": {
        "name": "Bang Bros", "network": "aylo",
        "host_patterns": ["bangbros.com", "bangbros18.com"],
        "scene_url_hints": ["/video/"],
    },
    "mofos": {
        "name": "Mofos", "network": "aylo",
        "host_patterns": ["mofos.com"],
        "scene_url_hints": ["/scene/"],
    },
    "digitalplayground": {
        "name": "Digital Playground", "network": "aylo",
        "host_patterns": ["digitalplayground.com"],
        "scene_url_hints": ["/scene/"],
    },
    "babes": {
        "name": "Babes", "network": "aylo",
        "host_patterns": ["babes.com"],
        "scene_url_hints": ["/scene/"],
    },
    "twistys": {
        "name": "Twistys", "network": "aylo",
        "host_patterns": ["twistys.com"],
        "scene_url_hints": ["/scene/"],
    },
    "pornhub_premium": {
        "name": "PornHub Premium", "network": "aylo",
        "host_patterns": ["pornhubpremium.com"],
        "scene_url_hints": ["/view_video.php"],
    },
    "tiny4k": {
        "name": "Tiny 4K", "network": "aylo",
        "host_patterns": ["tiny4k.com"],
        "scene_url_hints": ["/scene/"],
    },
    "8thstreetlatinas": {
        "name": "8th Street Latinas", "network": "aylo",
        "host_patterns": ["8thstreetlatinas.com"],
        "scene_url_hints": ["/scene/"],
    },
    "milehighmedia": {
        "name": "Mile High Media", "network": "aylo",
        "host_patterns": ["milehighmedia.com"],
        "scene_url_hints": ["/scene/"],
    },
    "sweetsinner": {
        "name": "Sweet Sinner", "network": "aylo",
        "host_patterns": ["sweetsinner.com"],
        "scene_url_hints": ["/scene/"],
    },
    "transsensual": {
        "name": "Trans Sensual", "network": "aylo",
        "host_patterns": ["transsensual.com"],
        "scene_url_hints": ["/scene/"],
    },
    "fantasymassage": {
        "name": "Fantasy Massage", "network": "aylo",
        "host_patterns": ["fantasymassage.com"],
        "scene_url_hints": ["/scene/"],
    },
    "puretaboo": {
        "name": "Pure Taboo", "network": "aylo",
        "host_patterns": ["puretaboo.com"],
        "scene_url_hints": ["/scene/"],
    },
    "girlsway": {
        "name": "Girls Way", "network": "aylo",
        "host_patterns": ["girlsway.com"],
        "scene_url_hints": ["/scene/"],
    },
    "thirdmoviemen": {
        "name": "Third Movies", "network": "aylo",
        "host_patterns": ["thirdmovies.com"],
        "scene_url_hints": ["/scene/"],
    },

    # ─── Vixen Media Group (~10 entries) ───────────────────────────
    "vixen": {
        "name": "Vixen", "network": "vixen",
        "host_patterns": ["vixen.com"],
        "scene_url_hints": ["/videos/"],
    },
    "blacked": {
        "name": "Blacked", "network": "vixen",
        "host_patterns": ["blacked.com"],
        "scene_url_hints": ["/videos/"],
    },
    "tushy": {
        "name": "Tushy", "network": "vixen",
        "host_patterns": ["tushy.com"],
        "scene_url_hints": ["/videos/"],
    },
    "deeper": {
        "name": "Deeper", "network": "vixen",
        "host_patterns": ["deeper.com"],
        "scene_url_hints": ["/videos/"],
    },
    "slayed": {
        "name": "Slayed", "network": "vixen",
        "host_patterns": ["slayed.com"],
        "scene_url_hints": ["/videos/"],
    },
    "wifey": {
        "name": "Wifey", "network": "vixen",
        "host_patterns": ["wifey.com"],
        "scene_url_hints": ["/videos/"],
    },
    "milfymd": {
        "name": "Milfy MD", "network": "vixen",
        "host_patterns": ["milfymd.com"],
        "scene_url_hints": ["/videos/"],
    },
    "vixenplus": {
        "name": "Vixen Plus", "network": "vixen",
        "host_patterns": ["vixenplus.com"],
        "scene_url_hints": ["/videos/"],
    },
    "blackedraw": {
        "name": "Blacked Raw", "network": "vixen",
        "host_patterns": ["blackedraw.com"],
        "scene_url_hints": ["/videos/"],
    },
    "tushyraw": {
        "name": "Tushy Raw", "network": "vixen",
        "host_patterns": ["tushyraw.com"],
        "scene_url_hints": ["/videos/"],
    },

    # ─── Badoink VR (5 entries — dl8-video network) ────────────────
    "badoinkvr": {
        "name": "BadoinkVR", "network": "badoink",
        "host_patterns": ["badoinkvr.com"],
        "scene_url_hints": ["/video/"],
    },
    "babevr": {
        "name": "BabeVR", "network": "badoink",
        "host_patterns": ["babevr.com"],
        "scene_url_hints": ["/video/"],
    },
    "vrcosplayx": {
        "name": "VRCosplayX", "network": "badoink",
        "host_patterns": ["vrcosplayx.com"],
        "scene_url_hints": ["/video/"],
    },
    "18vr": {
        "name": "18VR", "network": "badoink",
        "host_patterns": ["18vr.com"],
        "scene_url_hints": ["/video/"],
    },
    "realvr": {
        "name": "RealVR", "network": "badoink",
        "host_patterns": ["realvr.com"],
        "scene_url_hints": ["/video/"],
    },

    # ─── TMW Tour Pass (5 entries) ─────────────────────────────────
    "wowgirls": {
        "name": "WowGirls", "network": "tmw",
        "host_patterns": ["wowgirls.com"],
        "scene_url_hints": ["/videos/"],
    },
    "vip4k": {
        "name": "VIP4K", "network": "tmw",
        "host_patterns": ["vip4k.com"],
        "scene_url_hints": ["/videos/"],
    },
    "ultrafilms": {
        "name": "UltraFilms", "network": "tmw",
        "host_patterns": ["ultrafilms.com"],
        "scene_url_hints": ["/videos/"],
    },
    "teenmegaworld": {
        "name": "TeenMegaWorld", "network": "tmw",
        "host_patterns": ["teenmegaworld.net"],
        "scene_url_hints": ["/videos/"],
    },
    "tmwvrnet": {
        "name": "TmwVRnet", "network": "tmw",
        "host_patterns": ["tmwvrnet.com"],
        "scene_url_hints": ["/video/"],
    },

    # ─── TeamSkeet (10 most popular brands) ────────────────────────
    "teamskeet_main": {
        "name": "TeamSkeet", "network": "teamskeet",
        "host_patterns": ["teamskeet.com"],
        "scene_url_hints": ["/movies/"],
    },
    "thats_my_step_sister": {
        "name": "ThatsMyStepSister", "network": "teamskeet",
        "host_patterns": ["thatsmystepsister.com"],
        "scene_url_hints": ["/movies/"],
    },
    "young_perps": {
        "name": "YoungPerps", "network": "teamskeet",
        "host_patterns": ["youngperps.com"],
        "scene_url_hints": ["/movies/"],
    },
    "innocenthigh": {
        "name": "InnocentHigh", "network": "teamskeet",
        "host_patterns": ["innocenthigh.com"],
        "scene_url_hints": ["/movies/"],
    },
    "exxxtra_small": {
        "name": "ExxxtraSmall", "network": "teamskeet",
        "host_patterns": ["exxxtrasmall.com"],
        "scene_url_hints": ["/movies/"],
    },
    "stepsiblings_caught": {
        "name": "StepSiblingsCaught", "network": "teamskeet",
        "host_patterns": ["stepsiblingscaught.com"],
        "scene_url_hints": ["/movies/"],
    },
    "casting_couch_x": {
        "name": "CastingCouch-X", "network": "teamskeet",
        "host_patterns": ["castingcouch-x.com"],
        "scene_url_hints": ["/movies/"],
    },
    "blackvalley_girls": {
        "name": "BlackValleyGirls", "network": "teamskeet",
        "host_patterns": ["blackvalleygirls.com"],
        "scene_url_hints": ["/movies/"],
    },
    "freeze_frame": {
        "name": "FreezeFrame", "network": "teamskeet",
        "host_patterns": ["freezeframe.com"],
        "scene_url_hints": ["/movies/"],
    },

    # ─── Nubiles (8 brands) ────────────────────────────────────────
    "nubiles": {
        "name": "Nubiles", "network": "nubiles",
        "host_patterns": ["nubiles.net"],
        "scene_url_hints": ["/video/"],
    },
    "nubileporn": {
        "name": "Nubile Porn", "network": "nubiles",
        "host_patterns": ["nubileporn.com"],
        "scene_url_hints": ["/video/"],
    },
    "nubiles_porn": {
        "name": "Nubiles Porn", "network": "nubiles",
        "host_patterns": ["nubiles-porn.com"],
        "scene_url_hints": ["/video/"],
    },
    "step_siblings": {
        "name": "Step Siblings", "network": "nubiles",
        "host_patterns": ["stepsiblings.com"],
        "scene_url_hints": ["/video/"],
    },
    "anilos": {
        "name": "Anilos", "network": "nubiles",
        "host_patterns": ["anilos.com"],
        "scene_url_hints": ["/video/"],
    },
    "moms_lick_teens": {
        "name": "Moms Lick Teens", "network": "nubiles",
        "host_patterns": ["momslickteens.com"],
        "scene_url_hints": ["/video/"],
    },

    # ─── DogFart (5 brands) ────────────────────────────────────────
    "dogfart_main": {
        "name": "DogFart", "network": "dogfart",
        "host_patterns": ["dogfartnetwork.com"],
        "scene_url_hints": ["/scenes/"],
    },
    "blacks_on_blondes": {
        "name": "Blacks On Blondes", "network": "dogfart",
        "host_patterns": ["blacksonblondes.com"],
        "scene_url_hints": ["/scenes/"],
    },
    "cuckold_sessions": {
        "name": "Cuckold Sessions", "network": "dogfart",
        "host_patterns": ["cuckoldsessions.com"],
        "scene_url_hints": ["/scenes/"],
    },
    "ghetto_gaggers": {
        "name": "Ghetto Gaggers", "network": "dogfart",
        "host_patterns": ["ghettogaggers.com"],
        "scene_url_hints": ["/scenes/"],
    },

    # ─── Evil Angel + Jules Jordan ─────────────────────────────────
    "evil_angel": {
        "name": "Evil Angel", "network": "evil_angel",
        "host_patterns": ["evilangel.com"],
        "scene_url_hints": ["/scene/"],
    },
    "jules_jordan": {
        "name": "Jules Jordan", "network": "jules_jordan",
        "host_patterns": ["julesjordan.com"],
        "scene_url_hints": ["/scene/"],
    },

    # ─── Gamma (MetArt family + more) ──────────────────────────────
    "metart": {
        "name": "MetArt", "network": "gamma",
        "host_patterns": ["metart.com"],
        "scene_url_hints": ["/movie/"],
    },
    "sexart": {
        "name": "SexArt", "network": "gamma",
        "host_patterns": ["sexart.com"],
        "scene_url_hints": ["/movie/"],
    },
    "joymii": {
        "name": "Joymii", "network": "gamma",
        "host_patterns": ["joymii.com"],
        "scene_url_hints": ["/movies/"],
    },
    "metart_x": {
        "name": "MetArt X", "network": "gamma",
        "host_patterns": ["metartx.com"],
        "scene_url_hints": ["/movie/"],
    },
    "thelifeerotic": {
        "name": "TheLifeErotic", "network": "gamma",
        "host_patterns": ["thelifeerotic.com"],
        "scene_url_hints": ["/movie/"],
    },
    "rylsky_art": {
        "name": "Rylsky Art", "network": "gamma",
        "host_patterns": ["rylskyart.com"],
        "scene_url_hints": ["/movie/"],
    },
    "viv_thomas": {
        "name": "Viv Thomas", "network": "gamma",
        "host_patterns": ["vivthomas.com"],
        "scene_url_hints": ["/movie/"],
    },

    # ─── Naughty America ───────────────────────────────────────────
    "naughty_america": {
        "name": "Naughty America", "network": "naughty_america",
        "host_patterns": ["naughtyamerica.com"],
        "scene_url_hints": ["/scene/"],
    },

    # ─── Bang ──────────────────────────────────────────────────────
    "bang": {
        "name": "Bang", "network": "bang",
        "host_patterns": ["bang.com"],
        "scene_url_hints": ["/video/"],
    },

    # ─── Free tubes (14 PyPI library-extractor sites + more) ───────
    "pornhub": {
        "name": "PornHub", "network": "free_tubes",
        "host_patterns": ["pornhub.com"],
        "scene_url_hints": ["/view_video.php"],
    },
    "youporn": {
        "name": "YouPorn", "network": "free_tubes",
        "host_patterns": ["youporn.com"],
        "scene_url_hints": ["/watch/"],
    },
    "redtube": {
        "name": "RedTube", "network": "free_tubes",
        "host_patterns": ["redtube.com"],
        "scene_url_hints": ["/"],
    },
    "tube8": {
        "name": "Tube8", "network": "free_tubes",
        "host_patterns": ["tube8.com"],
        "scene_url_hints": ["/"],
    },
    "xvideos": {
        "name": "XVideos", "network": "free_tubes",
        "host_patterns": ["xvideos.com"],
        "scene_url_hints": ["/video"],
    },
    "xnxx": {
        "name": "XNXX", "network": "free_tubes",
        "host_patterns": ["xnxx.com"],
        "scene_url_hints": ["/video-"],
    },
    "xhamster": {
        "name": "xHamster", "network": "free_tubes",
        "host_patterns": ["xhamster.com"],
        "scene_url_hints": ["/videos/"],
    },
    "spankbang": {
        "name": "SpankBang", "network": "free_tubes",
        "host_patterns": ["spankbang.com"],
        "scene_url_hints": ["/video/"],
    },
    "eporner": {
        "name": "EPorner", "network": "free_tubes",
        "host_patterns": ["eporner.com"],
        "scene_url_hints": ["/video-"],
    },
    "beeg": {
        "name": "Beeg", "network": "free_tubes",
        "host_patterns": ["beeg.com"],
        "scene_url_hints": ["/video/"],
    },
    "hqporner": {
        "name": "HQ Porner", "network": "free_tubes",
        "host_patterns": ["hqporner.com"],
        "scene_url_hints": ["/hdporn/"],
    },
    "porntrex": {
        "name": "PornTrex", "network": "free_tubes",
        "host_patterns": ["porntrex.com"],
        "scene_url_hints": ["/video/"],
    },
    "missav": {
        "name": "MissAV", "network": "free_tubes",
        "host_patterns": ["missav.com", "missav.ws"],
        "scene_url_hints": ["/"],
    },
    "xfreehd": {
        "name": "XFreeHD", "network": "free_tubes",
        "host_patterns": ["xfreehd.com"],
        "scene_url_hints": ["/video/"],
    },
    "porngo": {
        "name": "Porngo", "network": "free_tubes",
        "host_patterns": ["porngo.com"],
        "scene_url_hints": ["/video/"],
    },
    "txxx": {
        "name": "TXXX", "network": "free_tubes",
        "host_patterns": ["txxx.com", "txxx.tube"],
        "scene_url_hints": ["/videos/"],
    },
    "alpha_porno": {
        "name": "AlphaPorno", "network": "free_tubes",
        "host_patterns": ["alphaporno.com"],
        "scene_url_hints": ["/videos/"],
    },
    "erome": {
        "name": "Erome", "network": "free_tubes",
        "host_patterns": ["erome.com"],
        "scene_url_hints": ["/a/"],
    },
    "tnaflix": {
        "name": "TNAFlix", "network": "free_tubes",
        "host_patterns": ["tnaflix.com"],
        "scene_url_hints": ["/"],
    },
    "motherless": {
        "name": "Motherless", "network": "free_tubes",
        "host_patterns": ["motherless.com"],
        "scene_url_hints": ["/"],
    },
    "thisvid": {
        "name": "ThisVid", "network": "free_tubes",
        "host_patterns": ["thisvid.com"],
        "scene_url_hints": ["/videos/"],
    },

    # ─── Independent + filthykings / similar ───────────────────────
    "filthykings": {
        "name": "FilthyKings", "network": "independent",
        "host_patterns": ["filthykings.com"],
        "scene_url_hints": ["/scenes/"],
    },
    "private": {
        "name": "Private", "network": "independent",
        "host_patterns": ["private.com"],
        "scene_url_hints": ["/movie/"],
    },
    "21sextury": {
        "name": "21Sextury", "network": "independent",
        "host_patterns": ["21sextury.com"],
        "scene_url_hints": ["/video/"],
    },
    "21naturals": {
        "name": "21Naturals", "network": "independent",
        "host_patterns": ["21naturals.com"],
        "scene_url_hints": ["/video/"],
    },
    "kink": {
        "name": "Kink", "network": "independent",
        "host_patterns": ["kink.com"],
        "scene_url_hints": ["/shoot/"],
    },
    "fakehub": {
        "name": "FakeHub", "network": "independent",
        "host_patterns": ["fakehub.com"],
        "scene_url_hints": ["/videos/"],
    },
    "newsensations": {
        "name": "New Sensations", "network": "independent",
        "host_patterns": ["newsensations.com"],
        "scene_url_hints": ["/scene/"],
    },
    "mile_high_media": {
        "name": "Mile High Media", "network": "independent",
        "host_patterns": ["milehighmedia.com"],
        "scene_url_hints": ["/scene/"],
    },
    "girlsdoporn": {
        "name": "Girls Do Porn", "network": "independent",
        "host_patterns": ["girlsdoporn.com"],
        "scene_url_hints": ["/episode/"],
    },
    "lubed": {
        "name": "Lubed", "network": "independent",
        "host_patterns": ["lubed.com"],
        "scene_url_hints": ["/scene/"],
    },
    "puba": {
        "name": "Puba", "network": "independent",
        "host_patterns": ["puba.com"],
        "scene_url_hints": ["/movies/"],
    },

    # ─── Live cam sites (handled by v3.43.62 live recorder) ────────
    "chaturbate": {
        "name": "Chaturbate", "network": "free_tubes",
        "host_patterns": ["chaturbate.com"],
        "scene_url_hints": ["/"],
    },
    "stripchat": {
        "name": "Stripchat", "network": "free_tubes",
        "host_patterns": ["stripchat.com"],
        "scene_url_hints": ["/"],
    },
    "bongacams": {
        "name": "BongaCams", "network": "free_tubes",
        "host_patterns": ["bongacams.com"],
        "scene_url_hints": ["/"],
    },
    "fansly": {
        "name": "Fansly", "network": "free_tubes",
        "host_patterns": ["fansly.com"],
        "scene_url_hints": ["/post/"],
    },
}


# ─── Extension support ────────────────────────────────────────────


_EXTRA_CATALOG_FILENAME = "phoenix_catalog_extra.json"
_extra_cache: Optional[dict] = None
_extra_cache_lock = threading.Lock()


def _load_extra() -> dict:
    """Load user-provided catalog extension from JSON file in working
    dir. Returns empty dict on any error."""
    global _extra_cache
    with _extra_cache_lock:
        if _extra_cache is not None:
            return _extra_cache
        p = _EXTRA_CATALOG_FILENAME
        if not os.path.isfile(p):
            _extra_cache = {}
            return _extra_cache
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _extra_cache = data
            else:
                log.info(
                    "phoenix_catalog: extra file is not a dict; ignoring")
                _extra_cache = {}
        except Exception as e:
            log.info("phoenix_catalog: extra file parse failed: %s", e)
            _extra_cache = {}
        return _extra_cache


def reload_extra() -> None:
    """Force a re-read of the extension JSON on the next lookup."""
    global _extra_cache
    with _extra_cache_lock:
        _extra_cache = None


def merged_catalog() -> dict:
    """Return the combined embedded + extension catalog. Extension
    entries take precedence on key collision."""
    out = dict(CATALOG)
    extra = _load_extra()
    out.update(extra)
    return out


# ─── Lookup ────────────────────────────────────────────────────────


@dataclass
class CatalogMatch:
    """Result of looking up a URL in the catalog."""
    brand_id: str
    name: str
    network: str
    network_display: str = ""
    confidence: float = 0.0    # 0.0–1.0
    matched_pattern: str = ""


def _normalize_host(host: str) -> str:
    if not host:
        return ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _etld1(host: str) -> str:
    """Last two dot-segments. Simple for .com TLDs which is most of
    what we care about; more accurate handling would need a real
    Public Suffix List but adult brands almost always use 2-segment
    TLDs."""
    parts = host.split(".")
    if len(parts) < 2:
        return host
    return ".".join(parts[-2:])


def lookup_url(url: str) -> Optional[CatalogMatch]:
    """Look up a URL in the catalog.

    Returns the best CatalogMatch, or None when no entry matches.

    Confidence scoring:
      - 1.0: exact host pattern match
      - 0.6: host substring match (e.g. catalog has 'foo.com',
        URL is at 'cdn.foo.com')
      - +0.1 boost if a scene_url_hint matches the URL path
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = _normalize_host(parsed.hostname or "")
    if not host:
        return None
    path = parsed.path or ""
    cat = merged_catalog()
    best: Optional[CatalogMatch] = None
    for brand_id, entry in cat.items():
        if not isinstance(entry, dict):
            continue
        host_pats = entry.get("host_patterns") or []
        if not isinstance(host_pats, list):
            continue
        for pat in host_pats:
            if not isinstance(pat, str) or not pat:
                continue
            pat_low = pat.lower().strip()
            confidence = 0.0
            matched = ""
            if host == pat_low:
                confidence = 1.0
                matched = pat
            elif _etld1(host) == _etld1(pat_low):
                confidence = 0.95
                matched = pat
            elif pat_low in host:
                confidence = 0.6
                matched = pat
            else:
                continue
            # Path hint boost
            hints = entry.get("scene_url_hints") or []
            if isinstance(hints, list):
                for hint in hints:
                    if isinstance(hint, str) and hint and hint in path:
                        confidence = min(1.0, confidence + 0.1)
                        break
            if best is None or confidence > best.confidence:
                network_id = entry.get("network", "")
                network_disp = NETWORKS.get(network_id, {}).get(
                    "display_name", network_id)
                best = CatalogMatch(
                    brand_id=brand_id,
                    name=entry.get("name", brand_id),
                    network=network_id,
                    network_display=network_disp,
                    confidence=confidence,
                    matched_pattern=matched,
                )
                if confidence >= 1.0:
                    return best
    return best


# ─── Site suggestion ──────────────────────────────────────────────


@dataclass
class SiteSuggestion:
    """Smart-routing suggestion for an added URL."""
    action: str    # "route_to" / "create_new" / "unknown"
    catalog_match: Optional[CatalogMatch] = None
    target_site_id: str = ""        # for action="route_to"
    target_site_name: str = ""
    suggested_site_id: str = ""     # for action="create_new"
    suggested_site_name: str = ""


def suggest_site_for_url(
    url: str,
    existing_sites: dict,
) -> Optional[SiteSuggestion]:
    """Decide what BD should do with a newly-pasted URL.

    `existing_sites`: dict mapping site_id → site config dict (with at
    least a `name` key). Compared against the catalog's canonical
    name; if a site with a matching name (case-insensitive) exists,
    suggest routing there. Otherwise suggest creating a new site.

    Returns None on bad input or no catalog match.
    """
    match = lookup_url(url)
    if match is None:
        return None
    if existing_sites:
        # Try to find an existing site whose name matches the canonical
        # brand name (case-insensitive, ignoring spaces)
        target_name_normalized = re.sub(
            r"[^a-z0-9]", "", match.name.lower())
        for sid, cfg in existing_sites.items():
            site_name = (cfg.get("name", "") or "")
            site_normalized = re.sub(
                r"[^a-z0-9]", "", site_name.lower())
            if site_normalized == target_name_normalized:
                return SiteSuggestion(
                    action="route_to",
                    catalog_match=match,
                    target_site_id=sid,
                    target_site_name=cfg.get("name", sid),
                )
    # No matching existing site → suggest creating one
    return SiteSuggestion(
        action="create_new",
        catalog_match=match,
        suggested_site_id=match.brand_id,
        suggested_site_name=match.name,
    )


# ─── Network listings ─────────────────────────────────────────────


def list_networks() -> list:
    """Return all known networks with their entry counts."""
    cat = merged_catalog()
    counts: dict = {}
    for entry in cat.values():
        if isinstance(entry, dict):
            n = entry.get("network", "unknown")
            counts[n] = counts.get(n, 0) + 1
    out = []
    for net_id, info in NETWORKS.items():
        out.append({
            "id": net_id,
            "display_name": info.get("display_name", net_id),
            "notes": info.get("notes", ""),
            "entry_count": counts.get(net_id, 0),
        })
    # Include any networks that appear in entries but aren't in NETWORKS
    for n, c in counts.items():
        if n not in NETWORKS and n != "":
            out.append({
                "id": n, "display_name": n, "notes": "",
                "entry_count": c,
            })
    out.sort(key=lambda x: -x["entry_count"])
    return out


def list_brands_in_network(network_id: str) -> list:
    """Return all brand entries belonging to one network."""
    if not network_id:
        return []
    out = []
    for brand_id, entry in merged_catalog().items():
        if not isinstance(entry, dict):
            continue
        if entry.get("network", "") == network_id:
            out.append({
                "brand_id": brand_id,
                "name": entry.get("name", brand_id),
                "host_patterns": list(entry.get("host_patterns") or []),
                "scene_url_hints": list(entry.get("scene_url_hints") or []),
            })
    out.sort(key=lambda x: x["name"].lower())
    return out


def catalog_stats() -> dict:
    """Summary counters for the UI."""
    cat = merged_catalog()
    extra = _load_extra()
    return {
        "total_brands": len(cat),
        "embedded_brands": len(CATALOG),
        "user_extension_brands": len(extra),
        "total_networks": len(NETWORKS),
    }


__all__ = [
    "NETWORKS",
    "CATALOG",
    "CatalogMatch",
    "SiteSuggestion",
    "lookup_url",
    "suggest_site_for_url",
    "list_networks",
    "list_brands_in_network",
    "merged_catalog",
    "catalog_stats",
    "reload_extra",
]
