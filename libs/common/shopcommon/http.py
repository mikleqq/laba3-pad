"""HTTP client for synchronous service-to-service calls.

* propagates ``X-Request-ID``;
* adds the internal service token;
* enforces a timeout and retries connection errors (the request was never sent);
* converts transport failures into 502/504 application errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UpstreamError,
    UpstreamTimeoutError,
)
from .logging import log_extra, request_id_var

logger = logging.getLogger("shopcommon.http_client")


class ServiceClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout: float = 5.0,
        internal_token: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        connect_retries: int = 2,
    ) -> None:
        self.name = name
        self.internal_token = internal_token
        self.connect_retries = connect_retries
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: Optional[bytes] = None,
        params: Any = None,
        headers: Optional[dict[str, str]] = None,
        raise_on_server_error: bool = True,
    ) -> httpx.Response:
        hdrs = {"X-Request-ID": request_id_var.get()}
        if self.internal_token:
            hdrs["X-Internal-Token"] = self.internal_token
        if headers:
            hdrs.update(headers)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.request(
                    method, path, json=json, content=content, params=params, headers=hdrs
                )
                break
            except httpx.TimeoutException as exc:
                logger.error("upstream timeout", extra=log_extra(upstream=self.name, path=path))
                raise UpstreamTimeoutError(f"{self.name} did not respond in time") from exc
            except httpx.TransportError as exc:
                # Connection-level failure: the request did not reach the service, retry is safe.
                if attempt > self.connect_retries:
                    logger.error("upstream unavailable", extra=log_extra(upstream=self.name, error=str(exc)))
                    raise UpstreamError(f"{self.name} is unavailable", code="upstream_unavailable") from exc
                await asyncio.sleep(0.2 * attempt)

        if raise_on_server_error and response.status_code >= 500:
            logger.error("upstream error", extra=log_extra(upstream=self.name, status=response.status_code))
            raise UpstreamError(f"{self.name} failed to process the request")
        return response

    async def close(self) -> None:
        await self._client.aclose()


def upstream_message(response: httpx.Response) -> Optional[str]:
    try:
        return response.json()["error"]["message"]
    except Exception:  # noqa: BLE001
        return None


def raise_for_upstream(response: httpx.Response, service: str) -> None:
    """Translate a 4xx/5xx answer of another service into our own error."""
    if response.is_success:
        return
    status = response.status_code
    message = upstream_message(response) or f"{service} returned HTTP {status}"
    if status in (400, 422):
        raise BadRequestError(message)
    if status == 404:
        raise NotFoundError(message)
    if status == 409:
        raise ConflictError(message)
    raise UpstreamError(f"{service}: {message}")
