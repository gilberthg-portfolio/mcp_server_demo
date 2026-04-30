"""Diagnostic probe — print raw BCCR series responses.

Run from the project root with the venv active:

    python scripts/probe_bccr.py                # default: today + last 7 days
    python scripts/probe_bccr.py 2026-04-15 2026-04-22

This bypasses every parser in the server and just prints status + body for
two requests: a single-day fetch and a multi-day fetch. We need both to
verify the response shape is the same in each case.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

import httpx

from bccr_mcp_server.config import load_settings


async def fetch_and_print(
    *,
    base_url: str,
    token: str,
    indicator: str,
    start: date,
    end: date,
) -> None:
    url = f"{base_url}/indicadoresEconomicos/{indicator}/series"
    params = {
        "fechaInicio": start.strftime("%Y/%m/%d"),
        "fechaFin": end.strftime("%Y/%m/%d"),
        "idioma": "ES",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    print(f"--- Indicator {indicator}, {start.isoformat()} → {end.isoformat()} ---")
    print(f"GET {url}")
    print(f"params: {params}")
    print()

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params, headers=headers)

    print(f"Status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")
    print()
    print("Body:")
    print(response.text)
    print()


async def main() -> None:
    settings = load_settings()

    if len(sys.argv) >= 3:
        start_arg = date.fromisoformat(sys.argv[1])
        end_arg = date.fromisoformat(sys.argv[2])
    else:
        # Default: today and a week-back range so we see multi-day shape too.
        end_arg = date.today()
        start_arg = end_arg - timedelta(days=7)

    today = date.today()

    # 1) Single-day call (sell, indicator 318) — this is what worked.
    await fetch_and_print(
        base_url=settings.bccr_base_url,
        token=settings.bccr_token,
        indicator="318",
        start=today,
        end=today,
    )

    # 2) Multi-day call (sell). Most likely culprit if historical fails.
    await fetch_and_print(
        base_url=settings.bccr_base_url,
        token=settings.bccr_token,
        indicator="318",
        start=start_arg,
        end=end_arg,
    )


if __name__ == "__main__":
    asyncio.run(main())
