# Quick Start

## Prerequisites

- QuickBooks Online account (sandbox or production)
- Intuit Developer app credentials from [developer.intuit.com](https://developer.intuit.com)

## Docker (30 seconds)

```bash
git clone https://github.com/yourusername/qbo-mcp
cd qbo-mcp
cp .env.example .env
# Edit .env with your QBO_CLIENT_ID and QBO_CLIENT_SECRET
docker compose up
```

The server starts at `http://localhost:8000`.

## Claude Desktop (stdio)

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

## Connecting your first company

1. In Claude, ask: _"Connect my QuickBooks company with realm ID 9341453472467123"_
2. Claude calls `connect_company` and returns an Intuit OAuth URL
3. Open the URL in your browser and authorise access
4. Copy the `code` and `state` from the callback URL
5. Tell Claude: _"Complete the OAuth with code=... state=..."_
6. Claude calls `complete_oauth_connection` and the company is saved

Now you can ask things like:
- _"Show me all unpaid invoices"_
- _"What's our P&L for Q1 2025?"_
- _"Create an invoice for Acme Corp for $2,500"_
