from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting, Difficulty

DEFAULT_DIFFICULTY_KEY = "default_difficulty"


def get_default_difficulty(db: Session) -> Difficulty:
    value = get_setting(db, DEFAULT_DIFFICULTY_KEY) or get_settings().default_difficulty
    return _difficulty_from_value(value)


def set_default_difficulty(db: Session, difficulty: Difficulty) -> dict[str, str]:
    setting = _get_or_create_setting(db, DEFAULT_DIFFICULTY_KEY)
    setting.value = difficulty.value
    db.commit()
    return {"default_difficulty": difficulty.value}


def get_setting(db: Session, key: str) -> str | None:
    setting = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return setting.value if setting else None


def _get_or_create_setting(db: Session, key: str) -> AppSetting:
    setting = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if setting is not None:
        return setting
    setting = AppSetting(key=key, value="")
    db.add(setting)
    return setting


def _difficulty_from_value(value: str) -> Difficulty:
    try:
        return Difficulty(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Difficulty)
        raise ValueError(f"Invalid default difficulty. Expected one of: {allowed}") from exc
