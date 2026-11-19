# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal link preview fetcher
## Date Created: 2026-11-17
## Description: Builds a Signal link preview (title, description, picture) for a link the user sends.
"""Link previews for links the user sends, like Signal's "Generate link previews".

Only runs when the ``link_previews`` setting is on. The page is fetched from
this computer, the way Signal's own clients do it, so everything here is
bounded:

* ``https`` only, on the default port, and every address the host name
  resolves to must be public: no loopback, private, link-local, multicast or
  reserved ranges, so a link can never make the bridge probe the local network.
  The connection goes to the address that was checked, not to a second lookup.
* At most three redirects, each checked the same way.
* A short timeout for the page, and the rest of a fixed budget for the picture
  (some sites, GitHub among them, draw the picture on the first request), and a size cap on what is read: 512 KiB of HTML,
  2 MiB for the picture, which must really be a PNG, JPEG, GIF or WebP.
* Nothing from the page is trusted: title and description go through
  :func:`clean_text`, and only the first ``og:image`` (or ``twitter:image``)
  is fetched.

Any failure returns ``None`` and the message is sent without a preview.
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import os
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .links import find_urls
from .sanitize import clean_name, clean_text

TIMEOUT_S = 4.0       # for the page
BUDGET_S = 7.5        # page and picture together; the picture gets whatever the page left
MAX_HTML = 512 * 1024
MAX_IMAGE = 2 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) omarchy-signal link preview"

IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


@dataclass
class LinkPreview:
    url: str
    title: str
    description: str = ""
    image_path: str = ""          # a private temporary file; the caller removes it
    image_type: str = ""


class PreviewError(Exception):
    pass


def first_link(text: str) -> str:
    """The first ``https`` link in ``text`` (Signal previews only that one)."""
    for _, _, url in find_urls(text or ""):
        if url.lower().startswith("https://"):
            return url
    return ""


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


def _resolve(host: str) -> str:
    """One public address for ``host``; refuse it if any address it has is not public."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PreviewError(f"cannot resolve {host}") from exc
    ips = [info[4][0] for info in infos]
    if not ips or not all(_public(ip) for ip in ips):
        raise PreviewError(f"{host} is not a public address")
    return ips[0]


def _check_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise PreviewError("only https links get a preview")
    if parts.port not in (None, 443) or parts.username or parts.password:
        raise PreviewError("unexpected port or credentials in link")
    host = parts.hostname.encode("idna").decode("ascii") if not parts.hostname.isascii() else parts.hostname
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, path


class _PinnedHTTPS(http.client.HTTPSConnection):
    """An HTTPS connection to an address that was already checked, verifying
    the certificate for the host name."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get(url: str, accept: str, limit: int, opener=None, timeout: float = TIMEOUT_S) -> tuple[str, str, bytes]:
    """GET ``url`` following checked redirects. Returns (final url, content type, body)."""
    for _ in range(MAX_REDIRECTS + 1):
        if timeout <= 0:
            raise PreviewError("out of time")
        host, path = _check_url(url)
        conn = opener(host) if opener else _PinnedHTTPS(host, _resolve(host), timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                               "Accept-Encoding": "identity"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location") or ""
                if not location:
                    raise PreviewError("redirect without a location")
                url = urljoin(url, location)
                continue
            if resp.status != 200:
                raise PreviewError(f"HTTP {resp.status}")
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > limit and not ctype.startswith("text/html"):
                raise PreviewError("too large")
            body = resp.read(limit + 1)
            return url, ctype, body[:limit] if ctype.startswith("text/html") else body
        finally:
            conn.close()
    raise PreviewError("too many redirects")


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        a = {k.lower(): (v or "") for k, v in attrs if k}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "title":
            self._in_title = True
        elif tag == "body":
            self._done = True       # everything a preview needs lives in <head>

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 1000:
            self.title += data


def parse_page(page: str) -> tuple[str, str, str]:
    """(title, description, image URL) from a page's ``<head>``."""
    p = _MetaParser()
    try:
        p.feed(page)
        p.close()
    except Exception:  # noqa: BLE001 - a broken page just has less metadata
        pass
    m = p.meta
    title = m.get("og:title") or m.get("twitter:title") or p.title
    desc = m.get("og:description") or m.get("twitter:description") or m.get("description") or ""
    image = m.get("og:image") or m.get("og:image:url") or m.get("og:image:secure_url") or m.get("twitter:image") or ""
    clean = lambda s, n: clean_text(html.unescape(s), max_length=n, single_line=True).strip()  # noqa: E731
    return clean(title, 300), clean(desc, 500), image.strip()


def _image_kind(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _save_image(data: bytes, directory: Path) -> tuple[str, str]:
    kind = _image_kind(data)
    if not kind or len(data) > MAX_IMAGE:
        return "", ""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, name = tempfile.mkstemp(prefix="preview-", suffix=IMAGE_TYPES[kind], dir=directory)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(name, 0o600)
    return name, kind


def fetch(url: str, image_dir: Path, *, opener=None, budget: float = BUDGET_S) -> LinkPreview | None:
    """Build the preview for ``url``, or ``None``. Blocking: run it in a thread.

    ``opener(host)`` returns an HTTP connection; tests pass one that talks to a
    local server. Without it every address is checked and pinned. ``budget``
    bounds the whole thing in seconds: the page gets at most :data:`TIMEOUT_S`
    of it and the picture whatever is left, so a slow picture costs the
    preview its picture, never its title."""
    deadline = time.monotonic() + budget
    try:
        final, ctype, body = _get(url, "text/html,image/*;q=0.9,*/*;q=0.1", MAX_IMAGE, opener,
                                  timeout=min(TIMEOUT_S, budget))
        host = urlsplit(final).hostname or ""
        if ctype in IMAGE_TYPES:
            # A link straight to a picture: the picture is the preview.
            path, kind = _save_image(body, image_dir)
            if not path:
                return None
            name = clean_name(os.path.basename(urlsplit(final).path), max_length=120) or host
            return LinkPreview(url=url, title=name, description=host, image_path=path, image_type=kind)
        if ctype not in ("text/html", "application/xhtml+xml"):
            return None
        title, desc, image = parse_page(body[:MAX_HTML].decode("utf-8", "replace"))
        if not title:
            return None
        preview = LinkPreview(url=url, title=title, description=desc)
        if image:
            try:
                left = deadline - time.monotonic()
                _, ictype, idata = _get(urljoin(final, image), "image/*", MAX_IMAGE, opener, timeout=left)
                if ictype in IMAGE_TYPES or _image_kind(idata):
                    preview.image_path, preview.image_type = _save_image(idata, image_dir)
            except (PreviewError, OSError, http.client.HTTPException, ValueError):
                pass
        return preview
    except (PreviewError, OSError, http.client.HTTPException, ValueError, UnicodeError):
        return None
