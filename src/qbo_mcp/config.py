from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Transport
    mcp_transport: str = "stdio"      # "stdio" or "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # QuickBooks OAuth
    qbo_client_id: str = ""
    qbo_client_secret: str = ""
    qbo_environment: str = "sandbox"   # "sandbox" or "production"
    qbo_redirect_uri: str = "http://localhost:8000/oauth/callback"

    # Database
    db_path: str = "./accounting.db"

    # Safety defaults
    default_draft_mode: bool = True    # all writes go to draft first
    log_level: str = "INFO"


settings = Settings()
