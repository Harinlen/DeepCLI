"""Unit tests for domain_filter — SSRF protection."""

from __future__ import annotations

from kernel.tools.web.domain_filter import (
    add_blocked_domain,
    check_domain,
    remove_blocked_domain,
)


# ── Loopback / private / link-local / reserved ──


def test_blocks_loopback_ipv4():
    assert check_domain("http://127.0.0.1/admin") is not None


def test_blocks_loopback_ipv6():
    assert check_domain("http://[::1]/admin") is not None


def test_blocks_link_local():
    assert check_domain("http://169.254.169.254/latest/meta-data/") is not None


def test_blocks_private_10():
    assert check_domain("http://10.0.0.1/internal") is not None


def test_blocks_private_192():
    assert check_domain("http://192.168.1.1/") is not None


def test_blocks_private_172():
    assert check_domain("http://172.16.0.1/") is not None


def test_blocks_localhost():
    assert check_domain("http://localhost:8080/") is not None


# ── Scheme ──


def test_blocks_ftp():
    assert check_domain("ftp://example.com/file") is not None


def test_blocks_file():
    assert check_domain("file:///etc/passwd") is not None


def test_allows_http():
    assert check_domain("http://example.com/") is None


def test_allows_https():
    assert check_domain("https://docs.python.org/3/") is None


# ── Embedded credentials ──


def test_blocks_embedded_credentials():
    assert check_domain("http://user:pass@example.com/") is not None


def test_blocks_embedded_user_only():
    assert check_domain("http://admin@example.com/") is not None


# ── Embedded API key ──


def test_blocks_embedded_api_key_query():
    assert check_domain("https://example.com/?api_key=sk-abc123456789") is not None


def test_blocks_encoded_api_key():
    # sk- encoded as %73%6b-
    assert check_domain("https://example.com/?key=%73%6b-abc123456789") is not None


def test_allows_short_params():
    """Short query params should not trigger the API key regex."""
    assert check_domain("https://example.com/?q=hello") is None


# ── Operator blocklist ──


def test_operator_blocklist_add_remove():
    add_blocked_domain("evil.com")
    assert check_domain("http://evil.com/") is not None
    remove_blocked_domain("evil.com")
    assert check_domain("http://evil.com/") is None


def test_operator_blocklist_case_insensitive():
    add_blocked_domain("EVIL.COM")
    assert check_domain("http://evil.com/") is not None
    remove_blocked_domain("evil.com")


# ── Public domains allowed ──


def test_allows_public_domain():
    assert check_domain("https://docs.python.org/3/") is None


def test_allows_public_ip():
    assert check_domain("http://8.8.8.8/") is None


def test_allows_github():
    assert check_domain("https://github.com/anthropics/claude-code") is None
