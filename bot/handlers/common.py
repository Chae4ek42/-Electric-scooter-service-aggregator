"""Start / main-menu handlers."""

from __future__ import annotations

from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from bot.core.config import ADMIN_USERNAMES, SUPPORT_USER, COOPERATION_USER
from bot.ui.keyboards import main_menu_kb, support_kb
from bot.core.database import async_session
from bot.domain.models import User
from sqlalchemy import select

router = Router(name="common")

_WELCOME_VIDEO = (
    Path(__file__).resolve().parent.parent.parent / "media" / "IMG_4105.MOV"
)

_WELCOME_TEXT = (
    "Добро пожаловать в \n"
    "*Service Map*📍\n\n"
    "*Кто мы?*\n"
    "Сервис подбора и контроля ремонта электросамокатов. \n"
    "Ремонт без риска - только проверенные сервисы с гарантией.🔗\n\n"
    "*Что мы делаем?*\n"
    "Подберем проверенный сервис для ремонта электросамоката "
    "за 10 минут с гарантией результата по лучшей цене. "
    "Проконтролируем ремонт за вас🤝"
    "Мы решаем вашу проблему  под ключ. "
    "Вы оставляете заявку, мы подбираем сервис по вашему запросу "
    "и проблеме, контролируем весь процесс, даем гарантию. \n\n"
    "_Оставляй заявку прямо сейчас!_"
)


def _is_admin(username: str | None) -> bool:
    return bool(username) and username.lower() in ADMIN_USERNAMES


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    # Upsert user
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == message.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=message.from_user.id,
                username=message.from_user.username or "",
                full_name=message.from_user.full_name or "Unknown",
            )
            session.add(user)
            await session.commit()

    kb = main_menu_kb(is_admin=_is_admin(message.from_user.username))
    if _WELCOME_VIDEO.exists():
        await message.answer_video(
            FSInputFile(_WELCOME_VIDEO),
            caption=_WELCOME_TEXT,
            reply_markup=kb,
        )
    else:
        await message.answer(_WELCOME_TEXT, reply_markup=kb)


@router.message(F.text == "Поддержка")
async def cmd_support(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("Процедура прервана.")
    await message.answer(
        "Выберите тему обращения:",
        reply_markup=support_kb(SUPPORT_USER, COOPERATION_USER),
    )
