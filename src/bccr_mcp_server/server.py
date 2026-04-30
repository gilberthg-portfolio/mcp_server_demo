"""FastMCP server definition and tool registration.

This module creates the singleton ``FastMCP`` instance and wires our two
tool functions to it. Actual startup logic (loading settings, opening the
HTTP client, entering the event loop) lives in ``__main__.py`` so this file
stays importable without side effects.
"""

from __future__ import annotations

# --- MCP SDK ---------------------------------------------------------------
# FastMCP is the "batteries-included" decorator-based API layered on top of
# the lower-level MCP server primitives. We register tools with
# ``@mcp.tool()`` — FastMCP inspects type hints and the docstring to build
# the JSON schema the client sees.
from mcp.server.fastmcp import FastMCP

from .tools import exchange_rate as _exchange_rate

# The string name is the server identifier MCP clients display. It shows up
# in Claude Desktop's tool menu as the prefix for every tool.
mcp = FastMCP("bccr")


# --- Python idiom: registering a function with a decorator at module scope --
# We *could* use ``@mcp.tool()`` directly on each function in
# ``tools/exchange_rate.py``, but doing it here keeps the "which tools does
# this server expose?" answer in one place. FastMCP copies the function's
# docstring into the tool description, so keep those tool docstrings
# informative — the language model reads them.

mcp.tool(name="get_current_exchange_rate")(
    _exchange_rate.get_current_exchange_rate
)

mcp.tool(name="get_historical_exchange_rate")(
    _exchange_rate.get_historical_exchange_rate
)
