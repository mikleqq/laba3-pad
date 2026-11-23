"""Reusable input validation rules (the web UI applies the same rules before sending).

Messages are in Russian because they are shown to the shop's customers as-is.
"""

from __future__ import annotations

import re
from typing import Annotated, Optional

from pydantic import AfterValidator, BeforeValidator

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .'\-]*$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()\-]+$")


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def validate_person_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    if not 2 <= len(value) <= 100:
        raise ValueError("Имя должно содержать от 2 до 100 символов")
    if not _NAME_RE.match(value):
        raise ValueError("Имя может содержать только буквы, пробелы, дефис и апостроф")
    return value


def validate_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not _PHONE_RE.match(value) or not 10 <= len(digits) <= 15:
        raise ValueError("Телефон должен содержать от 10 до 15 цифр, например +373 69 123 456")
    return value


def validate_password(value: str) -> str:
    if not 8 <= len(value) <= 128:
        raise ValueError("Пароль должен содержать от 8 до 128 символов")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", value) or not re.search(r"\d", value):
        raise ValueError("Пароль должен содержать хотя бы одну букву и одну цифру")
    if value.strip() != value:
        raise ValueError("Пароль не должен начинаться или заканчиваться пробелом")
    return value


def validate_address(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    if not 5 <= len(value) <= 255:
        raise ValueError("Адрес должен содержать от 5 до 255 символов")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", value):
        raise ValueError("Укажите улицу, дом и город")
    return value


PersonName = Annotated[str, BeforeValidator(_strip), AfterValidator(validate_person_name)]
Phone = Annotated[str, BeforeValidator(_strip), AfterValidator(validate_phone)]
Password = Annotated[str, AfterValidator(validate_password)]
Address = Annotated[str, BeforeValidator(_strip), AfterValidator(validate_address)]
OptionalComment = Annotated[Optional[str], BeforeValidator(lambda v: (_strip(v) or None) if v is not None else None)]
