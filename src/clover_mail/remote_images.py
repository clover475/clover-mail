"""Optional, tightly bounded remote Newsletter image retrieval.

Disabled unless CLOVER_MAIL_REMOTE_IMAGES=selective. No cookies, Referer,
redirects, or private-network destinations; URLs can still contain tracking IDs.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

from .content import LocalImage, RemoteImageCandidate, _dimensions

REMOTE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PER_IMAGE_BYTES = 3 * 1024 * 1024


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, *, timeout: int = 15) -> None:
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        plain = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(plain, server_hostname=self.host)
        except Exception:
            plain.close()
            raise


def _public_address(host: str) -> str | None:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None
    parsed = []
    for entry in addresses:
        try:
            address = ipaddress.ip_address(entry[4][0])
        except ValueError:
            return None
        if not address.is_global:
            return None
        parsed.append(str(address))
    return parsed[0] if parsed else None


def _download(candidate: RemoteImageCandidate, max_bytes: int) -> LocalImage | None:
    if len(candidate.url) > 2048:
        return None
    try:
        parts = urlsplit(candidate.url)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or port not in (None, 443)):
        return None
    address = _public_address(parts.hostname)
    if address is None:
        return None
    connection = _PinnedHTTPSConnection(parts.hostname, address)
    try:
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        connection.request("GET", path, headers={
            "Accept": ", ".join(REMOTE_TYPES),
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = connection.getresponse()
        media_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if response.status != 200 or media_type not in REMOTE_TYPES:
            return None
        if int(response.getheader("Content-Length") or "0") > max_bytes:
            return None
        data = response.read(max_bytes + 1)
        if not 8_192 <= len(data) <= max_bytes:
            return None
        if ((media_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"))
                or (media_type == "image/jpeg" and not data.startswith(b"\xff\xd8"))
                or (media_type == "image/webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))):
            return None
        width, height = _dimensions(data, media_type)
        if width is not None and height is not None and (width < 240 or height < 160):
            return None
        digest = hashlib.sha256(data).hexdigest()
        return LocalImage(media_type, f"remote-{digest[:12]}.{REMOTE_TYPES[media_type]}", data, width, height)
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def fetch_selected(candidates: tuple[RemoteImageCandidate, ...], *, max_count: int,
                   max_bytes: int) -> tuple[LocalImage, ...]:
    images: list[LocalImage] = []
    digests: set[str] = set()
    remaining = min(max_bytes, PER_IMAGE_BYTES * max_count)
    for candidate in candidates:
        if len(images) >= max_count or remaining < 8_192:
            break
        image = _download(candidate, min(remaining, PER_IMAGE_BYTES))
        if image is None:
            continue
        digest = hashlib.sha256(image.data).hexdigest()
        if digest in digests:
            continue
        digests.add(digest)
        images.append(image)
        remaining -= len(image.data)
    return tuple(images)
