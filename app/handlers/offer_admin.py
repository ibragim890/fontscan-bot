import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    get_launch_offer_hours_left,
    get_or_create_user,
    is_launch_offer_active,
    reset_launch_offer,
    start_launch_offer,
)
from app.config import settings
from app.models import AdminAccess, User

router = Router()
logger = logging.getLogger(__name__)


async def has_offer_admin_access(session: AsyncSession, telegram_id: int) -> bool:
    if telegram_id in settings.admin_id_set:
        return True

    result = await session.execute(
        select(AdminAccess).where(AdminAccess.telegram_id == telegram_id)
    )
    access = result.scalar_one_or_none()
    return bool(access and access.is_active)


def offer_debug_text(user: User) -> str:
    return (
        "Offer Debug\n\n"
        f"offer_active: {str(is_launch_offer_active(user)).lower()}\n"
        f"started_at: {user.launch_offer_started_at}\n"
        f"ends_at: {user.launch_offer_ends_at}\n"
        f"purchased: {str(bool(user.launch_offer_purchased)).lower()}\n"
        f"reminder_6h: {str(bool(user.launch_offer_reminder_6h_sent)).lower()}\n"
        f"reminder_12h: {str(bool(user.launch_offer_reminder_12h_sent)).lower()}\n"
        f"reminder_18h: {str(bool(user.launch_offer_reminder_18h_sent)).lower()}\n"
        f"reminder_24h: {str(bool(user.launch_offer_reminder_24h_sent)).lower()}\n"
        f"hours_left: {get_launch_offer_hours_left(user)}"
    )


@router.message(Command("debug_offer"))
@router.message(F.text == "/debug_offer")
async def debug_offer_handler(message: Message, session: AsyncSession) -> None:
    await message.answer("debug_offer handler reached")
    logger.info(
        "Offer admin command called: command=%s user_id=%s",
        "debug_offer",
        message.from_user.id,
    )

    if not await has_offer_admin_access(session, message.from_user.id):
        await message.answer("Нет доступа.")
        return

    user = await get_or_create_user(session, message.from_user)
    await message.answer(offer_debug_text(user))


@router.message(Command("start_my_offer"))
@router.message(F.text == "/start_my_offer")
async def start_my_offer_handler(message: Message, session: AsyncSession) -> None:
    await message.answer("start_my_offer handler reached")
    logger.info(
        "Offer admin command called: command=%s user_id=%s",
        "start_my_offer",
        message.from_user.id,
    )

    if not await has_offer_admin_access(session, message.from_user.id):
        await message.answer("Нет доступа.")
        return

    user = await get_or_create_user(session, message.from_user)
    start_launch_offer(user)
    await session.commit()
    await message.answer("Offer запущен на 24 часа.")


@router.message(Command("reset_my_offer"))
@router.message(F.text == "/reset_my_offer")
async def reset_my_offer_handler(message: Message, session: AsyncSession) -> None:
    await message.answer("reset_my_offer handler reached")
    logger.info(
        "Offer admin command called: command=%s user_id=%s",
        "reset_my_offer",
        message.from_user.id,
    )

    if not await has_offer_admin_access(session, message.from_user.id):
        await message.answer("Нет доступа.")
        return

    user = await get_or_create_user(session, message.from_user)
    reset_launch_offer(user)
    await session.commit()
    await message.answer("Offer сброшен.")
