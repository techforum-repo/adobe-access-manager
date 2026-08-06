from pydantic_settings import BaseSettings, SettingsConfigDict


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
    adobe_user_sync_max_pages: int = 10
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_domains(self) -> set[str]:
        return {x.strip().lower() for x in self.allowed_email_domains.split(",") if x.strip()}

    @property
    def adobe_configured(self) -> bool:
        return all((self.adobe_org_id, self.adobe_client_id, self.adobe_client_secret, self.adobe_scopes))

    @property
    def adobe_user_sync_max_pages_effective(self) -> int | None:
        """None means "no cap" — client.list_users() fetches every page Adobe
        has rather than stopping at a fixed count. Set
        ADOBE_USER_SYNC_MAX_PAGES=0 in .env to enable this."""
        return None if self.adobe_user_sync_max_pages <= 0 else self.adobe_user_sync_max_pages


settings = Settings()
