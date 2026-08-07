from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import harden_file_permissions

# Same project-root-relative resolution as database.py's DB_PATH — matches
# what SettingsConfigDict's relative "env_file" below resolves to as long as
# the app is started from the project root (true for start-unix.sh /
# start-windows.bat / `streamlit run app.py` from a checkout).
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    app_env: str = "development"
    mock_adobe: bool = True
    adobe_write_enabled: bool = False
    allowed_email_domains: str = "example.com"
    default_country: str = "US"
    default_identity_type: str = "federatedID"
    cache_ttl_seconds: int = 600
    adobe_org_id: str = ""
    adobe_client_id: str = ""
    adobe_client_secret: str = ""
    adobe_scopes: str = "openid,AdobeID,user_management_sdk"
    adobe_ims_token_url: str = "https://ims-na1.adobelogin.com/ims/token/v3"
    adobe_umapi_base_url: str = "https://usermanagement.adobe.io/v2/usermanagement"
    adobe_http_timeout: float = 60.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_domains(self) -> set[str]:
        return {x.strip().lower() for x in self.allowed_email_domains.split(",") if x.strip()}

    @property
    def adobe_configured(self) -> bool:
        return all((self.adobe_org_id, self.adobe_client_id, self.adobe_client_secret, self.adobe_scopes))


settings = Settings()


def harden_env_file() -> None:
    """Restrict .env (holds the Adobe client secret) to the owning user only —
    mirrors how database.py hardens the SQLite DB and logging_setup.py hardens
    the log file. Called explicitly from app.py's startup, not at import time,
    so importing this module never has filesystem side effects on its own.
    Best-effort/POSIX-only — see harden_file_permissions()'s docstring."""
    if ENV_PATH.exists():
        harden_file_permissions(ENV_PATH)
