"""tpl95-wowgirls-1: a photo-set cohort tied with the film cohort is not scenes.

Measured on venus.wowgirls.com/ (discovery run eca29708, 03:19Z): the member
home carries a /film/<id>/<slug> row and a /gallery/<id>/<slug> row of the same
size, both with thumbnails.  The tie admitted both cohorts, and "newest 3"
queued three /gallery/ photo sets as scenes.
"""
BD_GATE_SCOPE = "module"

from bulk_downloader.scene_crawler import _scene_cohort


_HOME = "https://venus.wowgirls.com/"
_FILMS = (
    "https://venus.wowgirls.com/film/fc43abef/can-t-be-hotter-than-this",
    "https://venus.wowgirls.com/film/ba91fb1f/her-deepest-needs",
    "https://venus.wowgirls.com/film/wfd490d0/stunning-princess",
)
_GALLERIES = (
    "https://venus.wowgirls.com/gallery/h5096456/always-nearby",
    "https://venus.wowgirls.com/gallery/y8714a47/angel-fever",
    "https://venus.wowgirls.com/gallery/ja9b31fe/starlight-seductions",
)


def _cards(urls):
    return [{"url": url, "text": url.rsplit("/", 1)[-1], "has_img": True}
            for url in urls]


def test_tpl95_wowgirls_1_tied_gallery_cohort_is_not_admitted_beside_films():
    # Galleries first, as on the page: the newest cards are photo sets.
    scenes, shapes = _scene_cohort(_cards(_GALLERIES) + _cards(_FILMS), _HOME)
    urls = [scene["url"] for scene in scenes]
    assert sorted(urls) == sorted(_FILMS), f"tpl95-wowgirls-1 queued non-scene cards: {urls}"
    assert shapes == ["/film/<slug>/<slug>"], shapes


def test_tpl95_wowgirls_1_films_listing_is_unchanged():
    scenes, shapes = _scene_cohort(_cards(_FILMS), "https://venus.wowgirls.com/films/")
    assert sorted(scene["url"] for scene in scenes) == sorted(_FILMS)
    assert shapes == ["/film/<slug>/<slug>"]


def test_tpl95_wowgirls_1_tie_without_scene_evidence_still_admits_both():
    # Control: the tie-break only acts on product scene-URL evidence.  Two
    # tied cohorts neither of which reads as a scene URL keep today's result.
    left = tuple(f"https://x.example/set/a{n}/set-{n}" for n in range(3))
    right = tuple(f"https://x.example/gallery/b{n}/gal-{n}" for n in range(3))
    scenes, shapes = _scene_cohort(_cards(left) + _cards(right), "https://x.example/")
    assert sorted(scene["url"] for scene in scenes) == sorted(left + right)
    assert len(shapes) == 2
