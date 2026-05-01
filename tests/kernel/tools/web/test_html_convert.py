"""Unit tests for html_convert — HTML to Markdown conversion."""

from __future__ import annotations

from kernel.tools.web.html_convert import html_to_markdown


def test_basic_heading():
    md = html_to_markdown("<h1>Title</h1><p>Hello world</p>")
    assert "Title" in md
    assert "Hello" in md


def test_preserves_links():
    md = html_to_markdown('<a href="https://example.com">Click</a>')
    assert "example.com" in md
    assert "Click" in md


def test_strips_script_tags():
    html = "<p>Safe</p><script>alert('xss')</script><p>Also safe</p>"
    md = html_to_markdown(html)
    assert "alert" not in md
    assert "Safe" in md
    assert "Also safe" in md


def test_strips_base64_images():
    html = '<p>Text</p><img src="data:image/png;base64,iVBOR...">'
    md = html_to_markdown(html)
    assert "data:image" not in md
    assert "iVBOR" not in md


def test_strips_base64_in_markdown_image():
    # html2text may produce ![alt](data:image/...) — we strip it
    html = '<img alt="pic" src="data:image/png;base64,AAAA">'
    md = html_to_markdown(html)
    assert "data:image" not in md


def test_truncation():
    html = "<p>" + "x" * 10_000 + "</p>"
    md = html_to_markdown(html, max_chars=100)
    assert len(md) <= 100


def test_empty_html():
    md = html_to_markdown("")
    assert isinstance(md, str)


def test_nested_html():
    html = "<div><ul><li>One</li><li>Two</li></ul></div>"
    md = html_to_markdown(html)
    assert "One" in md
    assert "Two" in md
