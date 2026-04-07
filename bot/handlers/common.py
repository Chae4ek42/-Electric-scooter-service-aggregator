"""Start / main-menu handlers."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from bot.core.config import ADMIN_USERNAMES, SUPPORT_USER
from bot.ui.keyboards import main_menu_kb, support_kb
from bot.core.database import async_session
from bot.domain.models import User
from sqlalchemy import select

router = Router(name="common")


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

    await message.answer(
        "Добро пожаловать в сервис ESAS!\nВыберите действие из меню ниже.",
        reply_markup=main_menu_kb(is_admin=_is_admin(message.from_user.username)),
    )


@router.message(F.text == "Техподдержка")
async def cmd_support(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("Процедура прервана.")
    await message.answer(
        "Техническая поддержка:",
        reply_markup=support_kb(SUPPORT_USER),
    )
