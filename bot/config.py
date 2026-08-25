"""Конфигурация приложения: читается из окружения и `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/bot.db"


class Settings(BaseSettings):
    """Все настройки бота. Секреты — только через окружение/`.env`."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- обязательное -------------------------------------------------------
    bot_token: SecretStr
    itad_api_key: SecretStr | None = None

    # --- регион -------------------------------------------------------------
    default_country: str = "KZ"
    default_currency: str = "KZT"
    timezone: str = "Asia/Almaty"

    # --- база ---------------------------------------------------------------
    database_url: str = DEFAULT_DATABASE_URL

    # --- расписание ---------------------------------------------------------
    price_check_interval_minutes: int = Field(default=60, ge=5)
    free_games_post_hour: int = Field(default=20, ge=0, le=23)

    # --- транспорт ----------------------------------------------------------
    use_webhook: bool = False
    webhook_base_url: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr | None = None
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080

    # --- HTTP ---------------------------------------------------------------
    http_timeout: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)

    # --- кэш, секунды -------------------------------------------------------
    cache_ttl_search: int = 60 * 60  # поиск — час
    cache_ttl_prices: int = 30 * 60  # цены — полчаса
    cache_ttl_shops: int = 24 * 60 * 60  # справочник магазинов — сутки

    # --- логи ---------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("default_country", "default_currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL должен быть одним из {sorted(allowed)}")
        return level

    @property
    def itad_key(self) -> str | None:
        """Ключ ITAD в открытом виде либо None, если не задан."""
        if self.itad_api_key is None:
            return None
        value = self.itad_api_key.get_secret_value().strip()
        return value or None

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def sqlite_path(self) -> Path | None:
        """Путь к файлу SQLite, если используется SQLite (для mkdir на старте)."""
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        if raw.startswith(":memory:"):
            return None
        path = Path(raw)
        return path if path.is_absolute() else ROOT_DIR / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Синглтон настроек — читается один раз за процесс."""
    return Settings()  # type: ignore[call-arg]  # значения приходят из окружения


def database_url_from_env() -> str:
    """URL базы без полной валидации `Settings`.

    Нужен Alembic: миграции должны прогоняться и там, где BOT_TOKEN не задан
    (CI, `alembic revision --autogenerate` на чистой машине).
    """
    import os

    if url := os.getenv("DATABASE_URL"):
        return url

    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DATABASE_URL="):
                value = line.partition("=")[2].strip().strip("'\"")
                if value:
                    return value

    return DEFAULT_DATABASE_URL
