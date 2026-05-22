from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    steam_creator_id: str
    poll_interval_minutes: int = 30


settings = Settings()
