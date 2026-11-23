from typing import Optional

from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"
    user_service_url: str = "http://user-service:8000"
    access_token_expire_minutes: int = 60
    password_hash_iterations: int = 200_000

    # Bootstrap administrator (created on startup if missing). Only when both are set;
    # the password comes from a secret, never from the code.
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None
    admin_full_name: str = "Shop Administrator"
