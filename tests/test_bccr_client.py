"""Tests for ``bccr_mcp_server.bccr.client.BccrClient``."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from bccr_mcp_server.bccr.client import BccrClient
from bccr_mcp_server.bccr.models import BCCR_INDICATOR_BUY, BCCR_INDICATOR_SELL
from bccr_mcp_server.errors import AuthenticationError, UpstreamError


BASE_URL = "https://bccr.test/api"
TOKEN = "tok-secret-123"


# --- Local helper: build a fake BCCR series payload ------------------------
# Mirrors the actual BCCR response shape:
#   {"estado": true, "mensaje": "...", "datos": [{"codigoIndicador": ...,
#    "nombreIndicador": ..., "series": [{"fecha": ..., "valorDatoPorPeriodo": ...}]}]}
# Inlined rather than imported from conftest.py because pytest treats
# tests/ as a non-package and relative imports (``from .conftest import …``)
# raise ImportError. Keeping this helper local is simpler than sharing it.
def make_bccr_series_payload(
    indicator_code: str,
    rows: list[tuple[date, float | None]],
) -> dict[str, Any]:
    return {
        "estado": True,
        "mensaje": "Consulta exitosa",
        "datos": [
            {
                "codigoIndicador": indicator_code,
                "nombreIndicador": (
                    "Tipo cambio compra"
                    if indicator_code == BCCR_INDICATOR_BUY
                    else "Tipo cambio venta"
                ),
                "series": [
                    {
                        "fecha": d.isoformat(),
                        "valorDatoPorPeriodo": value,
                    }
                    for d, value in rows
                ],
            }
        ],
    }


async def test_fetch_series_builds_correct_url_and_headers(
    mock_bccr: respx.MockRouter,
) -> None:
    """URL path, Spanish date format, and bearer header must all be present."""
    d1 = date(2026, 4, 20)
    d2 = date(2026, 4, 21)

    route = mock_bccr.get(
        re.compile(rf"{re.escape(BASE_URL)}/indicadoresEconomicos/317/series.*")
    ).mock(
        return_value=httpx.Response(
            200,
            json=make_bccr_series_payload(BCCR_INDICATOR_BUY, [(d1, 507.2), (d2, 508.1)]),
        )
    )

    async with BccrClient(base_url=BASE_URL, bearer_token=TOKEN) as client:
        points = await client.fetch_series(BCCR_INDICATOR_BUY, d1, d2)

    assert len(points) == 2
    req = route.calls.last.request

    # Query params use Spanish slashes. URL-encoded %2F is equivalent.
    qs = str(req.url)
    assert "fechaInicio=2026%2F04%2F20" in qs or "fechaInicio=2026/04/20" in qs
    assert "fechaFin=2026%2F04%2F21" in qs or "fechaFin=2026/04/21" in qs
    assert "idioma=ES" in qs
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"


async def test_fetch_buy_sell_merges_two_series(
    mock_bccr: respx.MockRouter,
) -> None:
    d = date(2026, 4, 21)

    mock_bccr.get(re.compile(r".*/317/series.*")).mock(
        return_value=httpx.Response(
            200, json=make_bccr_series_payload(BCCR_INDICATOR_BUY, [(d, 507.2)])
        )
    )
    mock_bccr.get(re.compile(r".*/318/series.*")).mock(
        return_value=httpx.Response(
            200, json=make_bccr_series_payload(BCCR_INDICATOR_SELL, [(d, 513.5)])
        )
    )

    async with BccrClient(base_url=BASE_URL, bearer_token=TOKEN) as client:
        rates = await client.fetch_buy_sell(d, d)

    assert len(rates) == 1
    assert rates[0].observed_on == d
    assert rates[0].buy == pytest.approx(507.2)
    assert rates[0].sell == pytest.approx(513.5)


async def test_401_raises_authentication_error(
    mock_bccr: respx.MockRouter,
) -> None:
    mock_bccr.get(re.compile(r".*/series.*")).mock(
        return_value=httpx.Response(401, text="Invalid token")
    )

    async with BccrClient(base_url=BASE_URL, bearer_token=TOKEN) as client:
        with pytest.raises(AuthenticationError):
            await client.fetch_series(BCCR_INDICATOR_BUY, date(2026, 4, 21), date(2026, 4, 21))


async def test_503_raises_upstream_error(
    mock_bccr: respx.MockRouter,
) -> None:
    mock_bccr.get(re.compile(r".*/series.*")).mock(
        return_value=httpx.Response(503, text="Service unavailable")
    )

    async with BccrClient(base_url=BASE_URL, bearer_token=TOKEN) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.fetch_series(BCCR_INDICATOR_BUY, date(2026, 4, 21), date(2026, 4, 21))

    assert excinfo.value.status == 503


async def test_token_never_in_exception_text(mock_bccr: respx.MockRouter) -> None:
    """The bearer token must never appear in the thrown exception's message."""
    mock_bccr.get(re.compile(r".*/series.*")).mock(
        return_value=httpx.Response(500, text="Kaboom")
    )

    async with BccrClient(base_url=BASE_URL, bearer_token=TOKEN) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.fetch_series(BCCR_INDICATOR_BUY, date(2026, 4, 21), date(2026, 4, 21))

    assert TOKEN not in str(excinfo.value)
    assert TOKEN not in repr(excinfo.value)
