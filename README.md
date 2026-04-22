# BCCR MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes **USD ↔ CRC exchange rates**
published by the **Central Bank of Costa Rica (BCCR)** to MCP-compatible clients such as
Claude Desktop.

> **Status:** 🚧 Work in progress — design phase. Implementation will follow an
> OpenSpec-driven workflow.

---

## What it will do

This server provides tools a language model can call to query exchange-rate data from the
BCCR public economic-indicators API:

| Tool | Description |
|------|-------------|
| `get_current_exchange_rate` | Returns today's USD/CRC buy (317) and sell (318) rates. |
| `get_historical_exchange_rate` | Returns daily rates for a given date range. |

Behind the scenes it talks to the BCCR REST API at
`https://apim.bccr.fi.cr/SDDE/api/Bccr.GE.SDDE.Publico.Indicadores.API` using a Bearer
token obtained from the user's BCCR credentials.

---

## Tech stack

- **Python 3.11+**
- **[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)** (FastMCP style)
- **`httpx`** for async HTTP to BCCR
- **`python-dotenv`** for local credential loading

Design favors **SOLID** principles and a small, focused surface — no over-engineering.

---

## Installation

> These steps describe the target setup. The server is not yet published.

```bash
git clone https://github.com/gilberthg-portfolio/mcp_server_demo.git
cd mcp_server_demo
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
```

---

## Configuration — BCCR bearer token

You need your **own** BCCR bearer token. Register on the BCCR developer portal, issue
a token there, and supply it to the server — the server never ships with credentials
and never needs your username or password.

Provide the token via one environment variable:

```env
BCCR_TOKEN=your_bearer_token
```

For local development, place it in a `.env` file at the repo root (already in
`.gitignore`). For Claude Desktop, inject it through the `mcpServers.env` block in
`claude_desktop_config.json` (example below).

---

## Using with Claude Desktop

Add an entry to your Claude Desktop config:

```json
{
  "mcpServers": {
    "bccr": {
      "command": "python",
      "args": ["-m", "bccr_mcp_server"],
      "env": {
        "BCCR_TOKEN": "your_bearer_token"
      }
    }
  }
}
```

Restart Claude Desktop and the tools will appear under the MCP tool menu.

---

## Development

This project uses **OpenSpec** for change management. Design artifacts live under
`openspec/changes/` and specs under `openspec/specs/`.

Planned workflow:

1. `opsx:explore` — capture requirements
2. `opsx:ff` — generate proposal, design, spec deltas, and tasks
3. `opsx:apply` — implement tasks
4. `opsx:archive` — finalize when done

---

## License

TBD.

---

## Disclaimer

This project is not affiliated with or endorsed by the Banco Central de Costa Rica.
Rate data is fetched from the bank's public API; the server merely relays it.
