"""Async HTTP client for the BCCR economic-indicators API.

This module is the *only* place that knows BCCR's URL shape, query parameters
(``fechaInicio`` / ``fechaFin`` in ``YYYY/MM/DD`` format), or Spanish field
names. Everywhere else works with ``FlatRate`` objects returned by
``BccrClient.fetch_buy_sell``.

Error translation:
    * HTTP 401 → ``AuthenticationError``
    * Other HTTP 4xx/5xx → ``UpstreamError(status=…, detail=…)``
    * Network / timeout errors → ``UpstreamError(status=None, detail=…)``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Self

# --- Python idiom: `typing.Self` (Python 3.11+) -----------------------------
# `Self` is a type hint that means "whatever concrete subclass we're in".
# We use it on `__aenter__` so the returned type stays accurate if someone
# subclasses BccrClient.

# --- Third-party: httpx -----------------------------------------------------
# `httpx.AsyncClient` is the async counterpart of the familiar `requests`
# library. It reuses connections across calls (pool semantics) which matters
# because we fire two requests in parallel (series 317 and 318) for every
# rate lookup.
import httpx
from pydantic import ValidationError as PydanticValidationError

from ..errors import AuthenticationError, UpstreamError
from .models import (
    BCCR_INDICATOR_BUY,
    BCCR_INDICATOR_SELL,
    BccrResponse,
    BccrSeriesPoint,
    FlatRate,
)


log = logging.getLogger(__name__)


# BCCR wants dates with forward slashes, not dashes. One tiny helper keeps
# the formatting concern out of the call-site code.
def _fmt_bccr_date(d: date) -> str:
    return d.strftime("%Y/%m/%d")


class BccrClient:
    """Thin async wrapper around the BCCR ``/indicadoresEconomicos/{code}/series`` endpoint.

    Usage::

        async with BccrClient(base_url=..., bearer_token=...) as client:
            rates = await client.fetch_buy_sell(start, end)
    """

    # 10-second default timeout. Long enough for BCCR's cold-start latency,
    # short enough that a genuinely hung call fails fast.
    DEFAULT_TIMEOUT_SECONDS: float = 10.0

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS

        # The httpx client is lazy-initialised inside ``__aenter__`` so a
        # ``BccrClient`` can be constructed at import time without side effects.
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Async context manager protocol
    # ------------------------------------------------------------------
    # --- Python idiom: `async with` and `__aenter__` / `__aexit__` ------
    # Implementing these two magic methods makes the class usable with
    # `async with`. Enter creates resources (here: the httpx client); exit
    # releases them (here: closes the connection pool). This mirrors the
    # familiar synchronous context-manager pattern (__enter__ / __exit__).

    async def __aenter__(self) -> Self:
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_series(
        self,
        indicator_code: str,
        start: date,
        end: date,
    ) -> list[BccrSeriesPoint]:
        """Fetch a single BCCR series for the inclusive date range [start, end]."""
        http = self._require_http()

        url = f"{self._base_url}/indicadoresEconomicos/{indicator_code}/series"
        params = {
            "fechaInicio": _fmt_bccr_date(start),
            "fechaFin": _fmt_bccr_date(end),
            "idioma": "ES",
        }
        headers = {
            # Every BCCR request carries the bearer token. We pass it here and
            # NEVER log the header anywhere in this module.
            "Authorization": f"Bearer {self._bearer_token}",
            "Accept": "application/json",
        }

        try:
            response = await http.get(url, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Network-level problems: wrap as UpstreamError with no status.
            # ``str(exc)`` for TransportError is safe (no token echoed).
            raise UpstreamError(status=None, detail=str(exc)) from exc

        if response.status_code == 401:
            raise AuthenticationError(detail=_safe_body_text(response))
        if response.status_code >= 400:
            raise UpstreamError(
                status=response.status_code,
                detail=_safe_body_text(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            log.warning(
                "BCCR returned a non-JSON body for indicator %s; status=%s",
                indicator_code, response.status_code,
            )
            raise UpstreamError(
                status=response.status_code,
                detail="BCCR returned a non-JSON response.",
            ) from exc

        # The real BCCR envelope is always a dict shaped like
        # {"estado": bool, "mensaje": str, "datos": [{"codigoIndicador": ...,
        #  "series": [{"fecha": ..., "valorDatoPorPeriodo": ...}, ...]}]}.
        if not isinstance(payload, dict):
            raise UpstreamError(
                status=response.status_code,
                detail=f"Unexpected payload type: {type(payload).__name__}",
            )

        try:
            bccr_response = BccrResponse.model_validate(payload)
        except PydanticValidationError as exc:
            # Critical: do NOT include `payload` or `exc` text in the error we
            # propagate, because pydantic's error message embeds the offending
            # input data verbatim — and that input is BCCR rate data we must
            # not leak through MCP tool-error messages. Log the diagnostic
            # detail to stderr (visible only to the operator) and raise a
            # sanitized UpstreamError.
            log.warning(
                "Failed to parse BCCR response for indicator %s. "
                "Pydantic errors: %s. Raw payload (truncated): %s",
                indicator_code,
                exc.errors(),
                _truncate(repr(payload), 800),
            )
            raise UpstreamError(
                status=response.status_code,
                detail="BCCR response did not match the expected schema.",
            ) from exc

        # BCCR can report a logical failure with HTTP 200 by setting
        # ``estado=false`` and putting the reason in ``mensaje``. We treat
        # that the same as an HTTP error.
        if not bccr_response.estado:
            raise UpstreamError(
                status=response.status_code,
                detail=bccr_response.mensaje or "BCCR reported a failure.",
            )

        return bccr_response.points_for(indicator_code)

    async def fetch_buy_sell(self, start: date, end: date) -> list[FlatRate]:
        """Fetch buy + sell series and merge them by date.

        We fire the two requests concurrently with ``asyncio.gather`` so the
        end-to-end latency is roughly max(buy, sell) instead of their sum.
        """
        buy_points, sell_points = await asyncio.gather(
            self.fetch_series(BCCR_INDICATOR_BUY, start, end),
            self.fetch_series(BCCR_INDICATOR_SELL, start, end),
        )

        # Index by date so we can merge in one pass.
        buy_by_date: dict[date, float | None] = {
            p.fecha: p.valor for p in buy_points
        }
        sell_by_date: dict[date, float | None] = {
            p.fecha: p.valor for p in sell_points
        }

        all_dates = sorted(set(buy_by_date) | set(sell_by_date))
        return [
            FlatRate(
                observed_on=d,
                buy=buy_by_date.get(d),
                sell=sell_by_date.get(d),
            )
            for d in all_dates
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_http(self) -> httpx.AsyncClient:
        """Return the underlying httpx client or explain the misuse."""
        if self._http is None:
            raise RuntimeError(
                "BccrClient must be used inside `async with` — the httpx "
                "client is only created on context entry."
            )
        return self._http


def _truncate(s: str, n: int) -> str:
    """Return at most ``n`` characters of ``s``, suffixed with an ellipsis if cut."""
    return s if len(s) <= n else s[:n] + "…"


def _safe_body_text(response: httpx.Response) -> str | None:
    """Return the first 200 chars of the response body, or None.

    BCCR's error bodies are usually a one-line Spanish string. We clip to 200
    characters so a rogue HTML page can't flood the logs, and strip to avoid
    trailing whitespace artifacts.
    """
    try:
        text = response.text
    except Exception:  # noqa: BLE001 — defensive: decoding errors are rare
        return None
    if not text:
        return None
    trimmed = text.strip()
    return trimmed[:200] if trimmed else None
