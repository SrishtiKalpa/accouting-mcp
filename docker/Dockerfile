FROM python:3.12-slim AS base
WORKDIR /app

RUN pip install uv --no-cache-dir

# ── deps stage ────────────────────────────────────────────────────────────────
FROM base AS deps
COPY pyproject.toml .
RUN uv pip install --system ".[dev]" --no-cache

# ── production stage ──────────────────────────────────────────────────────────
FROM base AS production
COPY pyproject.toml .
RUN uv pip install --system . --no-cache

COPY src/ ./src/

RUN mkdir -p /data && useradd -r -u 1001 appuser && chown appuser /data
USER appuser
VOLUME ["/data"]

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000
ENV DB_PATH=/data/qbo_mcp.db

EXPOSE 8000

COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["qbo-mcp"]
