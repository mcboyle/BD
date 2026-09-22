"""Every non-httpx egress site in this package, accounted for in the tree (row 805).

``tools/ssrf_client_census.py`` derives the egress population from the tree --
``urllib.request`` (``urlopen`` and a ``build_opener`` opener's ``.open``),
``requests`` (a verb call or a ``Session`` subclass) and ``aiohttp`` -- and
reconciles it against ``ACCOUNTED`` below.  A site this file does not name
fails ``tests/test_row805_ssrf_census_covers_every_transport.py``, and so does
an entry here that matches no site.  So a new egress site cannot be added
without a reader deciding, in the tree, why it is safe.

The key is ``<tracked path>::<qualified owner>``, which survives the edits a
line number does not.  The value is ``(kind, reason)``:

``guarded``
    An SSRF check named in the reason runs in the tree before the send.
    This says "classified", not "pinned": two of these classify the host and
    then connect by name, which still leaves the DNS rebinding window that
    ``bulk_downloader/ssrf_transport.py`` closes for httpx (rows 687/703).
``exempt``
    No attacker-reachable input steers the destination: a literal endpoint, a
    fixed vendor API base, or a destination the single operator configures.
    An exemption is a judgement about reachability, not a guarantee about the
    host, and it is re-read whenever the owning function changes.

This module holds data only; it imports nothing and the census reads it with
``ast``, never by importing it.
"""
from __future__ import annotations

ACCOUNTED = {
    # -- guarded: an in-tree check runs before the send -------------------
    "bulk_downloader/urllib_ssrf.py::PinnedUrlOpener.open": (
        "guarded",
        "PinnedUrlOpener resolves every host answer, rejects a disallowed one, "
        "and dispatches urllib only to the selected vetted IP literal (row 728)"),
    # deep_http.guarded_open no longer dispatches urllib itself: row 839 routes
    # it through http_client.proxy_open(direct_open=_OPENER.open), so the
    # census no longer derives a site there (the _NoRedirect opener + _check
    # still guard the direct path).
    "bulk_downloader/deep_http.py::_GuardedSession": (
        "guarded",
        "the request override disables requests' redirects, classifies every "
        "hop through _check and refuses a cross-origin Location"),
    "bulk_downloader/webhooks.py::_deliver_one": (
        "guarded",
        "_validate_webhook_url re-vets the stored subscription URL at delivery "
        "time and the POST sets allow_redirects=False (F-CBD12-01)"),
    "bulk_downloader/selector_playground.py::fetch_page": (
        "guarded",
        "_host_public is re-checked at every hop with allow_redirects=False, so "
        "a public first hop cannot 302 the fetch to an internal address"),
    "bulk_downloader/site_weather.py::probe_http._fetch": (
        "guarded",
        "_host_is_public is re-checked at every redirect hop with "
        "allow_redirects=False, so a public first hop cannot 302 the probe to "
        "an internal address; the verb is chosen at runtime, the module is not"),
    "bulk_downloader/aiassist.py::_post_json": (
        "guarded",
        "_pin_lan_endpoint resolves the operator's endpoint and sends to the "
        "pinned literal with a Host header (F-CBD03-01)"),
    "bulk_downloader/service_mesh.py::_default_transport": (
        "exempt",
        "the route table is application configuration and the request may add "
        "only a validated relative path; it cannot select a destination host"),

    # -- exempt: no attacker-reachable input steers the destination -------
    "bulk_downloader/ai_provider.py::AIProvider._http_post": (
        "exempt",
        "the provider endpoint comes from the operator's own AI configuration, "
        "never from a request, and the path is fixed by the provider class"),
    "bulk_downloader/ai_provider.py::OllamaProvider.health_check": (
        "exempt",
        "GETs /api/tags on the operator-configured Ollama endpoint; no caller "
        "input reaches the URL"),
    "bulk_downloader/ai_provider.py::OpenAIProvider.health_check": (
        "exempt",
        "GETs /v1/models on the operator-configured OpenAI endpoint with the "
        "operator's own key; no caller input reaches the URL"),
    "bulk_downloader/ai_provider.py::GeminiProvider.health_check": (
        "exempt",
        "GETs /v1beta/models on the operator-configured Gemini endpoint with "
        "the operator's own key; no caller input reaches the URL"),
    "bulk_downloader/app_health.py::api_health_v2": (
        "exempt",
        "the Ollama reachability probe is the literal http://127.0.0.1:11434 "
        "URL written here; nothing in the request can steer it"),
    "bulk_downloader/ollama_boot_probe.py::_request_json": (
        "exempt",
        "every call builds the URL from the boot probe's operator-configured "
        "endpoint attribute plus a fixed path"),
    "bulk_downloader/audio_transcriber.py::_request_json": (
        "exempt",
        "every call builds the URL from AudioTranscriber's declared satellite "
        "endpoint attribute plus a fixed path (row856); tests inject a "
        "fixture transport and never reach this function"),
    "bulk_downloader/request_replay.py::replay": (
        "exempt",
        "replays a captured request against the operator's own app, whose "
        "base_url defaults to loopback and is an operator argument"),
    "bulk_downloader/cli_dashboard.py::_fetch_status": (
        "exempt",
        "polls the operator's own BD instance at the api_base the operator "
        "passed to the dashboard CLI"),
    "bulk_downloader/cli_dashboard.py::_fetch_capacity": (
        "exempt",
        "polls the operator's own BD instance at the api_base the operator "
        "passed to the dashboard CLI"),
    "bulk_downloader/cli_dashboard.py::_fetch_events": (
        "exempt",
        "polls the operator's own BD instance at the api_base the operator "
        "passed to the dashboard CLI"),
    "bulk_downloader/tray_app.py::_poll_loop": (
        "exempt",
        "polls the operator's own BD instance at the tray's configured url; "
        "no request-supplied value reaches it"),
    "bulk_downloader/tray_app.py::_post": (
        "exempt",
        "posts a tray control action to the operator's own BD instance at the "
        "tray's configured url"),
"bulk_downloader/tpdb.py::_request": (
        "exempt",
        "authenticated GET against the fixed TPDB API base written in this "
        "module; the caller chooses a path and query, not a host"),
    "bulk_downloader/wayback_cdx.py::find_snapshots": (
        "exempt",
        "queries the fixed Wayback CDX API base written in this module; the "
        "caller's URL travels as a query parameter, not as the destination"),
    "bulk_downloader/wayback_cdx.py::availability": (
        "exempt",
        "queries the literal archive.org availability endpoint written here; "
        "the caller's URL travels as a query parameter"),
    "bulk_downloader/ytdlp_updater.py::_default_fetch": (
        "exempt",
        "the only caller passes the literal PyPI JSON URL defined in that "
        "module to learn the latest yt-dlp version at boot"),
    "bulk_downloader/guardrails.py::_default_request": (
        "exempt",
        "the only caller passes the fixed local guardrails endpoint constant; "
        "untrusted metadata is sent in the JSON body, never used as a destination"),
    "bulk_downloader/semantic_search.py::_rerank": (
        "exempt",
        "the configured endpoint is restricted to a loopback host before the "
        "reranker request is constructed, so no caller can select external egress"),
    "bulk_downloader/subtitle_search.py::index_video": (
        "exempt",
        "the destination is the operator-configured Elasticsearch endpoint "
        "(SUBTITLE_SEARCH_ELASTICSEARCH_URL, default loopback:9200); "
        "video_id/dialogue travel as the bulk request body, never as the host"),
    "bulk_downloader/subtitle_search.py::search": (
        "exempt",
        "the destination is the operator-configured Elasticsearch endpoint; "
        "the caller's query text travels as the search body, never as the host"),
    "bulk_downloader/subtitle_search.py::status": (
        "exempt",
        "reachability probe against the operator-configured Elasticsearch "
        "endpoint; no caller input reaches the URL"),
}

