import logging
from datetime import timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import get_trial_config, now_utc, set_app_setting
from app.config import settings
from app.keyboards import (
    text_after_save_keyboard,
    text_category_keyboard,
    text_edit_keyboard,
    text_menu_keyboard,
)
from app.models import (
    AdminAccess,
    ApiKeyUsage,
    FontRequest,
    Payment,
    Tariff as TariffModel,
    User,
)
from app.payments import list_tariffs
from app.texts import (
    DEFAULT_BOT_TEXTS,
    get_bot_text_template,
    list_bot_texts,
    reset_bot_text,
    set_bot_text,
)

router = Router(name="admin")
logger = logging.getLogger(__name__)


class TextEditState(StatesGroup):
    waiting_text = State()


TEXT_CATEGORIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "main": ("Главное меню", [("main_menu", "Главное меню")]),
    "find": ("Узнать шрифт", [("find_font", "Узнать шрифт")]),
    "subscription": (
        "Подписка",
        [
            ("subscription_trial", "Trial"),
            ("subscription_no_access", "Нет доступа"),
            ("subscription_designer", "Designer"),
            ("subscription_studio", "Studio"),
        ],
    ),
    "profile": (
        "Профиль",
        [
            ("profile_trial", "Trial"),
            ("profile_no_access", "Нет доступа"),
            ("profile_designer", "Designer"),
            ("profile_studio", "Studio"),
        ],
    ),
    "no_access": ("No access", [("no_access", "No access")]),
    "result": (
        "Результат",
        [
            ("font_result_found", "Результат найден"),
            ("font_result_not_found", "Результат не найден"),
        ],
    ),
    "payment": (
        "После оплаты",
        [
            ("payment_success_designer", "Designer"),
            ("payment_success_studio", "Studio"),
        ],
    ),
    "support": ("Поддержка", [("support", "Поддержка")]),
    "terms": ("Условия", [("terms", "Условия")]),
}

TEXT_KEY_TO_CATEGORY = {
    key: category
    for category, (_title, items) in TEXT_CATEGORIES.items()
    for key, _item_title in items
}

ALL_TEXT_VARIABLES = [
    "trial_days",
    "trial_limit",
    "status",
    "days_left",
    "hours_left",
    "date",
    "remaining",
    "limit",
    "tariff",
    "price_designer",
    "limit_designer",
    "price_studio",
    "limit_studio",
    "font_name",
    "support_username",
]

TEXT_VARIABLES: dict[str, list[str]] = {
    "main_menu": ["trial_days", "trial_limit"],
    "find_font": [],
    "subscription_trial": [
        "days_left",
        "hours_left",
        "remaining",
        "limit",
        "price_designer",
        "limit_designer",
        "price_studio",
        "limit_studio",
    ],
    "subscription_no_access": [
        "price_designer",
        "limit_designer",
        "price_studio",
        "limit_studio",
    ],
    "subscription_designer": ["date", "remaining", "limit", "tariff", "status"],
    "subscription_studio": ["date", "remaining", "limit", "tariff", "status"],
    "profile_trial": ["days_left", "hours_left", "remaining", "limit", "status"],
    "profile_no_access": ["status"],
    "profile_designer": ["date", "days_left", "remaining", "limit", "tariff", "status"],
    "profile_studio": ["date", "days_left", "remaining", "limit", "tariff", "status"],
    "no_access": [
        "price_designer",
        "limit_designer",
        "price_studio",
        "limit_studio",
    ],
    "font_result_found": ["font_name"],
    "font_result_not_found": [],
    "payment_success_designer": ["date", "limit", "tariff"],
    "payment_success_studio": ["date", "limit", "tariff"],
    "support": ["support_username"],
    "terms": [],
}


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_set


async def get_admin_access(
    session: AsyncSession,
    telegram_id: int,
) -> AdminAccess | None:
    result = await session.execute(
        select(AdminAccess).where(AdminAccess.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def has_secret_stats_access(session: AsyncSession, telegram_id: int) -> bool:
    if is_admin(telegram_id):
        return True

    access = await get_admin_access(session, telegram_id)
    return bool(access and access.is_active)


async def get_or_create_admin_access(
    session: AsyncSession,
    telegram_id: int,
) -> AdminAccess:
    access = await get_admin_access(session, telegram_id)
    now = now_utc()
    if access is None:
        access = AdminAccess(
            telegram_id=telegram_id,
            granted_at=now,
            last_used_at=now,
            is_active=True,
        )
        session.add(access)
        await session.flush()
        return access

    access.granted_at = now
    access.last_used_at = now
    access.is_active = True
    return access


async def build_whatfontis_usage_text(
    session: AsyncSession,
    with_heading: bool = False,
) -> tuple[list[str], str]:
    today = now_utc().date()
    usage_rows_result = await session.execute(
        select(ApiKeyUsage).where(
            ApiKeyUsage.provider == "whatfontis",
            ApiKeyUsage.date == today,
        )
    )
    usage_by_key = {
        usage.key_index: usage for usage in usage_rows_result.scalars().all()
    }
    total_api_usage_today = sum(
        usage.requests_count for usage in usage_by_key.values()
    )

    usage_lines = []
    for index in range(1, len(settings.whatfontis_api_keys) + 1):
        usage = usage_by_key.get(index)
        requests_count = usage.requests_count if usage else 0
        status = "rate limited" if usage and usage.rate_limited else "active"
        usage_lines.append(f"Key {index}: {requests_count} requests, {status}")
    if not usage_lines:
        usage_lines.append("No WhatFontIs API keys configured")

    safety_limit = settings.daily_api_safety_limit
    if safety_limit:
        safety_status = (
            "reached" if total_api_usage_today >= safety_limit else "active"
        )
        safety_text = (
            f"Used today: {total_api_usage_today} / {safety_limit}\n"
            f"Status: {safety_status}"
        )
    else:
        safety_text = (
            f"Used today: {total_api_usage_today} / disabled\n"
            "Status: disabled"
        )

    if with_heading:
        safety_text = "API safety limit:\n" + safety_text

    return usage_lines, safety_text


async def build_admin_stats(session: AsyncSession) -> str:
    now = now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trial_config = await get_trial_config(session)

    total_users = await session.scalar(select(func.count(User.id)))
    active_trial = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_started_at.is_not(None),
            User.trial_ends_at > now,
            User.trial_requests_used < trial_config.requests_limit,
        )
    )
    active_paid = await session.scalar(
        select(func.count(User.id)).where(
            User.plan.in_(["designer", "studio"]),
            User.plan_ends_at > now,
        )
    )
    total_requests = await session.scalar(select(func.count(FontRequest.id)))
    successful_payments = await session.scalar(select(func.count(Payment.id)))
    today_requests = await session.scalar(
        select(func.count(FontRequest.id)).where(FontRequest.created_at >= today_start)
    )
    api_calls_today = await session.scalar(
        select(func.count(FontRequest.id)).where(
            FontRequest.created_at >= today_start,
            FontRequest.provider == "whatfontis",
            FontRequest.is_cached_response.is_(False),
        )
    )
    cache_hits_today = await session.scalar(
        select(func.count(FontRequest.id)).where(
            FontRequest.created_at >= today_start,
            FontRequest.is_cached_response.is_(True),
        )
    )
    usage_lines, safety_text = await build_whatfontis_usage_text(
        session,
        with_heading=True,
    )

    return (
        "Статистика\n"
        f"Всего пользователей: {total_users or 0}\n"
        f"Активных trial: {active_trial or 0}\n"
        f"Активных paid: {active_paid or 0}\n"
        f"Всего распознаваний: {total_requests or 0}\n"
        f"Всего успешных платежей: {successful_payments or 0}\n"
        f"Запросов за сегодня: {today_requests or 0}\n"
        f"API calls today: {api_calls_today or 0}\n"
        f"Cache hits today: {cache_hits_today or 0}\n\n"
        "WhatFontIs usage today:\n"
        + "\n".join(usage_lines)
        + "\n\n"
        + safety_text
    )


async def build_secret_stats(session: AsyncSession) -> str:
    now = now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_start = now - timedelta(days=7)
    trial_config = await get_trial_config(session)

    total_users = await session.scalar(select(func.count(User.id)))
    new_today = await session.scalar(
        select(func.count(User.id)).where(User.created_at >= today_start)
    )
    new_7_days = await session.scalar(
        select(func.count(User.id)).where(User.created_at >= seven_days_start)
    )
    active_trial = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_started_at.is_not(None),
            User.trial_ends_at > now,
            User.trial_requests_used < trial_config.requests_limit,
        )
    )
    trial_finished = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_started_at.is_not(None),
            or_(
                User.trial_ends_at <= now,
                User.trial_requests_used >= trial_config.requests_limit,
            ),
        )
    )
    active_designer = await session.scalar(
        select(func.count(User.id)).where(
            User.plan == "designer",
            User.plan_ends_at > now,
        )
    )
    active_studio = await session.scalar(
        select(func.count(User.id)).where(
            User.plan == "studio",
            User.plan_ends_at > now,
        )
    )
    paid_total = (active_designer or 0) + (active_studio or 0)

    requests_today = await session.scalar(
        select(func.count(FontRequest.id)).where(FontRequest.created_at >= today_start)
    )
    requests_7_days = await session.scalar(
        select(func.count(FontRequest.id)).where(
            FontRequest.created_at >= seven_days_start
        )
    )
    cache_hits_today = await session.scalar(
        select(func.count(FontRequest.id)).where(
            FontRequest.created_at >= today_start,
            FontRequest.is_cached_response.is_(True),
        )
    )
    api_calls_today = await session.scalar(
        select(func.count(FontRequest.id)).where(
            FontRequest.created_at >= today_start,
            FontRequest.provider == "whatfontis",
            FontRequest.is_cached_response.is_(False),
        )
    )

    payments_total = await session.scalar(select(func.count(Payment.id)))
    payments_today = await session.scalar(
        select(func.count(Payment.id)).where(Payment.created_at >= today_start)
    )
    stars_total = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_stars), 0))
    )
    stars_today = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_stars), 0)).where(
            Payment.created_at >= today_start
        )
    )
    usage_lines, safety_text = await build_whatfontis_usage_text(session)

    return (
        "Аналитика FontScan\n\n"
        "Пользователи:\n"
        f"Всего: {total_users or 0}\n"
        f"Новых сегодня: {new_today or 0}\n"
        f"Новых за 7 дней: {new_7_days or 0}\n\n"
        "Trial:\n"
        f"Активных trial: {active_trial or 0}\n"
        f"Trial закончился: {trial_finished or 0}\n\n"
        "Подписки:\n"
        f"Активных Designer: {active_designer or 0}\n"
        f"Активных Studio: {active_studio or 0}\n"
        f"Всего платных пользователей: {paid_total}\n\n"
        "Запросы:\n"
        f"Распознаваний сегодня: {requests_today or 0}\n"
        f"Распознаваний за 7 дней: {requests_7_days or 0}\n"
        f"Cache hits сегодня: {cache_hits_today or 0}\n"
        f"API calls сегодня: {api_calls_today or 0}\n\n"
        "Оплаты:\n"
        f"Платежей всего: {payments_total or 0}\n"
        f"Платежей сегодня: {payments_today or 0}\n"
        f"Legacy XTR всего: {stars_total or 0}\n"
        f"Legacy XTR сегодня: {stars_today or 0}\n\n"
        "WhatFontIs:\n"
        + "\n".join(usage_lines)
        + "\n\n"
        "Safety limit:\n"
        + safety_text
    )


@router.message(Command("admin_login"))
async def admin_login_handler(message: Message, session: AsyncSession) -> None:
    if not settings.admin_secret_enabled:
        await message.answer("Секретный доступ отключён.")
        return

    expected_code = settings.admin_secret_code.strip()
    if not expected_code:
        await message.answer("Секретный код не настроен.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Формат: /admin_login <код>")
        return

    if parts[1].strip() != expected_code:
        await message.answer("Неверный код.")
        return

    await get_or_create_admin_access(session, message.from_user.id)
    logger.info("Admin analytics access granted for %s", message.from_user.id)
    await message.answer(
        "Доступ к аналитике открыт.\n\n"
        "Команда: /secret_stats"
    )


@router.message(Command("secret_stats"))
async def secret_stats_handler(message: Message, session: AsyncSession) -> None:
    if not await has_secret_stats_access(session, message.from_user.id):
        await message.answer("Нет доступа.")
        return

    access = await get_admin_access(session, message.from_user.id)
    if access is not None and access.is_active:
        access.last_used_at = now_utc()

    await message.answer(await build_secret_stats(session))


@router.message(Command("admin_logout"))
async def admin_logout_handler(message: Message, session: AsyncSession) -> None:
    access = await get_admin_access(session, message.from_user.id)
    if access is None or not access.is_active:
        await message.answer("У вас нет активного доступа.")
        return

    access.is_active = False
    access.last_used_at = now_utc()
    logger.info("Admin analytics access closed for %s", message.from_user.id)
    await message.answer("Доступ к аналитике закрыт.")


def parse_command_args(message: Message, expected_count: int) -> list[str] | None:
    args = (message.text or "").split()[1:]
    return args if len(args) == expected_count else None


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


async def require_tariff_admin(message: Message, session: AsyncSession) -> bool:
    if await has_secret_stats_access(session, message.from_user.id):
        return True

    await message.answer("Нет доступа.")
    return False


def sqlite_database_path() -> Path | None:
    database_url = settings.database_url
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if database_url.startswith(prefix):
            raw_path = database_url.removeprefix(prefix)
            if raw_path in {":memory:", ""}:
                return None
            if raw_path.startswith("/"):
                return Path(raw_path)
            return Path(raw_path).resolve()
    return None


@router.message(Command("backup_db"))
async def backup_db_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    db_path = sqlite_database_path()
    if db_path is None:
        await message.answer("Backup доступен только для SQLite-файла.")
        return

    if not db_path.exists() or not db_path.is_file():
        await message.answer("Файл базы данных не найден.")
        return

    await message.answer_document(
        FSInputFile(db_path),
        caption="Backup базы данных.",
    )


async def require_text_admin_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> bool:
    if await has_secret_stats_access(session, callback.from_user.id):
        return True

    await callback.answer("Нет доступа.", show_alert=True)
    return False


def text_menu_categories() -> list[tuple[str, str]]:
    return [
        (category, title)
        for category, (title, _items) in TEXT_CATEGORIES.items()
    ]


def text_key_exists(key: str) -> bool:
    return key in DEFAULT_BOT_TEXTS


def text_key_category(key: str) -> str:
    return TEXT_KEY_TO_CATEGORY.get(key, "main")


def variables_text(key: str) -> str:
    variables = TEXT_VARIABLES.get(key, ALL_TEXT_VARIABLES)
    if not variables:
        return "нет"
    return ", ".join("{" + variable + "}" for variable in variables)


async def show_text_menu(message: Message) -> None:
    await message.answer(
        "Редактор текстов\n\n"
        "Выберите блок:",
        reply_markup=text_menu_keyboard(text_menu_categories()),
    )


async def edit_text_menu(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            "Редактор текстов\n\n"
            "Выберите блок:",
            reply_markup=text_menu_keyboard(text_menu_categories()),
        )
    except Exception:
        await callback.message.answer(
            "Редактор текстов\n\n"
            "Выберите блок:",
            reply_markup=text_menu_keyboard(text_menu_categories()),
        )


async def show_text_category(
    callback: CallbackQuery,
    category: str,
) -> None:
    category_data = TEXT_CATEGORIES.get(category)
    if category_data is None:
        await callback.answer("Блок не найден.", show_alert=True)
        return

    title, items = category_data
    await callback.message.edit_text(
        f"Тексты: {title}\n\n"
        "Выберите текст:",
        reply_markup=text_category_keyboard(items, category),
    )


async def show_text_edit(
    callback: CallbackQuery,
    session: AsyncSession,
    key: str,
) -> None:
    if not text_key_exists(key):
        await callback.answer("Текст не найден.", show_alert=True)
        return

    category = text_key_category(key)
    current_text = await get_bot_text_template(session, key)
    await callback.message.edit_text(
        f"Блок: {key}\n\n"
        "Текущий текст:\n\n"
        f"{current_text}\n\n"
        "Доступные переменные:\n"
        f"{variables_text(key)}",
        reply_markup=text_edit_keyboard(key, category),
    )


@router.message(Command("text_menu", "texts"))
async def text_menu_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return
    await show_text_menu(message)


@router.callback_query(F.data == "textmenu:main")
async def text_menu_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    await edit_text_menu(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("textcat:"))
async def text_category_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    category = callback.data.split(":", maxsplit=1)[1]
    await show_text_category(callback, category)
    await callback.answer()


@router.callback_query(F.data.startswith("text_back:"))
async def text_back_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    category = callback.data.split(":", maxsplit=1)[1]
    await show_text_category(callback, category)
    await callback.answer()


@router.callback_query(F.data.startswith("textedit:"))
async def text_edit_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    key = callback.data.split(":", maxsplit=1)[1]
    await show_text_edit(callback, session, key)
    await callback.answer()


@router.callback_query(F.data.startswith("text_change:"))
async def text_change_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    key = callback.data.split(":", maxsplit=1)[1]
    if not text_key_exists(key):
        await callback.answer("Текст не найден.", show_alert=True)
        return

    await state.set_state(TextEditState.waiting_text)
    await state.update_data(
        waiting_text_key=key,
        waiting_text_category=text_key_category(key),
    )
    await callback.message.answer(
        "Отправьте новый текст для блока:\n"
        f"{key}\n\n"
        "Можно использовать переменные:\n"
        f"{', '.join('{' + variable + '}' for variable in ALL_TEXT_VARIABLES)}\n\n"
        "Для отмены: /cancel_text"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("text_reset:"))
async def text_reset_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not await require_text_admin_callback(callback, session):
        return

    key = callback.data.split(":", maxsplit=1)[1]
    if not text_key_exists(key):
        await callback.answer("Текст не найден.", show_alert=True)
        return

    await reset_bot_text(session, key)
    await callback.message.answer(
        "Текст сброшен к стандартному.",
        reply_markup=text_after_save_keyboard(key, text_key_category(key)),
    )
    await callback.answer()


@router.message(Command("list_texts"))
async def list_texts_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    texts = await list_bot_texts(session)
    await message.answer(
        "Тексты:\n"
        + "\n".join(f"{text.key} — {text.title}" for text in texts)
    )


@router.message(Command("get_text"))
async def get_text_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 1)
    if args is None:
        await message.answer("Формат: /get_text <key>")
        return

    key = args[0]
    if not text_key_exists(key):
        await message.answer("Текст не найден.")
        return

    current_text = await get_bot_text_template(session, key)
    await message.answer(f"Блок: {key}\n\n{current_text}")


@router.message(Command("set_text"))
async def set_text_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 1)
    if args is None:
        await message.answer("Формат: /set_text <key>")
        return

    key = args[0]
    if not text_key_exists(key):
        await message.answer("Текст не найден.")
        return

    await state.set_state(TextEditState.waiting_text)
    await state.update_data(
        waiting_text_key=key,
        waiting_text_category=text_key_category(key),
    )
    await message.answer(
        "Отправьте новый текст для блока:\n"
        f"{key}\n\n"
        "Для отмены: /cancel_text"
    )


@router.message(Command("reset_text"))
async def reset_text_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 1)
    if args is None:
        await message.answer("Формат: /reset_text <key>")
        return

    key = args[0]
    if not text_key_exists(key):
        await message.answer("Текст не найден.")
        return

    await reset_bot_text(session, key)
    await message.answer("Текст сброшен к стандартному.")


@router.message(Command("cancel_text"))
async def cancel_text_handler(message: Message, state: FSMContext) -> None:
    if await state.get_state() == TextEditState.waiting_text.state:
        await state.clear()
        await message.answer("Редактирование отменено.")
        return

    await message.answer("Нет активного редактирования.")


@router.message(TextEditState.waiting_text)
async def save_waiting_text_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        await state.clear()
        return

    if not message.text:
        await message.answer("Отправьте текстовое сообщение.")
        return

    data = await state.get_data()
    key = data.get("waiting_text_key")
    category = data.get("waiting_text_category") or "main"
    if not isinstance(key, str) or not text_key_exists(key):
        await state.clear()
        await message.answer("Текст не найден.")
        return

    await set_bot_text(session, key, message.text)
    await state.clear()
    await message.answer(
        "Текст обновлён.",
        reply_markup=text_after_save_keyboard(key, str(category)),
    )


async def find_tariff_model(
    session: AsyncSession,
    code: str,
) -> TariffModel | None:
    result = await session.execute(
        select(TariffModel).where(TariffModel.code == code.lower().strip())
    )
    return result.scalar_one_or_none()


@router.message(Command("set_price"))
async def set_price_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 2)
    if args is None:
        await message.answer("Формат: /set_price <tariff> <price>")
        return

    tariff_code, raw_price = args
    price = parse_int(raw_price)
    if price is None or price <= 0 or price > 10_000:
        await message.answer("Цена должна быть целым числом от 1 до 10000.")
        return

    tariff = await find_tariff_model(session, tariff_code)
    if tariff is None:
        await message.answer("Тариф не найден.")
        return

    tariff.price_stars = price
    logger.info(
        "Admin %s set price for tariff=%s to %s",
        message.from_user.id,
        tariff.code,
        price,
    )
    await message.answer(
        f"Цена тарифа {tariff.title} обновлена:\n"
        f"{price} ₽"
    )


@router.message(Command("set_limit"))
async def set_limit_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 2)
    if args is None:
        await message.answer("Формат: /set_limit <tariff> <limit>")
        return

    tariff_code, raw_limit = args
    monthly_limit = parse_int(raw_limit)
    if monthly_limit is None or monthly_limit <= 0 or monthly_limit > 100_000:
        await message.answer("Лимит должен быть целым числом от 1 до 100000.")
        return

    tariff = await find_tariff_model(session, tariff_code)
    if tariff is None:
        await message.answer("Тариф не найден.")
        return

    tariff.monthly_limit = monthly_limit
    logger.info(
        "Admin %s set monthly limit for tariff=%s to %s",
        message.from_user.id,
        tariff.code,
        monthly_limit,
    )
    await message.answer(
        f"Лимит {tariff.title} обновлён:\n"
        f"{monthly_limit} распознаваний"
    )


@router.message(Command("set_trial_days"))
async def set_trial_days_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 1)
    if args is None:
        await message.answer("Формат: /set_trial_days <days>")
        return

    days = parse_int(args[0])
    if days is None or days <= 0 or days > 365:
        await message.answer("Количество дней должно быть целым числом от 1 до 365.")
        return

    await set_app_setting(session, "trial_days", days)
    logger.info("Admin %s set trial days to %s", message.from_user.id, days)
    await message.answer(
        "Длительность Trial обновлена:\n"
        f"{days} д."
    )


@router.message(Command("set_trial_limit"))
async def set_trial_limit_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 1)
    if args is None:
        await message.answer("Формат: /set_trial_limit <limit>")
        return

    trial_limit = parse_int(args[0])
    if trial_limit is None or trial_limit <= 0 or trial_limit > 100_000:
        await message.answer("Лимит должен быть целым числом от 1 до 100000.")
        return

    await set_app_setting(session, "trial_requests_limit", trial_limit)
    logger.info(
        "Admin %s set trial requests limit to %s",
        message.from_user.id,
        trial_limit,
    )
    await message.answer(
        "Лимит Trial обновлён:\n"
        f"{trial_limit} распознаваний"
    )


@router.message(Command("tariffs"))
async def tariffs_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    tariffs = await list_tariffs(session)
    tariff_blocks = [
        (
            f"{tariff.title}:\n"
            f"Цена: {tariff.price_stars} ₽\n"
            f"Лимит: {tariff.monthly_limit}"
        )
        for tariff in tariffs
    ]
    tariff_text = "\n\n".join(tariff_blocks) or "Тарифы не настроены."
    await message.answer(tariff_text)


@router.message(Command("admin_stats"))
async def admin_stats_handler(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Недоступно.")
        return

    await message.answer(await build_admin_stats(session))


@router.message(Command("reset_limits"))
async def reset_limits_handler(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    result = await session.execute(
        update(User).values(
            trial_requests_used=0,
            monthly_requests_used=0,
        )
    )
    count = result.rowcount or 0
    await session.commit()
    logger.info(
        "Admin %s reset limits for %s users",
        message.from_user.id,
        count,
    )
    await message.answer(
        "Лимиты сброшены.\n\n"
        f"Пользователей обновлено: {count}"
    )


@router.message(Command("reset_trials"))
async def reset_trials_handler(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    result = await session.execute(
        update(User).values(
            trial_started_at=None,
            trial_ends_at=None,
            trial_requests_used=0,
            monthly_requests_used=0,
        )
    )
    count = result.rowcount or 0
    await session.commit()
    logger.info(
        "Admin %s reset trials for %s users",
        message.from_user.id,
        count,
    )
    await message.answer(
        "Trial сброшен.\n\n"
        f"Пользователей обновлено: {count}"
    )


@router.callback_query(F.data == "admin:stats")
async def admin_stats_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Недоступно.", show_alert=True)
        return

    await callback.message.answer(await build_admin_stats(session))
    await callback.answer()
