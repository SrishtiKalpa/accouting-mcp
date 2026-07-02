#!/bin/sh
set -e
python -c "import asyncio; from qbo_mcp.db.connection import init_schema; asyncio.run(init_schema())"
exec "$@"
