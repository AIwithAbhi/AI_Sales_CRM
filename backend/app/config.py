import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "AI Sales"
    API_V1_STR: str = "/api"
    
    # Database Settings
    # Using local user.db sqlite
    DATABASE_URL: str = "sqlite+aiosqlite:///../users.db"
    
    # JWT Authentication Settings
    JWT_SECRET_KEY: str = "7b5b5fde7b68ee1005b5fde7b68ee1007b5b5fde7b68ee100"  # Hex key, change in production
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # External APIs
    FIRECRAWL_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    NVIDIA_MODEL: str = "meta/llama-3.1-8b-instruct"
    
    # Airtable Configuration
    AIRTABLE_API_KEY: str = ""
    AIRTABLE_BASE_ID: str = ""
    AIRTABLE_TABLE_NAME: str = "Leads"
    AIRTABLE_FIELD_NAME: str = "Company Data"
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    
    # SMTP Email configuration (for reports)
    EMAIL_SENDER: Optional[str] = None
    EMAIL_PASSWORD: Optional[str] = None
    EMAIL_RECIPIENT: Optional[str] = None
    EMAIL_SMTP_HOST: str = "smtp.gmail.com"
    EMAIL_SMTP_PORT: int = 587

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
