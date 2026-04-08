from __future__ import annotations

from pydantic import BaseModel, field_validator


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
