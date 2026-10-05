"""Configuration management for ReviewLens."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["dev", "test", "prod"] = "dev"

    # LLM Configuration
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_api_keys: str = Field(default="", description="Comma-separated Google Gemini API keys")
    gemini_model: str = Field(default="gemini-2.5-flash-lite", description="Gemini model ID")
    llm_rpm_limit: int = Field(default=10, description="LLM requests per minute limit")
    llm_timeout_s: int = Field(default=45, description="LLM request timeout in seconds")

    @property
    def api_keys(self) -> list[str]:
        keys: list[str] = []
        if self.gemini_api_key:
            keys.extend([k.strip() for k in self.gemini_api_key.split(",") if k.strip()])
        if self.gemini_api_keys:
            keys.extend([k.strip() for k in self.gemini_api_keys.split(",") if k.strip()])
        return list(dict.fromkeys(keys))

    # Database Configuration
    duckdb_path: Path = Field(
        default=Path("data/warehouse/reviewlens.duckdb"),
        description="Path to DuckDB database file",
    )
    data_meta_path: Path = Field(
        default=Path("data/processed/meta.json"),
        description="Path to data metadata file",
    )

    # Vector Database Configuration
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant server URL")
    qdrant_api_key: str = Field(default="", description="Qdrant API key (optional for local)")
    qdrant_collection: str = Field(default="reviews", description="Qdrant collection name")

    # Search Configuration
    retrieval_mode: Literal["hybrid_rrf", "bm25", "dense"] = Field(
        default="hybrid_rrf", description="Retrieval mode for search"
    )
    retrieval_top_k: int = Field(default=8, description="Number of results to retrieve")
    retrieval_prefetch_k: int = Field(default=30, description="Prefetch limit for hybrid search")
    fastembed_cache_path: Path = Field(
        default=Path(".fastembed_cache"),
        description="Cache directory for fastembed models",
    )

    # SQL Configuration
    sql_max_rows: int = Field(default=500, description="Maximum rows returned by SQL queries")
    sql_timeout_s: int = Field(default=15, description="SQL query timeout in seconds")
    sql_max_attempts: int = Field(default=3, description="Maximum SQL repair attempts")

    # Agent Configuration
    agent_max_llm_calls: int = Field(default=8, description="Maximum LLM calls per agent request")
    agent_timeout_s: int = Field(default=60, description="Agent request timeout in seconds")

    # API Configuration
    ingest_api_key: str = Field(default="", description="API key for ingest endpoint")
    rate_limit_per_min: int = Field(default=10, description="Rate limit per minute per IP")

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env == "prod"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.app_env == "dev"

    @property
    def is_test(self) -> bool:
        """Check if running in test environment."""
        return self.app_env == "test"


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


# For backwards compatibility and convenience
settings = get_settings()
