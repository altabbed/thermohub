"""Runtime configuration, read from environment variables (or a .env file)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # hide_input_in_errors: a config error would otherwise print the password.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # Optional: the usual way to connect Cookidoo is the Settings page. When
    # these are set, Thermohub signs in with them until someone signs out there.
    cookidoo_email: str | None = None
    cookidoo_password: SecretStr | None = None
    # Default Cookidoo site until one is picked on the Settings page.
    cookidoo_country: str = "au"
    cookidoo_language: str = "en-AU"

    # Where the Cookidoo sign-in is kept between restarts.
    data_dir: Path = Field(default=Path("/data"), validation_alias="THERMOHUB_DATA_DIR")
    # When set, every /api request must send it in the X-API-Key header.
    api_key: SecretStr | None = Field(default=None, validation_alias="THERMOHUB_API_KEY")

    @field_validator("cookidoo_email", "cookidoo_password", "api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # Unraid passes template fields left empty as empty strings.
        return value or None

    @property
    def token_file(self) -> Path:
        return self.data_dir / "cookidoo_token.json"

    @property
    def account_file(self) -> Path:
        return self.data_dir / "cookidoo_account.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
