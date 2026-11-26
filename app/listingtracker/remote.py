"""Remote access for the API server: a self-signed TLS identity and a UPnP port mapping."""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
import random
import secrets
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.sax.saxutils import escape

from listingtracker.paths import DATA_DIR

logger = logging.getLogger(__name__)

HTTP_PORT = 8000   # home network and pairing
HTTPS_PORT = 8443  # pinned certificate; the one forwarded through the router

API_KEY_PATH = DATA_DIR / "api_key.txt"
TLS_DIR = DATA_DIR / "tls"
CERT_PATH = TLS_DIR / "server.crt"
KEY_PATH = TLS_DIR / "server.key"
STATE_PATH = DATA_DIR / "remote.json"

MAPPING_DESCRIPTION = "Durham Listing Tracker"
LEASE_SECONDS = 3600
RENEW_SECONDS = 900

_SSDP_ADDR = ("239.255.255.250", 1900)
_SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "upnp:rootdevice",
)
_WAN_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)
_CONFLICT = 718
_PERMANENT_LEASE_ONLY = 725
_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@functools.cache
def api_key() -> str:
    """The key paired devices present with every request; created on first use."""
    if API_KEY_PATH.exists():
        return API_KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    API_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_KEY_PATH.write_text(key, encoding="utf-8")
    logger.info("Generated new API key — saved to %s", API_KEY_PATH)
    return key


# ---------------------------------------------------------------------------
# TLS identity
# ---------------------------------------------------------------------------

def ensure_certificate() -> str:
    """Create the server's certificate on first run; return its SHA-256 fingerprint (hex)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    if not (CERT_PATH.exists() and KEY_PATH.exists()):
        TLS_DIR.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Durham Listing Tracker")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        KEY_PATH.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Generated TLS certificate at %s", CERT_PATH)

    cert = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex()


# ---------------------------------------------------------------------------
# Local addresses
# ---------------------------------------------------------------------------

def lan_ip() -> str:
    """This machine's address on the network that carries its default route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it only selects the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def other_home_ips() -> list[str]:
    """This machine's other home-network addresses besides lan_ip(): above all the one on the
    Mobile hotspot it may be running (192.168.137.1), where a phone on that hotspot reaches it.
    A phone tries every address at once, so one it can't use costs nothing."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    main, out = lan_ip(), []
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if (addr != main and addr not in out and ip.is_private and not ip.is_loopback
                and not ip.is_link_local and ip not in _TAILSCALE_NET):
            out.append(addr)
    return out


def tailscale_ip() -> Optional[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for info in infos:
        addr = info[4][0]
        if ipaddress.ip_address(addr) in _TAILSCALE_NET:
            return addr
    return None


def _is_public(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_global
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# UPnP Internet Gateway Device client
# ---------------------------------------------------------------------------

class UpnpError(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(f"UPnP error {code}: {description}")
        self.code = code


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Gateway:
    control_url: str
    service_type: str
    local_ip: str

    def _call(self, action: str, args: Optional[dict] = None) -> dict:
        fields = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in (args or {}).items())
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{self.service_type}">{fields}</u:{action}>'
            "</s:Body></s:Envelope>"
        )
        req = urllib.request.Request(
            self.control_url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{self.service_type}#{action}"',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            code, description = 0, e.reason
            try:
                for el in ET.fromstring(e.read()).iter():
                    if _local(el.tag) == "errorCode":
                        code = int(el.text or 0)
                    elif _local(el.tag) == "errorDescription":
                        description = el.text or description
            except (ET.ParseError, ValueError):
                pass
            raise UpnpError(code, str(description)) from None
        for el in ET.fromstring(payload).iter():
            if _local(el.tag) == f"{action}Response":
                return {_local(child.tag): (child.text or "") for child in el}
        return {}

    def external_ip(self) -> str:
        return self._call("GetExternalIPAddress").get("NewExternalIPAddress", "")

    def add_mapping(self, external_port: int, internal_port: int, lease: int) -> None:
        self._call("AddPortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": external_port,
            "NewProtocol": "TCP",
            "NewInternalPort": internal_port,
            "NewInternalClient": self.local_ip,
            "NewEnabled": 1,
            "NewPortMappingDescription": MAPPING_DESCRIPTION,
            "NewLeaseDuration": lease,
        })

    def mapping_owner(self, external_port: int) -> Optional[tuple[str, int]]:
        """Who holds `external_port`, as (internal client, internal port), or None if free."""
        try:
            r = self._call("GetSpecificPortMappingEntry", {
                "NewRemoteHost": "", "NewExternalPort": external_port, "NewProtocol": "TCP",
            })
            return r.get("NewInternalClient", ""), int(r.get("NewInternalPort") or 0)
        except (UpnpError, ValueError):
            return None

    def delete_mapping(self, external_port: int) -> None:
        self._call("DeletePortMapping", {
            "NewRemoteHost": "", "NewExternalPort": external_port, "NewProtocol": "TCP",
        })


def _gateway_from_description(location: str, local_ip: str) -> Optional[Gateway]:
    host = urllib.parse.urlsplit(location).hostname or ""
    try:
        if not ipaddress.ip_address(host).is_private:
            return None  # a gateway answers from the local network, never from outside it
    except ValueError:
        return None
    with urllib.request.urlopen(location, timeout=5) as resp:
        root = ET.fromstring(resp.read())
    base = location
    for el in root.iter():
        if _local(el.tag) == "URLBase" and el.text:
            base = el.text.strip()
            break
    services = {}
    for el in root.iter():
        if _local(el.tag) != "service":
            continue
        props = {_local(c.tag): (c.text or "").strip() for c in el}
        services[props.get("serviceType", "")] = props.get("controlURL", "")
    for service_type in _WAN_SERVICES:
        if services.get(service_type):
            control = urllib.parse.urljoin(base, services[service_type])
            return Gateway(control, service_type, local_ip)
    return None


def discover_gateway(timeout: float = 3.0) -> Optional[Gateway]:
    """Find the router's port-mapping service by SSDP, or None if it does not offer one."""
    local_ip = lan_ip()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.bind((local_ip, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for target in _SEARCH_TARGETS:
            sock.sendto((
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {_SSDP_ADDR[0]}:{_SSDP_ADDR[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {target}\r\n\r\n"
            ).encode("ascii"), _SSDP_ADDR)

        deadline = datetime.now() + timedelta(seconds=timeout)
        tried = set()
        while (remaining := (deadline - datetime.now()).total_seconds()) > 0:
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            location = ""
            for line in data.decode("utf-8", "replace").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "location":
                    location = value.strip()
            if not location or location in tried:
                continue
            tried.add(location)
            try:
                gateway = _gateway_from_description(location, local_ip)
            except (OSError, ET.ParseError, ValueError):
                continue
            if gateway:
                return gateway
        return None
    except OSError:
        return None
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Keeping the mapping alive
# ---------------------------------------------------------------------------

class RemoteAccess:
    """Holds a router port mapping for the HTTPS port and reports how the server can be reached."""

    def __init__(self, port: int, fingerprint: str, use_upnp: bool = True):
        self.port = port
        self.fingerprint = fingerprint
        self.use_upnp = use_upnp
        self.status = "starting"
        self.message = "Checking the router…"
        self.external_ip = ""
        self.external_port = 0
        self._gateway: Optional[Gateway] = None
        self._mapped_port = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.use_upnp:
            self.status, self.message = "disabled", "Automatic port forwarding is turned off."
            return
        self._thread = threading.Thread(target=self._run, name="remote-access", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        if self._gateway and self._mapped_port:
            try:
                self._gateway.delete_mapping(self._mapped_port)
                logger.info("Removed router port mapping %d", self._mapped_port)
            except (UpnpError, OSError) as e:
                logger.warning("Could not remove router port mapping: %s", e)

    def urls(self) -> list[str]:
        """HTTPS addresses for this server, most widely reachable first."""
        out = []
        if self.status == "ok":
            out.append(f"https://{self.external_ip}:{self.external_port}")
        ts = tailscale_ip()
        if ts:
            out.append(f"https://{ts}:{self.port}")
        out.append(f"https://{lan_ip()}:{self.port}")
        out += [f"https://{addr}:{self.port}" for addr in other_home_ips()]
        return out

    def _run(self) -> None:
        while True:
            try:
                self._refresh()
            except Exception as e:  # the mapping is best-effort; never take the server down
                logger.warning("Remote access check failed: %s", e)
                self._set("error", f"Could not set up port forwarding: {e}")
            if self._stop.wait(RENEW_SECONDS):
                return

    def _set(self, status: str, message: str) -> None:
        if (status, message) != (self.status, self.message):
            logger.info("Remote access: %s — %s", status, message)
        self.status, self.message = status, message

    def _refresh(self) -> None:
        gateway = discover_gateway()
        if not gateway:
            self._gateway = None
            self._set("no_upnp", (
                "Your router did not offer automatic port forwarding (UPnP). "
                f"Forward TCP port {self.port} to {lan_ip()} in the router's settings."
            ))
            return
        self._gateway = gateway

        external_ip = gateway.external_ip()
        external_port = self._map(gateway)
        self.external_ip, self.external_port = external_ip, external_port

        if _is_public(external_ip):
            self._set("ok", f"Reachable from anywhere at https://{external_ip}:{external_port}")
        else:
            self._set("cgnat", (
                f"The router's own address ({external_ip or 'unknown'}) is not a public one, "
                "so your internet provider does not allow incoming connections."
            ))

    def _map(self, gateway: Gateway) -> int:
        preferred = self._mapped_port or _load_state().get("external_port") or self.port
        candidates = [preferred] + random.sample(range(20000, 60000), 4)
        for external_port in candidates:
            owner = gateway.mapping_owner(external_port)
            if owner and owner != (gateway.local_ip, self.port):
                continue  # another device already uses this port on the router
            try:
                try:
                    gateway.add_mapping(external_port, self.port, LEASE_SECONDS)
                except UpnpError as e:
                    if e.code != _PERMANENT_LEASE_ONLY:
                        raise
                    gateway.add_mapping(external_port, self.port, 0)
            except UpnpError as e:
                if e.code == _CONFLICT:
                    continue
                raise
            if external_port != self._mapped_port:
                self._mapped_port = external_port
                _save_state({"external_port": external_port})
            return external_port
        raise UpnpError(_CONFLICT, "no free port on the router")


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
