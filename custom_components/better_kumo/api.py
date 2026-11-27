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
        and all(isinstance(p.get("id"), str) for p in state.get("people") or [])
        and list_of(state.get("earshot"), str)
        and all(isinstance(state.get(k), bool | None) for k in ("inCall", "audio", "video"))
    )


def idle_seconds(status: dict[str, Any]) -> int | None:
    """Seconds since the last input on the relay's computer; None when unknown."""
    value = status.get("idle_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**64:
        return value
    return None


# Remote error bodies may contain secrets, so messages use fixed text per status.
DESKTOP_ERRORS = {
    503: "Kumospace desktop is not connected to the relay, or not in a space",
    404: "the relay runs with the desktop bridge disabled",
}
MEDIA_ERRORS = {**DESKTOP_ERRORS, 503: "Kumospace desktop is not in a call"}
STATUS_ERRORS = {
    400: "the relay rejected the request as invalid",
    404: "the relay does not know this endpoint; update the relay",
    503: "the relay is temporarily unavailable",
}
MEDIA_NAMES = {"audio": "microphone", "video": "camera"}


def http_error(action: str, status: int, errors: dict[int, str] | None = None) -> str:
    """A message for a non-2xx answer, from the status and the endpoint alone."""
    if 300 <= status < 400:
        reason = "the relay answered with a redirect; use the relay's own address"
    else:
        reason = (errors or {}).get(status) or STATUS_ERRORS.get(
            status, "the relay answered with an error"
        )
    return f"{action} failed: {reason} (HTTP {status})"


class RelayClient:
    """Thin async wrapper over the relay's /v1 endpoints."""

    def __init__(self, session: aiohttp.ClientSession, url: str, secret: str) -> None:
        self._session = session
        self._url = url
        self._headers = {"Authorization": f"Bearer {secret}"}

    async def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        action: str,
        errors: dict[int, str] | None = None,
    ) -> Any:
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
                    raise RelayAuthError(f"{action} failed: wrong relay secret (HTTP 401)")
                if resp.status >= 300:
                    raise RelayError(http_error(action, resp.status, errors))
                # The relay sends UTF-8 JSON; a charset like base64 would raise LookupError.
                text = await resp.text(encoding="utf-8")
        except TimeoutError:
            raise RelayError(f"{action} failed: relay not reachable: timed out") from None
        except (aiohttp.ClientSSLError, aiohttp.ClientConnectorCertificateError) as err:
            # TLS errors can quote the peer certificate.
            raise RelayError(
                f"{action} failed: relay not reachable: TLS error ({type(err).__name__})"
            ) from None
        except aiohttp.ClientConnectorError as err:
            raise RelayError(f"{action} failed: relay not reachable: {err}") from err
        except aiohttp.ClientError as err:
            # Other aiohttp errors can quote a malformed reply's status line or headers.
            raise RelayError(
                f"{action} failed: relay not reachable ({type(err).__name__})"
            ) from None
        except UnicodeDecodeError:
            raise RelayError(
                f"{action} failed: unexpected reply from the relay: not UTF-8"
            ) from None
        try:
            return json.loads(text) if text.strip() else None
        except (ValueError, RecursionError):
            raise RelayError(
                f"{action} failed: unexpected reply from the relay: not JSON"
            ) from None

    async def _object(
        self, method: str, path: str, body: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        reply = await self._request(method, path, body, **kwargs)
        if not isinstance(reply, dict):
            raise RelayError(
                f"{kwargs['action']} failed: unexpected reply from the relay: not a JSON object"
            )
        return reply

    async def status(self) -> dict[str, Any]:
        """GET /v1/status."""
        return await self._object("GET", "/v1/status", action="Reading the relay status")

    async def _desktop(
        self, method: str, path: str, body: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        state = await self._object(method, path, body, **kwargs)
        if not is_desktop_state(state):
            raise RelayError(
                f"{kwargs['action']} failed: unexpected reply from the relay: "
                "malformed desktop state"
            )
        return state

    async def desktop(self) -> dict[str, Any]:
        """GET /v1/desktop."""
        return await self._desktop(
            "GET", "/v1/desktop", action="Reading Kumospace", errors=DESKTOP_ERRORS
        )

    async def set_availability(self, availability: str) -> dict[str, Any]:
        """POST /v1/desktop/status."""
        return await self._desktop(
            "POST",
            "/v1/desktop/status",
            {"availability": availability},
            action="Setting the Kumospace status",
            errors=DESKTOP_ERRORS,
        )

    async def set_media(self, **media: bool) -> dict[str, Any]:
        """POST /v1/desktop/media with audio= and/or video=."""
        turns = " and ".join(
            f"the {MEDIA_NAMES.get(k, k)} {'on' if v else 'off'}" for k, v in media.items()
        )
        return await self._desktop(
            "POST",
            "/v1/desktop/media",
            media,
            action=f"Turning {turns}",
            errors=MEDIA_ERRORS,
        )

    async def put_webhook(self, hook_id: str, urls: list[str], secret: str) -> None:
        """PUT /v1/webhooks/{id}."""
        await self._request(
            "PUT",
            f"/v1/webhooks/{hook_id}",
            {"urls": urls, "secret": secret},
            action="Registering the webhook",
        )

    async def delete_webhook(self, hook_id: str) -> None:
        """DELETE /v1/webhooks/{id}."""
        await self._request("DELETE", f"/v1/webhooks/{hook_id}", action="Removing the webhook")
