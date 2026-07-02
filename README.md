# qbo-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/qbo-mcp)](https://pypi.org/project/qbo-mcp/)
[![Docker Pulls](https://img.shields.io/docker/pulls/srishtikalpa/qbo-mcp)](https://hub.docker.com/r/srishtikalpa/qbo-mcp)

Open-source MCP server that connects AI assistants (Claude, Cursor, Windsurf, etc.) to QuickBooks Online. Ask natural language questions about your books, create invoices, run reports, and more — with a human-in-the-loop safety layer built in.

## What is this?

`qbo-mcp` implements the [Model Context Protocol](https://modelcontextprotocol.io) to give AI assistants direct, safe access to your QuickBooks Online data. All write operations go through a draft-and-confirm workflow — the AI proposes changes, you approve them.

## Quick Start — Docker (30 seconds)

```yaml
# docker-compose.yml
services:
  qbo-mcp:
    image: srishtikalpa/qbo-mcp:latest
    ports: ["8000:8000"]
    environment:
      QBO_CLIENT_ID: your_client_id
      QBO_CLIENT_SECRET: your_client_secret
      QBO_ENVIRONMENT: sandbox
```

```bash
# 1. Clone
git clone https://github.com/yourusername/qbo-mcp && cd qbo-mcp

# 2. Configure
cp .env.example .env  # Fill in QBO_CLIENT_ID, QBO_CLIENT_SECRET

# 3. Start
docker compose up

# 4. Connect company (in Claude)
# Ask: "Connect my QuickBooks company with realm ID 9341453472467123"

# 5. Ask questions
# "Show me all overdue invoices"
# "What's our net income for 2025 Q1?"
```

## Quick Start — Claude Desktop (stdio)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quickbooks": {
      "command": "uvx",
      "args": ["qbo-mcp"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "QBO_CLIENT_ID": "your_client_id",
        "QBO_CLIENT_SECRET": "your_client_secret",
        "QBO_ENVIRONMENT": "sandbox"
      }
    }
  }
}
```

Or run directly: `uvx qbo-mcp`

## Connecting your first company

1. Get credentials from [developer.intuit.com](https://developer.intuit.com) — create an app, note the Client ID and Secret
2. Find your Realm ID in your QBO URL: `qbo.intuit.com/app/homepage?realmId=XXXXXXX`
3. Tell Claude: _"Connect my QuickBooks company 'Acme Corp' with realm ID XXXXXXX"_
4. Claude returns an Intuit OAuth URL — open it, sign in, and authorise
5. Copy `code` and `state` from the callback, tell Claude to `complete_oauth_connection`
6. Done — ask anything about your books

## Safety model

Every write operation (create, update, void, send) goes through a three-step process:

1. **Draft** — Claude proposes the change and returns a `draft_action_id`
2. **Preview** — you can call `preview_action` to see exactly what will be sent to QBO
3. **Commit or Discard** — call `commit_action` to execute or `discard_action` to cancel

Additional guards:
- **Read-only mode** — per-company flag that blocks all writes
- **Dollar threshold** — block writes above a configurable USD amount per company
- **Audit log** — every tool call is logged to SQLite with outcome and draft ID

## Tool reference

See [docs/TOOLS.md](docs/TOOLS.md) for the full list of 30+ tools.

Highlights:
- **CRUD**: invoices, customers, vendors, bills, accounts
- **Reports**: P&L, balance sheet, cash flow, AR/AP aging, trial balance
- **Intelligence**: duplicate detection, month-end checklist, anomaly detection, health summary

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_PORT` | `8000` | HTTP server port |
| `QBO_CLIENT_ID` | — | Intuit app Client ID (required) |
| `QBO_CLIENT_SECRET` | — | Intuit app Client Secret (required) |
| `QBO_ENVIRONMENT` | `sandbox` | `sandbox` or `production` |
| `QBO_REDIRECT_URI` | `http://localhost:8000/oauth/callback` | OAuth callback URL |
| `DB_PATH` | `./qbo_mcp.db` | SQLite database path |
| `DEFAULT_DRAFT_MODE` | `true` | All writes go to draft first |
| `LOG_LEVEL` | `INFO` | Logging level |

## Contributing

```bash
git clone https://github.com/yourusername/qbo-mcp
cd qbo-mcp
uv sync --extra dev
cp .env.example .env
uv run pytest
```

PRs welcome. Please run `uv run ruff check` and `uv run mypy src/` before submitting.

## License

MIT — see [LICENSE](LICENSE).
