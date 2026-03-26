from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    twilio_account_sid: str = Field(default="", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(default="", alias="TWILIO_AUTH_TOKEN")
    twilio_whatsapp_number: str = Field(
        default="whatsapp:+14155238886", alias="TWILIO_WHATSAPP_NUMBER"
    )
    # Sin path final; p. ej. https://tu-app.onrender.com — si la firma falla tras el fix de URL.
    twilio_webhook_base_url: str = Field(default="", alias="TWILIO_WEBHOOK_BASE_URL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    # Admin configuration
    # IMPORTANT: Change these default values in production!
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="changeme", alias="ADMIN_PASSWORD")
    # WARNING: The default secret key is insecure. Set ADMIN_SECRET_KEY
    # in your environment or .env file for production deployments.
    admin_secret_key: str = Field(
        default="change-this-secret-key-in-production",
        alias="ADMIN_SECRET_KEY",
    )

    # Catalog upload configuration
    catalog_upload_dir: str = Field(
        default="uploads",
        alias="CATALOG_UPLOAD_DIR",
    )
    catalog_max_file_size_mb: int = Field(
        default=50,
        alias="CATALOG_MAX_FILE_SIZE_MB",
    )
    catalog_allowed_extensions: str = Field(
        default=".pdf",
        alias="CATALOG_ALLOWED_EXTENSIONS",
    )

    model_config = {"env_file": ".env", "populate_by_name": True}

    @field_validator(
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_webhook_base_url",
        mode="before",
    )
    @classmethod
    def _strip_twilio_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @property
    def catalog_upload_path(self) -> Path:
        """Get the absolute path to the catalog upload directory."""
        path = Path(self.catalog_upload_dir)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def catalog_max_file_size_bytes(self) -> int:
        """Get the maximum file size in bytes."""
        return self.catalog_max_file_size_mb * 1024 * 1024

    @property
    def catalog_allowed_extensions_list(self) -> list[str]:
        """Get the list of allowed file extensions."""
        return [ext.strip().lower() for ext in self.catalog_allowed_extensions.split(",")]


settings = Settings()
