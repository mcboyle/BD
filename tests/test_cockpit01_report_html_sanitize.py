"""F-COCKPIT01-03 -- cockpit report viewer sinks unsanitized python-markdown
HTML into innerHTML (stored-DOM-XSS).

The /cockpit report endpoint renders a .md report via _md.markdown() (which
passes raw inline HTML through) and the client does rv.innerHTML = d.html with
no sanitization -- so a report containing <script>/onerror=/javascript: links
executes in the operator's cockpit. The fix runs the markdown output through a
dependency-free allowlist sanitizer (_sanitize_report_html) server-side before
returning it: only markdown's own safe tags/attrs survive; script/style/iframe,
on* handlers, and javascript:/data: URLs are dropped.
"""
import os
import tempfile
import inspect

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def test_sanitizer_strips_script_and_handlers():
    from tools.cockpit_console import _sanitize_report_html as san
    assert "<script" not in san("<p>ok</p><script>alert(1)</script>").lower()
    assert "onerror" not in san('<img src=x onerror="alert(1)">').lower()
    assert "<iframe" not in san('<iframe src="http://evil"></iframe>').lower()


def test_sanitizer_drops_dangerous_urls():
    from tools.cockpit_console import _sanitize_report_html as san
    assert "javascript:" not in san('<a href="javascript:alert(1)">x</a>').lower()
    assert "data:text/html" not in san('<a href="data:text/html,<script>">x</a>').lower()


def test_sanitizer_preserves_safe_markup():
    from tools.cockpit_console import _sanitize_report_html as san
    out = san("<h1>Title</h1><p><strong>bold</strong> and <em>i</em></p>"
              "<ul><li>a</li></ul><a href=\"https://ok/\">link</a>")
    for frag in ("<h1>", "<strong>", "<em>", "<li>", 'href="https://ok/"'):
        assert frag in out, f"safe markup dropped: {frag} -> {out}"


def test_render_routes_markdown_through_sanitizer():
    import tools.cockpit_console as cc
    src = inspect.getsource(cc)
    assert "_md.markdown(" in src
    assert "_sanitize_report_html(" in src, "report render must sanitize markdown output"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
