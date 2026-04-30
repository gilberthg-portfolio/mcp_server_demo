"""MCP tool implementations.

Each module here contributes one or more tools that are registered with the
FastMCP server in `server.py`. Tool functions are the *only* place that
converts between the MCP-facing contract (ISO dates, English field names,
structured errors) and the internal BCCR data classes.
"""
