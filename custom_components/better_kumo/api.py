"""Client for the Better Kumo relay HTTP API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)


class RelayError(Exception):
    """The relay could not be reached or refused the request."""


class RelayAuthError(RelayError):
    """The relay rejected the secret."""


def parse_pairing_link(link: str) -> tuple[str, str]:
    """Return (url, secret) from a kumowatch://relay?url=…&secret=… link."""
    parts = urlsplit(link.strip())
    if parts.scheme != "kumowatch" or parts.netloc != "relay":
        raise ValueError("not a pairing link")
    query = parse_qs(parts.query)
    url = query.get("url", [""])[0].strip()
    secret = query.get("secret", [""])[0].strip()
    if not url or not secret:
        raise ValueError("pairing link without url or secret")
    return normalize_url(url), secret


def normalize_url(url: str) -> str:
    """Return the relay base URL without a trailing slash; reject non-HTTP URLs."""
    url = url.strip().rstrip("/")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ValueError("relay URL must be http(s)://host:port")
    return url


class RelayClient:
    """Thin async wrapper over the relay's /v1 endpoints."""

    def __init__(self, session: aiohttp.ClientSession, url: str, secret: str) -> None:
        self._session = session
        self._url = url
        self._headers = {"Authorization": f"Bearer {secret}"}

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._url}{path}",
                json=body,
                headers=self._headers,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise RelayAuthError("wrong relay secret")
                text = await resp.text()
                if resp.status >= 400:
                    raise RelayError(f"relay returned {resp.status}: {text.strip()}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise RelayError(f"relay not reachable: {err}") from err
        try:
            return json.loads(text) if text.strip() else None
        except ValueError as err:
            raise RelayError(f"unexpected reply from the relay: {err}") from err

    async def status(self) -> dict[str, Any]:
        """GET /v1/status."""
        return await self._request("GET", "/v1/status")

    async def desktop(self) -> dict[str, Any]:
        """GET /v1/desktop."""
        return await self._request("GET", "/v1/desktop")

    async def set_availability(self, availability: str, message: str) -> dict[str, Any]:
        """POST /v1/desktop/status."""
        return await self._request(
            "POST",
            "/v1/desktop/status",
            {"availability": availability, "message": message},
        )

    async def set_media(self, **media: bool) -> dict[str, Any]:
        """POST /v1/desktop/media with audio= and/or video=."""
        return await self._request("POST", "/v1/desktop/media", media)

    async def put_webhook(self, hook_id: str, urls: list[str], secret: str) -> None:
        """PUT /v1/webhooks/{id}."""
        await self._request("PUT", f"/v1/webhooks/{hook_id}", {"urls": urls, "secret": secret})

    async def delete_webhook(self, hook_id: str) -> None:
        """DELETE /v1/webhooks/{id}."""
        await self._request("DELETE", f"/v1/webhooks/{hook_id}")
