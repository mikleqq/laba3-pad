from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"
    user_service_url: str = "http://user-service:8000"
    access_token_expire_minutes: int = 60
    password_hash_iterations: int = 200_000

    # Bootstrap administrator (created on startup if missing).
    admin_email: str = "admin@example.com"
    admin_password: str = "Admin12345!"
    admin_full_name: str = "Shop Administrator"
