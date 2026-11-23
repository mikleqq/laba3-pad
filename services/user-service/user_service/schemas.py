from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from shopcommon.validation import Address, PersonName, Phone


class ProfileCreate(BaseModel):
    id: int = Field(gt=0)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=100)


class ProfileUpdate(BaseModel):
    full_name: Optional[PersonName] = None
    phone: Optional[Phone] = None
    address: Optional[Address] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"full_name": "Алиса Иванова", "phone": "+373 69 123 456", "address": "Кишинёв, ул. Пушкина, 10"}
        }
    )


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    phone: Optional[str]
    address: Optional[str]
    created_at: datetime
    updated_at: datetime


class ProfilePage(BaseModel):
    items: list[ProfileOut]
    total: int
    limit: int
    offset: int
