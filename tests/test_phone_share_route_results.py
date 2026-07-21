from flask import Flask

from bulk_downloader import app_route_urls as route_urls


class _Runner:
    def load_urls(self, urls):
        return len(urls), 0


def test_route_urls_returns_per_url_results_for_dashboard():
    original_cfg = route_urls._app_s_cfg
    original_runners = route_urls._app_runners
    route_urls._app_s_cfg = lambda: {
        "fk": {
            "name": "FilthyKings OPV",
            "login_url": "https://www.filthykings.com/en/login",
        }
    }
    route_urls._app_runners = lambda: {"fk": _Runner()}
    try:
        app = Flask(__name__)
        route_urls.register_routes(app)
        response = app.test_client().post(
            "/api/route_urls",
            json={
                "text": (
                    "https://www.filthykings.com/en/video/291303\n"
                    "https://example.org/x"
                )
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["results"] == [
            {
                "url": "https://www.filthykings.com/en/video/291303",
                "site_id": "fk",
                "matched": True,
            },
            {
                "url": "https://example.org/x",
                "site_id": None,
                "matched": False,
            },
        ]
        assert payload["unrouted"] == ["https://example.org/x"]
        assert payload["summary"]["fk"]["added"] == 1
    finally:
        route_urls._app_s_cfg = original_cfg
        route_urls._app_runners = original_runners


if __name__ == "__main__":
    test_route_urls_returns_per_url_results_for_dashboard()
