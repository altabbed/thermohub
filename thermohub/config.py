"""Runtime configuration, read from environment variables (or a .env file)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # hide_input_in_errors: a config error would otherwise print the password.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    cookidoo_email: str
    cookidoo_password: SecretStr
    # Cookidoo is regionalised; au/en-AU is cookidoo.com.au.
    cookidoo_country: str = "au"
    cookidoo_language: str = "en-AU"

    # Where the Cookidoo OAuth tokens are persisted between restarts.
    data_dir: Path = Field(default=Path("/data"), validation_alias="THERMOHUB_DATA_DIR")
    # When set, every /api request must send it in the X-API-Key header.
    api_key: SecretStr | None = Field(default=None, validation_alias="THERMOHUB_API_KEY")

    @property
    def token_file(self) -> Path:
        return self.data_dir / "cookidoo_token.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
