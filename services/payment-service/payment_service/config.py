from shopcommon.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "payment-service"
    # Simulated payment provider: charges above this amount are declined.
    max_payment_amount: float = 5000.0
