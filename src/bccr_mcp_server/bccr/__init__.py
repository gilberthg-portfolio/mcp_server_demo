"""BCCR adapter layer.

Everything that knows about the BCCR REST API lives in this sub-package —
URL shapes, Spanish field names, indicator codes, and the HTTP client itself.
The rest of the server talks to BCCR only through the classes exported here,
which keeps the MCP tool layer independent of the upstream contract.
"""
