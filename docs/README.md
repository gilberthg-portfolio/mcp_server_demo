# BCCR MCP Server — docs

Welcome. These docs walk you through the server from three angles:

| # | File | What it covers | When to read it |
|---|------|----------------|-----------------|
| 01 | [`01-mcp-primer.md`](01-mcp-primer.md) | What the Model Context Protocol is, what a *server / client / host* is, what *tools* and *sampling* are, how capabilities get negotiated. | Read first if MCP is new to you. |
| 02 | [`02-python-concepts.md`](02-python-concepts.md) | A glossary of every Python idiom the code uses: `async`/`await`, context managers, decorators, pydantic, type hints, and more. Each entry cites the file in this repo where it first appears. | Reach for this whenever a piece of code looks unfamiliar. |
| 03 | [`03-code-walkthrough.md`](03-code-walkthrough.md) | A module-by-module tour of the codebase following the *request lifecycle*: startup → config → tool call → cache → BCCR → response → sampling. | Read this after 01 & 02 to see the pieces in motion. |

## Audience

These docs assume you already:

- **Write Python at a high level** — you've used Python scripts, understand modules and classes, and know what `pip install` does.
- **Know roughly what MCP is** — you've seen Claude Desktop tool calls or at least heard the acronym.

They then teach you:

- **The Python idioms** specific to this project (async/await, decorators, pydantic, typing).
- **MCP-specific patterns** — tools, sampling, capability checks, stdio transport.
- **How the pieces fit** — which module does what, and in what order they're invoked during a request.

## After reading

You should be able to:

1. Read any file under `src/bccr_mcp_server/` and understand not just *what* the code does but *why* it's structured that way.
2. Point at a specific line and say "this is where the bearer token is attached" or "this is the sampling request for the narrative."
3. Add a new tool (say, an interest-rate lookup) using the same patterns.

If any of that isn't true after you finish, please open an issue — the docs should make it true.
