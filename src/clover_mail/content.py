"""Extract text and selected image metadata from an RFC822 message.

Parsing never fetches a URL. Remote candidates need an explicit runtime mode.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGES = 3
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
DECORATIVE_NAMES = re.compile(r"(?:logo|icon|avatar|signature|social|spacer|pixel|tracking|button|footer)", re.I)
INFORMATIVE_NAMES = re.compile(r"(?:poster|flyer|event|schedule|agenda|competition|deadline|registration|海报|活动|日程|报名)", re.I)
REMOTE_IMAGE_LIMIT = 2


@dataclass(frozen=True)
class LocalImage:
    media_type: str
    filename: str
    data: bytes
    width: int | None
    height: int | None


@dataclass(frozen=True)
class RemoteImageCandidate:
    url: str
    label: str
    score: int


@dataclass(frozen=True)
class ExtractedContent:
    sender: str
    subject: str
    date: str
    text: str
    images: tuple[LocalImage, ...]
    remote_image_count: int
    remote_candidates: tuple[RemoteImageCandidate, ...] = ()


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.remote_images = 0
        self.remote_candidates: list[RemoteImageCandidate] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section", "article"}:
            self.parts.append("\n")
        if tag == "img":
            fields = dict(attrs)
            src = fields.get("src") or ""
            if src.lower().startswith(("http://", "https://")):
                self.remote_images += 1
                label = " ".join((fields.get("alt") or "", fields.get("title") or "", src.split("?", 1)[0].rsplit("/", 1)[-1]))
                width = int(fields["width"]) if (fields.get("width") or "").isdigit() else None
                height = int(fields["height"]) if (fields.get("height") or "").isdigit() else None
                if (src.lower().startswith("https://") and INFORMATIVE_NAMES.search(label)
                        and not DECORATIVE_NAMES.search(label)
                        and not (width is not None and width < 240)
                        and not (height is not None and height < 160)):
                    score = 2 + bool(INFORMATIVE_NAMES.search(fields.get("alt") or ""))
                    self.remote_candidates.append(RemoteImageCandidate(src, label[:200], score))
            alt = (fields.get("alt") or "").strip()
            if alt and not DECORATIVE_NAMES.search(alt):
                self.parts.append(f" [{alt}] ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in {"p", "div", "li", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", joined)).strip()


def _dimensions(data: bytes, media_type: str) -> tuple[int | None, int | None]:
    if media_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if media_type == "image/gif" and data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if media_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            pos += 2
            if marker in {0xD8, 0xD9}:
                continue
            if pos + 2 > len(data):
                break
            size = int.from_bytes(data[pos:pos + 2], "big")
            if size < 2 or pos + size > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3} and size >= 7:
                return int.from_bytes(data[pos + 3:pos + 5], "big"), int.from_bytes(data[pos + 5:pos + 7], "big")
            pos += size
    return None, None


def extract_content(raw_message: bytes) -> ExtractedContent:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    plain: list[str] = []
    html: list[str] = []
    candidates: list[tuple[int, LocalImage]] = []
    seen_images: set[str] = set()
    remote_count = 0
    remote_candidates: list[RemoteImageCandidate] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        media_type = part.get_content_type().lower()
        if media_type in {"text/plain", "text/html"} and part.get_content_disposition() != "attachment":
            try:
                decoded = part.get_content()
            except (LookupError, UnicodeError, ValueError, TypeError):
                continue
            if not isinstance(decoded, str):
                continue
            if media_type == "text/plain":
                plain.append(decoded.strip())
            else:
                parser = _HTMLText()
                parser.feed(decoded)
                html.append(parser.text())
                remote_count += parser.remote_images
                remote_candidates.extend(parser.remote_candidates)
            continue
        if media_type not in IMAGE_TYPES:
            continue
        name = part.get_filename() or ""
        if DECORATIVE_NAMES.search(name):
            continue
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes) or not 8_192 <= len(data) <= MAX_IMAGE_BYTES:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_images:
            continue
        seen_images.add(digest)
        width, height = _dimensions(data, media_type)
        if width is not None and height is not None and (width < 240 or height < 160):
            continue
        if width is None and len(data) < 20_000:
            continue
        score = (3 if INFORMATIVE_NAMES.search(name) else 0) + (1 if part.get_content_disposition() == "attachment" else 0)
        score += min((width or 0) * (height or 0) // 500_000, 3)
        candidates.append((score, LocalImage(media_type, name, data, width, height)))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected: list[LocalImage] = []
    selected_bytes = 0
    for _, image in candidates:
        if len(selected) >= MAX_IMAGES:
            break
        if selected_bytes + len(image.data) <= MAX_IMAGE_BYTES:
            selected.append(image)
            selected_bytes += len(image.data)
    # MIME alternatives represent the same content. Newsletter plain parts are
    # often short placeholders, while HTML contains the actual information.
    plain_text = "\n\n".join(piece for piece in plain if piece)
    html_text = "\n\n".join(piece for piece in html if piece)
    text = html_text if len(html_text) > len(plain_text) * 1.5 else (plain_text or html_text)
    return ExtractedContent(
        sender=str(message.get("From", "")),
        subject=str(message.get("Subject", "")),
        date=str(message.get("Date", "")),
        text=text,
        images=tuple(selected),
        remote_image_count=remote_count,
        remote_candidates=tuple(sorted({candidate.url: candidate for candidate in remote_candidates}.values(),
                                       key=lambda candidate: candidate.score, reverse=True)[:REMOTE_IMAGE_LIMIT]),
    )
