from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "product-service"
    seed_demo_data: bool = True
    low_stock_threshold: int = 3
