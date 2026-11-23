from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"
    default_channel: str = "email"
    sender_address: str = "no-reply@shophub.local"
