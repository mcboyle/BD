import json
import tempfile
from pathlib import Path

from bulk_downloader.template_registry import (
    find_template_for_url,
    find_template_variants_for_url,
    score_template_against_html,
)


def _write_template(directory, filename, *, host, match=None, selectors=None):
    path = Path(directory) / filename
    path.write_text(
        json.dumps(
            {
                "host": host,
                "status": "enabled",
                "match": match or {},
                "selectors": selectors or {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explicit_alias_matches_but_unlisted_sibling_does_not():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "site.template.json",
        host="www.example.com",
        match={"hosts": ["www.example.com", "members.example.com"]},
    )

    matched = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )
    rejected = find_template_for_url(
        "https://cdn.example.com/video/1", template_dirs=[directory]
    )

    assert matched is not None
    assert matched["host"] == "www.example.com"
    assert rejected is None


def test_valid_sibling_domain_matches_domain_family():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "family.template.json",
        host="www.example.com",
        match={"sibling_domain": "example.com"},
    )

    matched = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )

    assert matched is not None
    assert matched["host"] == "www.example.com"


def test_invalid_sibling_domains_fail_closed():
    invalid_values = (
        "other.example",
        "localhost",
        "127.0.0.1",
        "https://example.com",
        "-bad.example.com",
        123,
    )
    for index, sibling_domain in enumerate(invalid_values):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"invalid-{index}.template.json",
            host="www.example.com",
            match={"sibling_domain": sibling_domain},
        )
        assert find_template_for_url(
            "https://members.example.com/video/1",
            template_dirs=[directory],
        ) is None


def test_canonical_host_does_not_match_url_host_with_trailing_dot():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "canonical.template.json",
        host="example.com",
    )

    assert find_template_for_url(
        "https://example.com./video/1",
        template_dirs=[directory],
    ) is None


def test_sibling_domain_with_multiple_trailing_dots_fails_closed():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "multiple-dots.template.json",
        host="www.example.com",
        match={"sibling_domain": "example.com.."},
    )

    assert find_template_for_url(
        "https://members.example.com/video/1",
        template_dirs=[directory],
    ) is None


def test_domains_below_localhost_fail_closed():
    for index, sibling_domain in enumerate(("api.localhost", "deep.api.localhost")):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"localhost-{index}.template.json",
            host=f"www.{sibling_domain}",
            match={"sibling_domain": sibling_domain},
        )
        assert find_template_for_url(
            f"https://members.{sibling_domain}/video/1",
            template_dirs=[directory],
        ) is None


def test_dotted_all_numeric_sibling_domains_fail_closed():
    for index, sibling_domain in enumerate(("127.1", "0.0.1")):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"numeric-{index}.template.json",
            host=f"www.{sibling_domain}",
            match={"sibling_domain": sibling_domain},
        )
        assert find_template_for_url(
            f"https://members.{sibling_domain}/video/1",
            template_dirs=[directory],
        ) is None


def test_non_list_and_non_string_alias_metadata_fails_closed():
    bad_values = (
        "members.example.com",
        {"members.example.com": True},
        [123, None],
    )
    for index, hosts in enumerate(bad_values):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"bad-alias-{index}.template.json",
            host="www.example.com",
            match={"hosts": hosts},
        )
        assert find_template_for_url(
            "https://members.example.com/video/1",
            template_dirs=[directory],
        ) is None


def test_match_priority_is_canonical_then_alias_then_child_then_sibling():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "00-sibling.template.json",
        host="www.example.com",
        match={"sibling_domain": "example.com"},
    )
    _write_template(
        directory,
        "10-child.template.json",
        host="members.example.com",
    )
    _write_template(
        directory,
        "20-alias.template.json",
        host="app.other.test",
        match={"hosts": ["deep.members.example.com", "alias-only.members.example.com"]},
    )
    _write_template(
        directory,
        "30-exact.template.json",
        host="deep.members.example.com",
    )

    exact = find_template_for_url(
        "https://deep.members.example.com/video/1",
        template_dirs=[directory],
    )
    alias = find_template_for_url(
        "https://alias-only.members.example.com/video/1",
        template_dirs=[directory],
    )
    child = find_template_for_url(
        "https://child.members.example.com/video/1",
        template_dirs=[directory],
    )

    assert exact is not None
    assert exact["host"] == "deep.members.example.com"
    assert alias is not None
    assert alias["host"] == "app.other.test"
    assert child is not None
    assert child["host"] == "members.example.com"


def test_variant_discovery_uses_same_alias_rules_as_primary_lookup():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "alias.template.json",
        host="www.example.com",
        match={"hosts": ["members.example.com"]},
    )

    primary = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )
    variants = find_template_variants_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )

    assert primary is not None
    assert [template["host"] for template in variants] == [primary["host"]]


def test_html_scoring_cannot_override_more_specific_host_match():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "exact.template.json",
        host="deep.members.example.com",
        selectors={"download": {"btn": "button.exact-layout"}},
    )
    _write_template(
        directory,
        "broader.template.json",
        host="members.example.com",
        selectors={"download": {"btn": "button.broader-layout"}},
    )

    url = "https://deep.members.example.com/video/1"
    html = '<button class="broader-layout">download</button>'
    variants = find_template_variants_for_url(url, template_dirs=[directory])
    assert [template["host"] for template in variants] == [
        "deep.members.example.com",
        "members.example.com",
    ]
    assert score_template_against_html(variants[0], html) == 0.0
    assert score_template_against_html(variants[1], html) == 1.0

    matched = find_template_for_url(
        url,
        template_dirs=[directory],
        html=html,
    )

    assert matched is not None
    assert matched["host"] == "deep.members.example.com"


if __name__ == "__main__":
    test_explicit_alias_matches_but_unlisted_sibling_does_not()
    test_valid_sibling_domain_matches_domain_family()
    test_invalid_sibling_domains_fail_closed()
    test_canonical_host_does_not_match_url_host_with_trailing_dot()
    test_sibling_domain_with_multiple_trailing_dots_fails_closed()
    test_domains_below_localhost_fail_closed()
    test_dotted_all_numeric_sibling_domains_fail_closed()
    test_non_list_and_non_string_alias_metadata_fails_closed()
    test_match_priority_is_canonical_then_alias_then_child_then_sibling()
    test_variant_discovery_uses_same_alias_rules_as_primary_lookup()
    test_html_scoring_cannot_override_more_specific_host_match()
