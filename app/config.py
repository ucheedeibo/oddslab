from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    football_data_token: str = ""      # football-data.org token (fixtures and results)
    api_football_key: str = ""         # old provider, no longer used
    app_api_token: str = ""
    allowed_origins: str = ""          # reserved; app is same-origin so no CORS is enabled
    display_timezone: str = "Africa/Lagos"
    cookie_secure: bool = True         # set COOKIE_SECURE=false only for local http testing
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
