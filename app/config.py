"""Application configuration settings using Pydantic settings."""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_api_token: str = os.getenv("APP_API_TOKEN", "default_token")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "False").lower() in ("true", "1", "yes")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
