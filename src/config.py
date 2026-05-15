import os
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    app_name: str = "SamvadSQL"
    app_env: str = "dev"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    ui_api_base_url: str = "http://localhost:8000"

    llm_provider: Literal["openai", "gemini", "none"] = "gemini"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_user: str = os.getenv("POSTGRES_USER", "postgres")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    postgres_db: str = os.getenv("POSTGRES_DB", "sql_testing_db")
    database_url: str = f"postgresql+psycopg2://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"

    # database_url: str = ""
    # postgres_host: str = ""
    # postgres_port: int = 5432
    # postgres_user: str = ""
    # postgres_password: str = ""
    # postgres_db: str = ""
    postgres_admin_db: str = "postgres"
    postgres_sslmode: str = ""
    test_db_template: str = Field(
        default="test_template",
        validation_alias=AliasChoices("TEST_DB_TEMPLATE", "TEST_TEMPLATE_DB"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5

    max_scenarios: int = 8
    max_retry_attempts: int = 2
    scenario_timeout_ms: int = 20000
    test_isolation_mode: Literal["transaction", "database"] = "transaction"
    test_isolation_auto_fallback: bool = True

    allow_destructive_without_approval: bool = False
    approval_tokens: str = "yes,y"

    @property
    def has_database(self) -> bool:
        if self.database_url.strip():
            return True
        return bool(self.postgres_host and self.postgres_user and self.postgres_db)

    @property
    def normalized_approval_tokens(self) -> set[str]:
        return {token.strip().lower() for token in self.approval_tokens.split(",") if token.strip()}


settings = Settings()
