"""Application configuration loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    web_search_max_results: int = 5
    web_search_timeout_seconds: float = 10.0

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
        return self.web_search_provider == "mock" or not bool(
            self.web_search_api_key
        )


class InternalAuthSettings(BaseSettings):
    """Operator authentication settings loaded only by the web application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    internal_auth_username: str
    internal_auth_password: SecretStr
    internal_session_secret_key: SecretStr
    internal_session_max_age_seconds: int = Field(default=28800, gt=0)
    internal_auth_cookie_secure: bool = True
    app_env: Literal["development", "test", "production"] = "production"

    @field_validator("internal_auth_username")
    @classmethod
    def username_must_be_non_blank(cls, value: str) -> str:
        if not isinstance(value, str) or value.strip() == "":
            raise ValueError(
                "internal_auth_username must be a non-blank string"
            )
        if value.strip() != value:
            raise ValueError(
                "internal_auth_username must not include leading or "
                "trailing whitespace"
            )
        return value

    @field_validator("internal_auth_password")
    @classmethod
    def password_must_be_strong(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret.strip() == "" or secret.strip() != secret:
            raise ValueError(
                "internal_auth_password must be non-blank without "
                "surrounding whitespace"
            )
        if len(secret) < 12:
            raise ValueError(
                "internal_auth_password must be at least 12 characters"
            )
        return value

    @field_validator("internal_session_secret_key")
    @classmethod
    def session_secret_must_be_strong(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret.strip() == "" or secret.strip() != secret:
            raise ValueError(
                "internal_session_secret_key must be non-blank without "
                "surrounding whitespace"
            )
        if len(secret) < 32:
            raise ValueError(
                "internal_session_secret_key must be at least 32 characters"
            )
        return value

    @model_validator(mode="after")
    def cookie_secure_only_relaxed_in_dev_test(
        self,
    ) -> "InternalAuthSettings":
        if (
            not self.internal_auth_cookie_secure
            and self.app_env not in {"development", "test"}
        ):
            raise ValueError(
                "internal_auth_cookie_secure=false is allowed only when "
                "APP_ENV is development or test"
            )
        return self


settings = Settings()
