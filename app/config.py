from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    steam_creator_id: str
    poll_interval_minutes: int = 30

    # Where uploaded mod files live (shared Docker volume in prod).
    mod_files_dir: str = "/data/mod_files"
    # Hard cap per uploaded file. 500 MB default; tune later.
    max_upload_mb: int = 500

    # Optional Steam Web API key. With a key, GetPublishedFileDetails
    # (and the newer IPublishedFileService.GetDetails endpoint we now
    # use) consistently returns engagement counters that the
    # unauthenticated edge cache sometimes omits.
    steam_web_api_key: str = ""

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

    # Stripe — required for membership payments.
    # Use sk_test_... / whsec_test_... for local dev + Stripe test mode.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def secure_cookies(self) -> bool:
        return self.production


settings = Settings()
