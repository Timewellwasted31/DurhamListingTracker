import pytest

from listingtracker import remote


def test_certificate_is_created_once_and_fingerprint_is_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "TLS_DIR", tmp_path)
    monkeypatch.setattr(remote, "CERT_PATH", tmp_path / "server.crt")
    monkeypatch.setattr(remote, "KEY_PATH", tmp_path / "server.key")

    first = remote.ensure_certificate()
    assert len(first) == 64 and int(first, 16) >= 0
    assert remote.ensure_certificate() == first


@pytest.mark.parametrize("addr,public", [
    ("8.8.4.4", True),
    ("192.168.2.1", False),
    ("10.0.0.5", False),
    ("100.72.3.9", False),  # carrier-grade NAT range: the ISP shares one address among customers
    ("", False),
])
def test_only_a_public_router_address_counts_as_reachable(addr, public):
    assert remote._is_public(addr) is public


def test_urls_list_the_public_address_first_only_when_the_mapping_works(monkeypatch):
    monkeypatch.setattr(remote, "lan_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(remote, "tailscale_ip", lambda: None)
    monkeypatch.setattr(remote, "hotspot_ip", lambda: None)
    access = remote.RemoteAccess(8443, "ab" * 32)
    assert access.urls() == ["https://192.168.1.20:8443"]

    access.status, access.external_ip, access.external_port = "ok", "203.0.113.7", 8443
    assert access.urls() == ["https://203.0.113.7:8443", "https://192.168.1.20:8443"]


def test_pair_info_is_refused_for_callers_on_other_machines(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from listingtracker import api

    monkeypatch.setattr(api, "api_key", lambda: "secret")

    def ask_from(host):
        return api.pair_info(SimpleNamespace(client=SimpleNamespace(host=host)))

    assert ask_from("127.0.0.1")["key"] == "secret"
    for outsider in ("192.168.2.50", "100.101.102.103", "203.0.113.9"):
        with pytest.raises(HTTPException) as refused:
            ask_from(outsider)
        assert refused.value.status_code == 403


def test_the_hotspot_address_is_offered_too_but_not_virtual_adapters(monkeypatch):
    monkeypatch.setattr(remote, "lan_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(remote, "tailscale_ip", lambda: None)
    monkeypatch.setattr(remote.socket, "getaddrinfo", lambda *a, **k: [
        (None, None, None, None, (addr, 0)) for addr in
        ("192.168.1.20", "192.168.56.1", "172.25.80.1", "192.168.137.1", "100.70.1.2", "25.1.2.3",
         "169.254.9.9", "127.0.0.1")])
    assert remote.hotspot_ip() == "192.168.137.1"
    access = remote.RemoteAccess(8443, "ab" * 32)
    assert access.urls() == ["https://192.168.1.20:8443", "https://192.168.137.1:8443"]
