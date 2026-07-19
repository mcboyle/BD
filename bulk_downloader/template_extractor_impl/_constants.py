"""template_extractor_impl._constants -- 7 scoring/prompt/login consts (verbatim)."""

import re as _re_login  # _LOGIN_SUBMIT_TEXT is a compiled regex

_CANDIDATE_TAGS = ("a", "button", "video", "source")
_CANDIDATE_ATTRS = ("data-href", "data-url", "data-src",
                      "data-download", "data-video")
MIN_SCORE_FOR_CANDIDATE = 0
MIN_SCORE_FOR_ROW = 25
MIN_SCORE_FOR_TEMPLATE = MIN_SCORE_FOR_ROW
REFINE_PROMPT = """You are refining a draft site template for an automated
downloader. The rule-based engine produced this draft:

CURRENT TEMPLATE:
{current_template}

TOP CANDIDATES (with scores):
{candidates_summary}

ORIGINAL HTML (excerpt):
{html_excerpt}

Your job:
1. Review the row_selectors — are they specific enough? Too brittle?
2. Suggest any trigger_selectors the rule-based engine missed.
3. Flag false positives (selectors that match navigation, ads, etc.).
4. If the site needs a special url_attribute (data-src, data-href),
   confirm or correct the current pick.

Return JSON only:
{{
  "refined_row_selectors": ["...", "..."],
  "refined_trigger_selectors": ["..."],
  "url_attribute": "href" or "data-...",
  "warnings": ["..."],
  "confidence": 0-100
}}"""
_LOGIN_SUBMIT_TEXT = _re_login.compile(
    r"\b(log\s?in|sign\s?in|get\s?inside|submit|continue|enter)\b",
    _re_login.I)
