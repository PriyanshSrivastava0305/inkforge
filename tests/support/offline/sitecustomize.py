"""Network guard for offline acceptance runs (docs/11 § Offline, § Network).

Loaded automatically by Python when this folder is on PYTHONPATH. Any attempt to connect to a
non-loopback address raises and is appended to $INKFORGE_NET_LOG, so a test can assert the app never
tried to reach the network. A real airplane-mode run is still the release gate; this makes the
check repeatable on every machine.
"""

import ipaddress
import os
import socket

_LOG = os.environ.get("INKFORGE_NET_LOG")
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _loopback(host) -> bool:
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _blocked(what: str) -> None:
    if _LOG:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(what + "\n")
    raise OSError(f"[inkforge offline guard] blocked non-loopback network access: {what}")


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(host):
        _blocked(f"connect {address}")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(host):
        _blocked(f"connect_ex {address}")
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if isinstance(host, bytes):
        host = host.decode()
    if host and not _loopback(host) and host != "localhost":
        _blocked(f"dns {host}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo
