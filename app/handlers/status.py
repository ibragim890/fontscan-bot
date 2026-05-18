from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import get_or_create_user, get_profile_text

router = Router(name="status")


@router.message(Command("status"))
async def status_handler(message: Message, session: AsyncSession) -> None:
    user = await get_or_create_user(session, message.from_user)
    await message.answer(await get_profile_text(session, user))
