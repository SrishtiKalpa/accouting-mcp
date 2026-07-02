SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    realm_id        TEXT NOT NULL UNIQUE,
    access_token    TEXT,
    refresh_token   TEXT NOT NULL,
    token_expires_at INTEGER NOT NULL,
    read_only       INTEGER NOT NULL DEFAULT 0,
    write_threshold_usd REAL,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at      INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS draft_actions (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    description     TEXT NOT NULL,
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    committed_at    INTEGER,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state           TEXT PRIMARY KEY,
    realm_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    redirect_uri    TEXT NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    company_id      TEXT,
    tool_name       TEXT NOT NULL,
    input_summary   TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    draft_action_id TEXT,
    error_message   TEXT,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);
"""
