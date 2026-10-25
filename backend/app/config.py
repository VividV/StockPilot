"""Environment-based configuration. Every setting can be overridden with an
environment variable of the same name (case-insensitive)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "StockPilot"
    ENVIRONMENT: str = "development"  # development | test | production

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stockpilot"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 14
    JWT_ALGORITHM: str = "HS256"

    # Comma-separated list of allowed browser origins, e.g. "https://a.com,https://b.com".
    # Kept as plain text: list-typed settings make pydantic-settings expect JSON.
    CORS_ORIGINS: str = "http://localhost:3000"
    COOKIE_SECURE: bool = False  # set True behind HTTPS

    # Public address of the web app, used in links inside emails.
    APP_URL: str = "http://localhost:3000"

    # Email (any SMTP service: Gmail/Google Workspace, Zoho, SendGrid, Mailgun, Postmark, Amazon SES…).
    # Leave SMTP_HOST empty in development: emails are then written to the worker log instead.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SECURITY: str = "starttls"  # starttls | ssl | none
    EMAIL_FROM: str = "StockPilot <no-reply@stockpilot.local>"

    # SMS and WhatsApp through Twilio (optional).
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_SMS_FROM: str = ""        # e.g. +15017122661 or a Messaging Service SID (MG…)
    TWILIO_WHATSAPP_FROM: str = ""   # e.g. +14155238886 (the WhatsApp-enabled number)

    # Uploaded files (delivery notes, invoices, photos). Use a persistent volume;
    # with several API replicas, mount the same shared volume on all of them.
    ATTACHMENTS_DIR: str = "./data/attachments"
    MAX_UPLOAD_MB: int = 10

    # Subscriptions (selling StockPilot to other businesses). Off = every company has
    # everything, no trial, no payments: right for running StockPilot for your own business.
    BILLING_ENABLED: bool = False
    PAYSTACK_SECRET_KEY: str = ""     # sk_test_… or sk_live_… from the Paystack dashboard
    PAYSTACK_API_URL: str = "https://api.paystack.co"  # only changed for testing
    BILLING_CURRENCY: str = "NGN"
    TRIAL_DAYS: int = 14
    GRACE_DAYS: int = 7               # days after a missed payment before the account becomes read-only
    # Monthly prices in BILLING_CURRENCY, e.g. "starter=15000,business=35000,pro=75000". Yearly = 10 x monthly.
    PLAN_PRICES: str = ""

    RATE_LIMIT_PER_MINUTE: int = 300
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10

    @property
    def cors_origins(self) -> list[str]:
        raw = self.CORS_ORIGINS.strip()
        if raw.startswith("["):  # also accept a JSON list
            import json
            return [str(o).strip() for o in json.loads(raw) if str(o).strip()]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def email_enabled(self) -> bool:
        return bool(self.SMTP_HOST)

    @property
    def sms_enabled(self) -> bool:
        return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN and self.TWILIO_SMS_FROM)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN and self.TWILIO_WHATSAPP_FROM)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.SECRET_KEY.startswith("change-me") or len(s.SECRET_KEY) < 32):
        raise RuntimeError("SECRET_KEY must be set to a long random value in production")
    return s
