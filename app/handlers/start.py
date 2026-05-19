import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    get_main_menu_text,
    get_or_create_user,
    get_profile_text,
    get_subscription_text,
    user_has_active_paid_plan,
)
from app.keyboards import (
    back_to_menu_keyboard,
    main_menu_keyboard,
    profile_keyboard,
    subscription_menu_keyboard,
)
from app.texts import get_bot_text

router = Router(name="start")
logger = logging.getLogger(__name__)


async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logger.debug("Failed to edit menu message: %s", exc.__class__.__name__)
        await callback.message.answer(text, reply_markup=reply_markup)


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession) -> None:
    await get_or_create_user(session, message.from_user)
    await message.answer(
        await get_main_menu_text(session),
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:find_font")
async def find_font_menu_handler(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await edit_or_answer(
        callback,
        await get_bot_text(session, "find_font"),
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "result:find_again")
async def find_again_handler(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await edit_or_answer(
        callback,
        await get_bot_text(session, "find_font"),
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def main_menu_callback_handler(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await edit_or_answer(
        callback,
        await get_main_menu_text(session),
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu_handler(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    user = await get_or_create_user(session, callback.from_user)
    await edit_or_answer(
        callback,
        await get_subscription_text(session, user),
        reply_markup=subscription_menu_keyboard(
            user_has_active_paid_plan(user)
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def profile_menu_handler(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    user = await get_or_create_user(session, callback.from_user)
    await edit_or_answer(
        callback,
        await get_profile_text(session, user),
        reply_markup=profile_keyboard(),
    )
    await callback.answer()
