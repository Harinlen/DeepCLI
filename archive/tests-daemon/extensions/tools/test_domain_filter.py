"""Tests for the domain security filter (Phase 5 hardening)."""

from __future__ import annotations

from daemon.extensions.tools.domain_filter import (
    add_blocked_domain,
    check_domain,
    get_blocked_domains,
    remove_blocked_domain,
)


class TestCheckDomain:
    def test_normal_url_allowed(self) -> None:
        assert check_domain("https://example.com/page") is None

    def test_loopback_ipv4_rejected(self) -> None:
        err = check_domain("http://127.0.0.1:8080/api")
        assert err is not None
        assert "loopback" in err

    def test_loopback_ipv6_rejected(self) -> None:
        err = check_domain("http://[::1]/api")
        assert err is not None
        assert "loopback" in err

    def test_link_local_rejected(self) -> None:
        # AWS metadata endpoint
        err = check_domain("http://169.254.169.254/latest/meta-data/")
        assert err is not None
        assert "link-local" in err

    def test_private_10x_rejected(self) -> None:
        err = check_domain("http://10.0.0.1/internal")
        assert err is not None
        assert "private" in err

    def test_private_192_rejected(self) -> None:
        err = check_domain("http://192.168.1.1/admin")
        assert err is not None
        assert "private" in err

    def test_localhost_hostname_rejected(self) -> None:
        err = check_domain("http://localhost:3000/api")
        assert err is not None
        assert "localhost" in err

    def test_empty_host_rejected(self) -> None:
        err = check_domain("http:///path")
        assert err is not None

    def test_public_ip_allowed(self) -> None:
        assert check_domain("http://8.8.8.8/dns") is None


class TestBlocklist:
    def test_add_and_check(self) -> None:
        add_blocked_domain("evil.example.com")
        err = check_domain("https://evil.example.com/phish")
        assert err is not None
        assert "blocked" in err
        remove_blocked_domain("evil.example.com")

    def test_case_insensitive(self) -> None:
        add_blocked_domain("EVIL.COM")
        err = check_domain("https://evil.com/x")
        assert err is not None
        remove_blocked_domain("evil.com")

    def test_remove_unblocks(self) -> None:
        add_blocked_domain("temp.com")
        remove_blocked_domain("temp.com")
        assert check_domain("https://temp.com") is None

    def test_get_blocked_domains_snapshot(self) -> None:
        add_blocked_domain("snap.test")
        snapshot = get_blocked_domains()
        assert "snap.test" in snapshot
        remove_blocked_domain("snap.test")

    def test_remove_absent_is_noop(self) -> None:
        remove_blocked_domain("never-added.example.com")  # no error
