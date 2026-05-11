"""OAuth2 client-credentials token cache with async-safe refresh."""
from __future__ import annotations

import asyncio
import time

import httpx

from flights_mcp.errors import ErrorCode, ToolError

_REFRESH_BUFFER_SECONDS = 60


class TokenCache:
    def __init__(self, *, client: httpx.AsyncClient, base_url: str,
                 client_id: str, client_secret: str):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        if self._is_valid():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            # Re-check under lock — another task may have refreshed while we waited.
            if self._is_valid():
                return self._token  # type: ignore[return-value]
            await self._refresh()
            return self._token  # type: ignore[return-value]

    def _is_valid(self) -> bool:
        return self._token is not None and time.monotonic() < self._expires_at

    async def _refresh(self) -> None:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Token fetch network error: {e}") from e

        if response.status_code == 401:
            raise ToolError(ErrorCode.AUTH_FAILED, "Amadeus rejected credentials during token fetch.")
        if response.status_code >= 500:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Amadeus token endpoint returned {response.status_code}.")
        if response.status_code != 200:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Unexpected token endpoint status {response.status_code}: {response.text[:200]}")

        body = response.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not token or not isinstance(expires_in, int):
            raise ToolError(ErrorCode.UPSTREAM_ERROR, "Malformed token response.")
        self._token = token
        self._expires_at = time.monotonic() + max(0, expires_in - _REFRESH_BUFFER_SECONDS)
