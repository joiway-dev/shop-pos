"""Addresses other devices in the shop (tablets, other PCs) can use to reach this server."""

import ipaddress
import socket


def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this computer (works without internet)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    addresses = []
    for text in sorted(found):
        ip = ipaddress.ip_address(text)
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            addresses.append(text)
    return addresses
