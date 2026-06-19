"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Web search
    web_search_api_key: str = ""
    web_search_provider: str = "mock"

    # Database
    database_url: str = (
        "postgresql+psycopg2://company_user:company_pass@localhost:5432/company_verification"
    )

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    output_dir: str = "outputs"

    @property
    def reports_dir(self) -> Path:
        return Path(self.output_dir) / "reports"

    @property
    def json_dir(self) -> Path:
        return Path(self.output_dir) / "json"

    @property
    def use_mock_llm(self) -> bool:
        return not bool(self.openai_api_key)

    @property
    def use_mock_search(self) -> bool:
        return self.web_search_provider == "mock" or not bool(self.web_search_api_key)


settings = Settings()