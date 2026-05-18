from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    whatfontis_api_key: str = Field(default="", alias="WHATFONTIS_API_KEY")
    whatfontis_api_keys_raw: str = Field(default="", alias="WHATFONTIS_API_KEYS")
    database_url: str = Field(
        default="sqlite+aiosqlite:///bot.db",
        alias="DATABASE_URL",
    )
    admin_ids: str = Field(default="", alias="ADMIN_IDS")

    trial_days: int = Field(default=2, alias="TRIAL_DAYS")
    trial_requests_limit: int = Field(default=3, alias="TRIAL_REQUESTS_LIMIT")

    designer_price_stars: int = Field(default=99, alias="DESIGNER_PRICE_STARS")
    designer_monthly_limit: int = Field(default=20, alias="DESIGNER_MONTHLY_LIMIT")

    studio_price_stars: int = Field(default=199, alias="STUDIO_PRICE_STARS")
    studio_monthly_limit: int = Field(default=50, alias="STUDIO_MONTHLY_LIMIT")

    subscription_period: int = Field(default=2_592_000, alias="SUBSCRIPTION_PERIOD")
    subscription_product_id: str = Field(default="", alias="SUBSCRIPTION_PRODUCT_ID")
    daily_api_safety_limit: int = Field(default=90, alias="DAILY_API_SAFETY_LIMIT")
    admin_secret_code: str = Field(default="", alias="ADMIN_SECRET_CODE")
    admin_secret_enabled: bool = Field(default=True, alias="ADMIN_SECRET_ENABLED")

    support_username: str = Field(default="", alias="SUPPORT_USERNAME")
    terms_url: str = Field(default="", alias="TERMS_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def admin_id_set(self) -> set[int]:
        result: set[int] = set()
        for raw_id in self.admin_ids.replace(";", ",").split(","):
            raw_id = raw_id.strip()
            if raw_id:
                result.add(int(raw_id))
        return result

    @property
    def support_contact(self) -> str:
        return self.support_username.strip() or "не указана"

    @property
    def whatfontis_api_keys(self) -> list[str]:
        raw_keys = self.whatfontis_api_keys_raw or self.whatfontis_api_key
        keys: list[str] = []
        for raw_key in raw_keys.replace(";", ",").split(","):
            raw_key = raw_key.strip()
            if raw_key:
                keys.append(raw_key)
        return keys


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
