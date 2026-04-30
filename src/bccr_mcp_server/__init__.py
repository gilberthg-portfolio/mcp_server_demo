"""BCCR MCP Server package.

This package exposes the Central Bank of Costa Rica (BCCR) USD↔CRC exchange-rate
endpoints as MCP tools that Claude Desktop (and other MCP clients) can invoke.

High-level layout:
    config.py           -- loads the BCCR bearer token from the environment.
    errors.py           -- custom exception hierarchy used across the server.
    cache.py            -- in-memory cache of historical rates.
    bccr/client.py      -- async HTTP client that talks to the BCCR REST API.
    bccr/models.py      -- pydantic models for BCCR request/response shapes.
    tools/exchange_rate.py  -- the MCP tool implementations.
    tools/summarize.py  -- pure function that aggregates daily rates per month.
    tools/narrative.py  -- helpers for the optional sampling-based narrative.
    server.py           -- FastMCP server wiring.
    __main__.py         -- entry point for `python -m bccr_mcp_server`.

See the top-level README.md and the docs/ folder for a guided tour.
"""

# Keeping __init__.py almost empty is a deliberate choice: heavy imports at
# package import time would slow the MCP handshake. Consumers reach into
# `bccr_mcp_server.server`, `bccr_mcp_server.config`, etc. directly.

__version__ = "0.1.0"
