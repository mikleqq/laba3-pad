from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "order-service"
    product_service_url: str = "http://product-service:8000"
