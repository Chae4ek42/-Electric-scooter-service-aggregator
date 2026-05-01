"""Start / main-menu handlers."""

from __future__ import annotations

from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from client_bot.core.config import ADMIN_USERNAMES, SUPPORT_USER, COOPERATION_USER
from client_bot.core.healthcheck import run_self_check
from client_bot.ui.keyboards import main_menu_kb, support_kb
from client_bot.core.database import async_session
from client_bot.domain.models import User
from client_bot.texts import Btn, Client
from sqlalchemy import select

router = Router(name="common")

_WELCOME_VIDEO = (
    Path(__file__).resolve().parent.parent.parent / "media" / "client_start.mp4"
)

_WELCOME_TEXT = Client.Common.WELCOME


def _is_admin(username: str | None) -> bool:
    return bool(username) and username.lower() in ADMIN_USERNAMES


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
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
            # width=720,
            # height=1280,
        )
    else:
        await message.answer(_WELCOME_TEXT, reply_markup=kb)


@router.message(F.text == Btn.SUPPORT)
async def cmd_support(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer(Client.PROCEDURE_INTERRUPTED)
    await message.answer(
        Client.Common.CHOOSE_SUPPORT_TOPIC,
        reply_markup=support_kb(SUPPORT_USER, COOPERATION_USER),
    )


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.username):
        await message.answer(Client.Common.UNAVAILABLE)
        return
    await state.clear()
    await message.answer(
        Client.Common.ADMIN_PANEL,
        reply_markup=main_menu_kb(is_admin=True),
    )


@router.message(Command("client"))
async def cmd_client(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        Client.Common.MAIN_MENU,
        reply_markup=main_menu_kb(is_admin=_is_admin(message.from_user.username)),
    )


@router.message(Command("health"))
async def cmd_health(message: types.Message) -> None:
    if not _is_admin(message.from_user.username):
        await message.answer(Client.Common.UNAVAILABLE)
        return

    await message.answer("Выполняю self-check инфраструктуры...")
    healthy, report = await run_self_check(
        source="client_bot_command",
        user_id=message.from_user.id,
    )
    title = "Self-check: OK" if healthy else "Self-check: DEGRADED"
    await message.answer(f"{title}\n\n{report}")
