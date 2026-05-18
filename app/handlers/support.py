from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.texts import paysupport_text, support_text, terms_text

router = Router(name="support")


@router.message(Command("paysupport"))
async def paysupport_handler(message: Message) -> None:
    await message.answer(paysupport_text())


@router.message(Command("support"))
async def support_handler(message: Message, session: AsyncSession) -> None:
    await message.answer(await support_text(session))


@router.message(Command("terms"))
async def terms_handler(message: Message, session: AsyncSession) -> None:
    await message.answer(await terms_text(session))
