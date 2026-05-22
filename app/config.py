from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    steam_creator_id: str
    poll_interval_minutes: int = 30

    # Google OAuth (optional — leave empty to disable login)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # When true, all cookies get Secure flag (HTTPS only). Cloudflare
    # Tunnel always terminates TLS, so any tunnel-backed deploy should
    # set PRODUCTION=true in its .env.
    production: bool = False

    # Canonical public URL — used to build absolute URLs in RSS feeds,
    # sitemaps, and JSON-LD instead of trusting the incoming Host header
    # (which an attacker could spoof to poison cached feed XML). Leave
    # blank in dev and we'll fall back to the request's host.
    # Example: "https://workshopmods.org"
    canonical_base: str = ""

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def secure_cookies(self) -> bool:
        return self.production


settings = Settings()
