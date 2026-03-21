from pydantic_settings import BaseSettings
from pydantic import Field


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

    model_config = {"env_file": ".env", "populate_by_name": True}


settings = Settings()
