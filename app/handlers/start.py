import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    get_or_create_user,
    get_subscription_text,
    user_has_current_paid_subscription,
)
from app.keyboards import (
    back_to_menu_keyboard,
    main_menu_keyboard,
    subscription_menu_keyboard,
)
from app.texts import (
    FIND_FONT_TEXT,
    MAIN_MENU_TEXT,
)

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
    await message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "menu:find_font")
async def find_font_menu_handler(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        FIND_FONT_TEXT,
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "result:find_again")
async def find_again_handler(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        FIND_FONT_TEXT,
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def main_menu_callback_handler(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        MAIN_MENU_TEXT,
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
        get_subscription_text(user),
        reply_markup=subscription_menu_keyboard(
            user_has_current_paid_subscription(user)
        ),
    )
    await callback.answer()
