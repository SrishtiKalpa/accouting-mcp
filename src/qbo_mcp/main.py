from __future__ import annotations

import asyncio
import logging
import sys

import structlog

from qbo_mcp.config import settings
from qbo_mcp.db.connection import init_schema
from qbo_mcp.server import mcp


def _configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def main() -> None:
    _configure_logging()
    asyncio.run(init_schema())

    if settings.mcp_transport == "streamable-http":
        # FastMCP.run() takes no host/port — they are configured via settings.
        mcp.settings.host = settings.mcp_host
        mcp.settings.port = settings.mcp_port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
