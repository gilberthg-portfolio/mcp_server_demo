"""Entry point for ``python -m bccr_mcp_server`` and the ``bccr-mcp-server`` script.

Startup order:
    1. Configure logging to stderr.
    2. Load settings (reads BCCR_TOKEN). Exit non-zero if missing.
    3. Build the BCCR HTTP client and the cache service.
    4. Hand those to the tool module (``configure_tools``).
    5. Start the FastMCP event loop over stdio.

The reason we configure everything before calling ``mcp.run()`` is so that a
misconfigured server fails immediately — Claude Desktop's MCP status panel
then shows a clear error instead of a cryptic tool-call failure later.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .bccr.client import BccrClient
from .cache import CacheBackedRateService, HistoricalRateCache
from .config import load_settings
from .errors import ConfigurationError
from .server import mcp
from .tools import exchange_rate as _exchange_rate


def _configure_logging() -> None:
    """Log to stderr so stdout stays clean for the MCP stdio transport.

    The MCP protocol uses line-delimited JSON on stdout; any stray print()
    call would corrupt the stream. Logging to stderr is the convention.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def _run() -> None:
    """Create async resources and hand control to FastMCP.

    Everything created with ``async with`` is closed when the event loop
    exits, even if ``mcp.run()`` raises.
    """
    settings = load_settings()

    # The BccrClient is an async context manager — entering it opens the
    # underlying httpx pool. The pool survives for the life of the server.
    async with BccrClient(
        base_url=settings.bccr_base_url,
        bearer_token=settings.bccr_token,
    ) as client:
        cache = HistoricalRateCache()
        service = CacheBackedRateService(client=client, cache=cache)

        # Inject collaborators into the tools module's module-level slots.
        # Done *before* the server loop starts so tool calls can find them.
        _exchange_rate.configure_tools(client=client, service=service)

        # One-line readiness signal to stderr. Stdout is reserved for the
        # MCP JSON-RPC stream, so we never print there.
        logging.getLogger(__name__).info(
            "BCCR MCP server ready (stdio). Base URL: %s", settings.bccr_base_url
        )

        # FastMCP exposes ``run_stdio_async`` for the long-lived stdio loop.
        await mcp.run_stdio_async()


def main() -> None:
    """Sync-world entry point. Wraps async startup and handles config errors."""
    _configure_logging()
    try:
        asyncio.run(_run())
    except ConfigurationError as exc:
        # Print the exact reason to stderr and exit non-zero. Claude Desktop
        # picks this up and surfaces it in the MCP connection status panel.
        print(f"[bccr-mcp-server] {exc}", file=sys.stderr)
        sys.exit(1)


# Standard Python idiom: the ``if __name__ == "__main__"`` guard runs main()
# only when this file is executed directly (e.g. via ``python -m bccr_mcp_server``),
# not when it is imported by tests.
if __name__ == "__main__":
    main()
