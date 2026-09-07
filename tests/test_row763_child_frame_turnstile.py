"""Row 763: child-frame widget evidence reaches challenge routing."""
import pytest

from bulk_downloader.challenge_classify import classify, route_challenge
from bulk_downloader.session_capture import (
    observation_is_conclusive,
    observe_page_for_challenge,
)


BD_GATE_SCOPE = "repo-wide"


class _Frame:
    def __init__(self, url):
        self.url = url


class _Page:
    def __init__(self, *, body="", url="https://members.example.test/", frames=()):
        self.url = url
        self._body = body
        self._frames = list(frames)

    def title(self):
        return ""

    def inner_text(self, selector):
        assert selector == "body"
        return self._body

    def frames(self):
        return self._frames


def test_child_frame_turnstile_is_routed_without_widget_interaction():
    child_url = ("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/"
                 "h/b/orchestrate/chl_page/v1")
    page = _Page(
        body="Please wait... Performing security verification",
        frames=[_Frame(child_url)],
    )
    assert len(page._frames) == 1, "precondition: exactly one child frame exists"
    assert page._frames[0].url == child_url

    observation = observe_page_for_challenge(page)
    routed = route_challenge(observation)

    assert routed["type"] == "turnstile"
    assert observation["frame_urls"] == [child_url]
    assert len(observation["markers"]) == 1, "frame observation records one marker"
    assert routed["challenge_present"] is True


def test_frame_urls_do_not_turn_a_public_login_url_into_a_login_wall():
    page = _Page(url="https://example.test/en/login", frames=[_Frame("https://example.test/en/login")])
    assert len(page._frames) == 1, "precondition: the public page has one same-origin frame"

    routed = route_challenge(observe_page_for_challenge(page))

    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


def test_body_login_wall_remains_classified_by_the_regular_observation_channel():
    observation = {"text": "Please sign in with your password", "title": "", "markers": []}
    assert observation["text"], "precondition: body carries login-wall evidence"

    assert classify(observation)["type"] == "login-wall"


def test_unreadable_frame_collection_is_empty_and_not_conclusive():
    class _UnreadablePage(_Page):
        def frames(self):
            raise RuntimeError("fixture frame read refused")

    page = _UnreadablePage()
    observation = observe_page_for_challenge(page)

    assert observation["frame_urls"] == []
    assert observation_is_conclusive(observation) is False


def test_readable_nonwidget_frame_does_not_make_unreadable_page_conclusive():
    page = _Page(frames=[_Frame("https://example.test/embed")])
    assert len(page._frames) == 1, "precondition: one readable nonwidget frame"
    observation = observe_page_for_challenge(page)
    assert observation["markers"] == []
    assert observation_is_conclusive(observation) is False


@pytest.mark.parametrize("marker", [
    "challenges.cloudflare", "hcaptcha", "h-captcha", "recaptcha",
    "g-recaptcha", "cf-turnstile", "turnstile",
])
def test_each_widget_frame_marker_makes_unreadable_body_conclusive(marker):
    class _UnreadableBody(_Page):
        def inner_text(self, selector):
            raise RuntimeError("fixture body read refused")

    frame_url = f"https://widgets.example.test/{marker}"
    observation = observe_page_for_challenge(
        _UnreadableBody(frames=[_Frame(frame_url)]),
    )
    assert observation["text"] == ""
    assert observation["frame_urls"] == [frame_url]
    assert observation["markers"] == ["child_frame_url"]
    assert observation_is_conclusive(observation) is True


def test_frame_observation_stops_after_eight_urls_before_reading_ninth():
    reads = []

    class _TrackedFrame:
        def __init__(self, number):
            self.number = number

        @property
        def url(self):
            reads.append(self.number)
            return f"https://example.test/frame/{self.number}"

    page = _Page(frames=[_TrackedFrame(n) for n in range(10)])
    assert len(page._frames) == 10
    observation = observe_page_for_challenge(page)
    assert observation["frame_urls"] == [
        f"https://example.test/frame/{n}" for n in range(8)
    ]
    assert reads == list(range(8))


def test_frame_url_storage_is_truncated_to_512_characters():
    frame_url = "https://example.test/" + "a" * 600
    assert len(frame_url) > 512
    observation = observe_page_for_challenge(_Page(frames=[_Frame(frame_url)]))
    assert observation["frame_urls"] == [frame_url[:512]]
    assert len(observation["frame_urls"][0]) == 512


def test_nonstring_frame_url_is_ignored_and_later_widget_is_observed():
    frame_url = "https://widgets.example.test/hcaptcha"
    # A truthy, sliceable non-string would be stored if the type guard vanished.
    invalid_url = ["https://example.test/embed"]
    assert invalid_url and not isinstance(invalid_url, str)
    observation = observe_page_for_challenge(
        _Page(frames=[_Frame(invalid_url), _Frame(frame_url)]),
    )
    assert observation["frame_urls"] == [frame_url]
    assert observation["markers"] == ["child_frame_url"]


def test_unreadable_frame_does_not_hide_later_callable_widget_url():
    reads = []

    class _UnreadableFrame:
        @property
        def url(self):
            reads.append("unreadable")
            raise RuntimeError("fixture frame URL read refused")

    frame_url = "https://widgets.example.test/turnstile"

    def read_url():
        reads.append("widget")
        return frame_url

    page = _Page()
    # Playwright exposes frames as a property; wrappers may expose callables.
    page.frames = [_UnreadableFrame(), _Frame(read_url)]
    observation = observe_page_for_challenge(page)
    assert reads == ["unreadable", "widget"]
    assert observation["frame_urls"] == [frame_url]
    assert observation["markers"] == ["child_frame_url"]
