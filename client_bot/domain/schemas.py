from __future__ import annotations

from pydantic import BaseModel, field_validator
import re


class MetroTextInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Введите хотя бы 2 символа для поиска станции метро")
        if len(v) > 100:
            raise ValueError("Слишком длинный ввод")
        return v


class ProblemDescription(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def check_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Описание слишком короткое (минимум 3 символа)")
        if len(v) > 1000:
            raise ValueError("Описание слишком длинное (максимум 1000 символов)")
        return v


class ModelNameInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_model_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError(
                "Пожалуйста, введите корректное название модели (минимум 2 символа)"
            )
        if len(v) > 150:
            raise ValueError("Название модели слишком длинное (максимум 150 символов)")
        if not any(c.isalpha() for c in v):
            raise ValueError("Пожалуйста, введите корректное название модели")
        return v


class BrandNameInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_brand_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Название бренда слишком короткое (минимум 2 символа)")
        if len(v) > 100:
            raise ValueError("Название бренда слишком длинное (максимум 100 символов)")
        if not any(c.isalpha() for c in v):
            raise ValueError("Пожалуйста, введите корректное название бренда")
        return v


class ServiceNameInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Минимум 3 символа")
        if len(v) > 200:
            raise ValueError("Максимум 200 символов")
        if not any(c.isalpha() for c in v):
            raise ValueError("Введите корректное название")
        return v


class AddressInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Адрес слишком короткий (минимум 10 символов)")
        if len(v) > 400:
            raise ValueError("Адрес слишком длинный (максимум 400 символов)")
        return v


class PhoneInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\+?[\d\- ]{7,20}$", v):
            raise ValueError("Неверный формат телефона")
        return v


class TelegramHandleInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not re.match(r"^[a-zA-Z0-9_]{5,32}$", v):
            raise ValueError("Неверный формат Telegram-хэндла")
        return v


class WorkHoursInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        m = re.match(r"^(\d{2}:\d{2})\s*[-\u2013]\s*(\d{2}:\d{2})$", v)
        if not m:
            raise ValueError("Формат: HH:MM-HH:MM (например 09:00-21:00)")

        start_raw, end_raw = m.group(1), m.group(2)

        def _to_minutes(part: str) -> int:
            hh_str, mm_str = part.split(":")
            hh = int(hh_str)
            mm = int(mm_str)
            if not (0 <= hh <= 23):
                raise ValueError("Часы должны быть в диапазоне 00-23")
            if not (0 <= mm <= 59):
                raise ValueError("Минуты должны быть в диапазоне 00-59")
            return hh * 60 + mm

        start_m = _to_minutes(start_raw)
        end_m = _to_minutes(end_raw)
        if start_m >= end_m:
            raise ValueError("Время открытия должно быть раньше времени закрытия")

        return f"{start_raw}-{end_raw}"


class DiagnosticsPriceInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        try:
            val = int(v)
        except ValueError:
            raise ValueError("Введите целое число (стоимость в рублях)")
        if val < 0:
            raise ValueError("Стоимость не может быть отрицательной")
        return v


class RejectReasonInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Причина слишком короткая (минимум 3 символа)")
        if len(v) > 500:
            raise ValueError("Причина слишком длинная (максимум 500 символов)")
        return v


class BankAccountInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{20}$", v):
            raise ValueError("Расчётный счёт должен содержать ровно 20 цифр")
        return v


class BankNameInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Минимум 3 символа")
        if len(v) > 200:
            raise ValueError("Максимум 200 символов")
        return v


class BikInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{9}$", v):
            raise ValueError("БИК должен содержать ровно 9 цифр")
        return v


class CorrAccountInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{20}$", v):
            raise ValueError("Корреспондентский счёт должен содержать ровно 20 цифр")
        return v


class OrgNameInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Минимум 3 символа")
        if len(v) > 300:
            raise ValueError("Максимум 300 символов")
        return v


class InnInput(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{10}(\d{2})?$", v):
            raise ValueError("ИНН должен содержать 10 или 12 цифр")
        return v
