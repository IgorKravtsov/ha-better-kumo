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
    return normalize_url(url), check_secret(secret)


def check_secret(secret: str) -> str:
    """Reject secrets that cannot go into an HTTP header."""
    secret = secret.strip()
    if not secret or any(ord(c) < 0x20 or ord(c) == 0x7F for c in secret):
        raise ValueError("invalid secret")
    return secret


def normalize_url(url: str) -> str:
    """Return the relay base URL without a trailing slash; reject non-HTTP URLs."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    # Endpoint paths are appended to this string, so a query or fragment would swallow them.
    # Reading .port raises ValueError for a non-numeric or out-of-range port.
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or "?" in url
        or "#" in url
        or parts.port == 0
    ):
        raise ValueError("relay URL must be http(s)://host:port")
    return url


def is_desktop_state(state: Any) -> bool:
    """Check the shape of the DesktopState fields the entities read."""

    def list_of(value: Any, kind: type) -> bool:
        return value is None or (
            isinstance(value, list) and all(isinstance(v, kind) for v in value)
        )

    return (
        isinstance(state, dict)
        and isinstance(state.get("self") or {}, dict)
        and list_of(state.get("zones"), dict)
        and list_of(state.get("people"), dict)
        and list_of(state.get("earshot"), str)
    )


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
                # A redirect would resend the body, which can carry the webhook secret.
                allow_redirects=False,
            ) as resp:
                if resp.status == 401:
                    raise RelayAuthError("wrong relay secret")
                text = await resp.text()
                if resp.status >= 300:
                    raise RelayError(f"relay returned {resp.status}: {text.strip()[:200]}")
        except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError) as err:
            raise RelayError(f"relay not reachable: {err}") from err
        try:
            return json.loads(text) if text.strip() else None
        except (ValueError, RecursionError) as err:
            raise RelayError(f"unexpected reply from the relay: {err}") from err

    async def _object(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        reply = await self._request(method, path, body)
        if not isinstance(reply, dict):
            raise RelayError("unexpected reply from the relay: not a JSON object")
        return reply

    async def status(self) -> dict[str, Any]:
        """GET /v1/status."""
        return await self._object("GET", "/v1/status")

    async def _desktop(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        state = await self._object(method, path, body)
        if not is_desktop_state(state):
            raise RelayError("unexpected reply from the relay: malformed desktop state")
        return state

    async def desktop(self) -> dict[str, Any]:
        """GET /v1/desktop."""
        return await self._desktop("GET", "/v1/desktop")

    async def boot(self) -> str:
        """Boot id of the running relay, from GET /v1/events (returns at once without params)."""
        boot = (await self._object("GET", "/v1/events")).get("boot")
        if not isinstance(boot, str):
            raise RelayError("unexpected reply from the relay: no boot id")
        return boot

    async def set_availability(self, availability: str) -> dict[str, Any]:
        """POST /v1/desktop/status."""
        return await self._desktop("POST", "/v1/desktop/status", {"availability": availability})

    async def set_media(self, **media: bool) -> dict[str, Any]:
        """POST /v1/desktop/media with audio= and/or video=."""
        return await self._desktop("POST", "/v1/desktop/media", media)

    async def put_webhook(self, hook_id: str, urls: list[str], secret: str) -> None:
        """PUT /v1/webhooks/{id}."""
        await self._request("PUT", f"/v1/webhooks/{hook_id}", {"urls": urls, "secret": secret})

    async def delete_webhook(self, hook_id: str) -> None:
        """DELETE /v1/webhooks/{id}."""
        await self._request("DELETE", f"/v1/webhooks/{hook_id}")
