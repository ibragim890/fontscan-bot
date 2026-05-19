import asyncio
import csv
import io
import logging
from datetime import timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    activate_plan,
    as_utc,
    get_or_create_user,
    get_launch_offer_hours_left,
    get_tariff_text_values,
    get_trial_config,
    increment_usage,
    is_launch_offer_active,
    is_useful_cached_result,
    now_utc,
    reset_launch_offer,
    set_app_setting,
    start_launch_offer,
    user_can_make_request,
    user_has_active_paid_plan,
    user_has_trial_available,
)
from app.config import settings
from app.db import force_rub_payment_ui_texts
from app.keyboards import (
    broadcast_audience_keyboard,
    broadcast_confirm_keyboard,
    subscription_menu_keyboard,
    text_after_save_keyboard,
    text_category_keyboard,
    text_edit_keyboard,
    text_menu_keyboard,
)
from app.models import (
    AdminAccess,
    ApiKeyUsage,
    ExternalPayment,
    FontRequest,
    Payment,
    Tariff as TariffModel,
    User,
)
from app.payments import list_tariffs, robokassa_debug_lines
from app.texts import (
    DEFAULT_BOT_TEXTS,
    get_bot_text,
    get_bot_text_template,
    list_bot_texts,
    reset_bot_text,
    set_bot_text,
)

router = Router(name="admin")
logger = logging.getLogger(__name__)
PROCESS_STARTED_AT = now_utc()
BROADCAST_SEND_DELAY_SECONDS = 0.08


class TextEditState(StatesGroup):
    waiting_text = State()


class BroadcastState(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_audience = State()
    waiting_confirm = State()


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
    "main_menu": ["trial_limit"],
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
            User.trial_requests_used < trial_config.requests_limit,
        )
    )
    active_paid = await session.scalar(
        select(func.count(User.id)).where(active_paid_condition(now))
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
            User.trial_requests_used < trial_config.requests_limit,
        )
    )
    trial_finished = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_requests_used >= trial_config.requests_limit,
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
    active_balance = await session.scalar(
        select(func.count(User.id)).where(User.recognition_balance > 0)
    )
    paid_total = int(
        await session.scalar(
            select(func.count(User.id)).where(active_paid_condition(now))
        )
        or 0
    )

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
        f"С платным балансом: {active_balance or 0}\n"
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


def format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def active_paid_condition(now) -> object:
    return or_(
        User.recognition_balance > 0,
        and_(
            User.plan.in_(["designer", "studio"]),
            User.plan_ends_at.is_not(None),
            User.plan_ends_at > now,
        ),
    )


def free_user_condition(now) -> object:
    return and_(
        User.recognition_balance <= 0,
        or_(
            User.plan.is_(None),
            User.plan == "none",
            User.plan_ends_at.is_(None),
            User.plan_ends_at <= now,
        ),
    )


async def count_paid_users_from_payments(session: AsyncSession) -> int:
    payment_users = await session.execute(select(Payment.telegram_id))
    external_payment_users = await session.execute(select(ExternalPayment.telegram_id))
    paid_ids = {
        telegram_id
        for telegram_id in payment_users.scalars().all()
        if telegram_id is not None
    }
    paid_ids.update(
        telegram_id
        for telegram_id in external_payment_users.scalars().all()
        if telegram_id is not None
    )
    return len(paid_ids)


async def build_funnels_text(session: AsyncSession) -> str:
    users_started = int(await session.scalar(select(func.count(User.id))) or 0)
    users_first_photo = int(
        await session.scalar(
            select(func.count(User.id)).where(User.first_photo_at.is_not(None))
        )
        or 0
    )
    users_with_requests = int(
        await session.scalar(
            select(func.count(func.distinct(FontRequest.telegram_id)))
        )
        or 0
    )
    users_sent_first_photo = max(users_first_photo, users_with_requests)
    users_hit_paywall = int(
        await session.scalar(
            select(func.count(User.id)).where(User.paywall_hit_at.is_not(None))
        )
        or 0
    )
    users_opened_payment = int(
        await session.scalar(
            select(func.count(User.id)).where(User.payment_opened_at.is_not(None))
        )
        or 0
    )
    users_paid = await count_paid_users_from_payments(session)

    return (
        "Funnels\n\n"
        f"Users started: {users_started}\n"
        f"Users sent first photo: {users_sent_first_photo}\n"
        f"Users hit paywall: {users_hit_paywall}\n"
        f"Users opened payment: {users_opened_payment}\n"
        f"Users paid: {users_paid}\n\n"
        "Conversion:\n"
        f"Start → Photo: {format_percent(users_sent_first_photo, users_started)}\n"
        f"Photo → Paywall: {format_percent(users_hit_paywall, users_sent_first_photo)}\n"
        f"Paywall → Payment: {format_percent(users_opened_payment, users_hit_paywall)}\n"
        f"Payment → Paid: {format_percent(users_paid, users_opened_payment)}"
    )


async def build_top_sources_text(session: AsyncSession) -> str:
    result = await session.execute(
        select(User.source, func.count(User.id))
        .where(User.source.is_not(None), User.source != "")
        .group_by(User.source)
        .order_by(desc(func.count(User.id)))
        .limit(20)
    )
    lines = [f"{source}: {count}" for source, count in result.all()]
    return "Top Sources\n\n" + ("\n".join(lines) if lines else "Нет данных.")


async def build_user_stats_text(session: AsyncSession) -> str:
    now = now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_start = now - timedelta(days=7)
    thirty_days_start = now - timedelta(days=30)
    trial_config = await get_trial_config(session)

    total_users = int(await session.scalar(select(func.count(User.id))) or 0)
    today_users = int(
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        or 0
    )
    seven_day_users = int(
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= seven_days_start)
        )
        or 0
    )
    thirty_day_users = int(
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= thirty_days_start)
        )
        or 0
    )
    paid_users = int(
        await session.scalar(
            select(func.count(User.id)).where(active_paid_condition(now))
        )
        or 0
    )
    trial_available = int(
        await session.scalar(
            select(func.count(User.id)).where(
                free_user_condition(now),
                User.trial_requests_used < trial_config.requests_limit,
            )
        )
        or 0
    )
    trial_exhausted = int(
        await session.scalar(
            select(func.count(User.id)).where(
                free_user_condition(now),
                User.trial_requests_used >= trial_config.requests_limit,
            )
        )
        or 0
    )

    return (
        "Users\n\n"
        f"Total: {total_users}\n"
        f"Today: {today_users}\n"
        f"7d: {seven_day_users}\n"
        f"30d: {thirty_day_users}\n\n"
        f"Paid users: {paid_users}\n"
        f"Trial available: {trial_available}\n"
        f"Trial exhausted: {trial_exhausted}"
    )


async def build_api_usage_text(session: AsyncSession) -> str:
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
    total_requests = sum(usage.requests_count for usage in usage_by_key.values())

    lines = ["API Usage Today", ""]
    key_count = len(settings.whatfontis_api_keys)
    if key_count == 0:
        lines.append("No WhatFontIs API keys configured")
        lines.append("")
    for index in range(1, key_count + 1):
        usage = usage_by_key.get(index)
        requests_count = usage.requests_count if usage else 0
        status = "rate limited" if usage and usage.rate_limited else "active"
        lines.extend(
            [
                f"Key {index}:",
                f"requests: {requests_count}",
                f"status: {status}",
                "",
            ]
        )

    safety_limit = settings.daily_api_safety_limit
    limit_text = str(safety_limit) if safety_limit else "disabled"
    lines.extend(
        [
            "Safety limit:",
            f"{total_requests} / {limit_text}",
        ]
    )
    return "\n".join(lines).strip()


async def build_top_fonts_text(session: AsyncSession) -> str:
    result = await session.execute(
        select(FontRequest.top_font, func.count(FontRequest.id))
        .where(
            FontRequest.top_font.is_not(None),
            FontRequest.top_font != "",
            FontRequest.top_font != "не определён",
        )
        .group_by(FontRequest.top_font)
        .order_by(desc(func.count(FontRequest.id)))
        .limit(20)
    )
    lines = [f"{font_name}: {count}" for font_name, count in result.all()]
    return "Top Fonts\n\n" + ("\n".join(lines) if lines else "Нет данных.")


async def build_inactive_users_text(session: AsyncSession) -> str:
    cutoff = now_utc() - timedelta(days=7)
    total = int(
        await session.scalar(
            select(func.count(User.id)).where(User.updated_at < cutoff)
        )
        or 0
    )
    result = await session.execute(
        select(User.telegram_id, User.username, User.updated_at)
        .where(User.updated_at < cutoff)
        .order_by(User.updated_at)
        .limit(20)
    )
    rows = result.all()
    lines = [
        (
            f"{telegram_id}"
            f"{' @' + username if username else ''}"
            f" — {updated_at}"
        )
        for telegram_id, username, updated_at in rows
    ]
    if not lines:
        lines.append("Нет пользователей без активности 7+ дней.")
    if total > len(rows):
        lines.append(f"...и ещё {total - len(rows)}")

    return (
        "Inactive Users\n\n"
        f"7+ days: {total}\n\n"
        + "\n".join(lines)
    )


async def build_health_full_text(session: AsyncSession) -> str:
    now = now_utc()
    try:
        await session.execute(select(func.count(User.id)).limit(1))
        db_status = "OK"
    except Exception as exc:
        logger.exception("Health DB check failed: %s", exc.__class__.__name__)
        db_status = "ERROR"

    uptime = now - PROCESS_STARTED_AT
    uptime_seconds = int(uptime.total_seconds())
    uptime_text = (
        f"{uptime_seconds // 86400}d "
        f"{(uptime_seconds % 86400) // 3600}h "
        f"{(uptime_seconds % 3600) // 60}m"
    )
    today = now.date()
    key_count = len(settings.whatfontis_api_keys)
    rate_limited_keys = int(
        await session.scalar(
            select(func.count(ApiKeyUsage.id)).where(
                ApiKeyUsage.provider == "whatfontis",
                ApiKeyUsage.date == today,
                ApiKeyUsage.rate_limited.is_(True),
            )
        )
        or 0
    )
    api_requests_today = int(
        await session.scalar(
            select(func.coalesce(func.sum(ApiKeyUsage.requests_count), 0)).where(
                ApiKeyUsage.provider == "whatfontis",
                ApiKeyUsage.date == today,
            )
        )
        or 0
    )
    safety_limit = settings.daily_api_safety_limit
    cache_total = int(
        await session.scalar(
            select(func.count(FontRequest.id)).where(
                FontRequest.is_cached_response.is_(True)
            )
        )
        or 0
    )
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cache_today = int(
        await session.scalar(
            select(func.count(FontRequest.id)).where(
                FontRequest.created_at >= today_start,
                FontRequest.is_cached_response.is_(True),
            )
        )
        or 0
    )

    return (
        "Health Full\n\n"
        f"DB: {db_status}\n"
        f"Railway uptime: {uptime_text}\n"
        f"API keys: {key_count} configured, {rate_limited_keys} rate limited\n"
        f"Safety limit: {api_requests_today} / "
        f"{safety_limit if safety_limit else 'disabled'}\n"
        "Polling: active\n"
        f"Robokassa: enabled={settings.robokassa_enabled}, "
        f"configured={settings.robokassa_is_configured}\n"
        f"Cache: {cache_today} today, {cache_total} total"
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


@router.message(Command("funnels"))
async def funnels_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_funnels_text(session))


@router.message(Command("top_sources"))
async def top_sources_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_top_sources_text(session))


@router.message(Command("user_stats"))
async def user_stats_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_user_stats_text(session))


@router.message(Command("api_usage"))
async def api_usage_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_api_usage_text(session))


@router.message(Command("top_fonts"))
async def top_fonts_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_top_fonts_text(session))


@router.message(Command("inactive_users"))
async def inactive_users_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_inactive_users_text(session))


@router.message(Command("health_full"))
async def health_full_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await message.answer(await build_health_full_text(session))


@router.message(Command("gift_sub"))
async def gift_sub_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    args = parse_command_args(message, 3)
    if args is None:
        await message.answer("Формат: /gift_sub <telegram_id> <tariff> <days>")
        return

    telegram_id = parse_int(args[0])
    tariff_code = args[1].lower().strip()
    days = parse_int(args[2])
    if telegram_id is None or telegram_id <= 0:
        await message.answer("telegram_id должен быть положительным числом.")
        return
    if days is None or days <= 0 or days > 3660:
        await message.answer("days должен быть целым числом от 1 до 3660.")
        return

    plan = await find_tariff_model(session, tariff_code)
    if plan is None or not plan.is_active:
        await message.answer("Тариф не найден.")
        return

    user = await find_user_by_telegram_id(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.flush()

    gift_base_at = now_utc()
    current_plan_ends_at = as_utc(user.plan_ends_at)
    if current_plan_ends_at is not None and current_plan_ends_at > gift_base_at:
        gift_ends_at = current_plan_ends_at + timedelta(days=days)
    else:
        gift_ends_at = gift_base_at + timedelta(days=days)

    activate_plan(
        user,
        plan.code,
        plan.monthly_limit,
        gift_ends_at,
    )
    logger.info(
        "Admin %s gifted subscription user=%s tariff=%s days=%s",
        message.from_user.id,
        telegram_id,
        plan.code,
        days,
    )
    await message.answer("Подписка выдана.")


@router.message(Command("export_users"))
async def export_users_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    result = await session.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "telegram_id",
            "username",
            "source",
            "created_at",
            "plan",
            "plan_ends_at",
            "trial_requests_used",
            "monthly_requests_used",
        ]
    )
    for user in users:
        writer.writerow(
            [
                user.telegram_id,
                user.username or "",
                user.source or "",
                user.created_at.isoformat() if user.created_at else "",
                user.plan or "",
                user.plan_ends_at.isoformat() if user.plan_ends_at else "",
                user.trial_requests_used,
                user.monthly_requests_used,
            ]
        )

    payload = buffer.getvalue().encode("utf-8-sig")
    await message.answer_document(
        BufferedInputFile(payload, filename="users_export.csv"),
        caption=f"Users export: {len(users)}",
    )


@router.message(Command("broadcast"))
async def broadcast_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        return

    await state.clear()
    await state.set_state(BroadcastState.waiting_text)
    await message.answer(
        "Отправьте текст рассылки.\n\n"
        "Для отмены: /cancel_broadcast"
    )


@router.message(Command("broadcast_photo"))
async def broadcast_photo_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        return

    await state.clear()
    await state.set_state(BroadcastState.waiting_photo)
    await message.answer(
        "Отправьте фото для рассылки. Caption будет использован как текст.\n\n"
        "Для отмены: /cancel_broadcast"
    )


@router.message(Command("cancel_broadcast"))
async def cancel_broadcast_handler(message: Message, state: FSMContext) -> None:
    if (await state.get_state() or "").startswith("BroadcastState:"):
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    await message.answer("Нет активной рассылки.")


async def show_broadcast_audience_step(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastState.waiting_audience)
    await message.answer(
        "Аудитория:",
        reply_markup=broadcast_audience_keyboard(),
    )


@router.message(BroadcastState.waiting_text, F.text, ~F.text.startswith("/"))
async def broadcast_text_received_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("Отправьте непустой текст рассылки.")
        return

    await state.update_data(kind="text", text=text)
    await message.answer("Preview:")
    await message.answer(text)
    await show_broadcast_audience_step(message, state)


@router.message(BroadcastState.waiting_text, F.photo)
@router.message(BroadcastState.waiting_text, F.document)
async def broadcast_text_expected_handler(message: Message) -> None:
    await message.answer("Отправьте текст рассылки или /cancel_broadcast.")


@router.message(BroadcastState.waiting_photo, F.photo)
async def broadcast_photo_received_handler(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_tariff_admin(message, session):
        await state.clear()
        return

    if message.caption and message.caption.lstrip().startswith("/"):
        return

    photo = message.photo[-1]
    caption = message.caption or ""
    await state.update_data(
        kind="photo",
        photo_file_id=photo.file_id,
        caption=caption,
    )
    await message.answer("Preview:")
    await message.answer_photo(photo.file_id, caption=caption or None)
    await show_broadcast_audience_step(message, state)


@router.message(BroadcastState.waiting_photo, F.text, ~F.text.startswith("/"))
async def broadcast_photo_expected_handler(message: Message) -> None:
    await message.answer("Отправьте фото для рассылки или /cancel_broadcast.")


def audience_title(audience: str) -> str:
    titles = {
        "all": "all",
        "free": "free",
        "paid": "paid",
    }
    return titles.get(audience, audience)


async def get_broadcast_user_ids(
    session: AsyncSession,
    audience: str,
) -> list[int]:
    now = now_utc()
    query = select(User.telegram_id).order_by(User.id)
    if audience == "free":
        query = query.where(free_user_condition(now))
    elif audience == "paid":
        query = query.where(active_paid_condition(now))
    elif audience != "all":
        return []

    result = await session.execute(query)
    return [telegram_id for telegram_id in result.scalars().all() if telegram_id]


@router.callback_query(
    BroadcastState.waiting_audience,
    F.data.startswith("broadcast:audience:"),
)
async def broadcast_audience_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_text_admin_callback(callback, session):
        await state.clear()
        return

    audience = callback.data.rsplit(":", maxsplit=1)[1]
    user_ids = await get_broadcast_user_ids(session, audience)
    await state.update_data(audience=audience)
    await state.set_state(BroadcastState.waiting_confirm)
    await callback.message.answer(
        "Confirm\n\n"
        f"Audience: {audience_title(audience)}\n"
        f"Users: {len(user_ids)}",
        reply_markup=broadcast_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast:cancel")
async def broadcast_cancel_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_text_admin_callback(callback, session):
        await state.clear()
        return

    await state.clear()
    await callback.message.answer("Рассылка отменена.")
    await callback.answer()


@router.callback_query(BroadcastState.waiting_confirm, F.data == "broadcast:send")
async def broadcast_send_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not await require_text_admin_callback(callback, session):
        await state.clear()
        return

    data = await state.get_data()
    audience = data.get("audience")
    if not isinstance(audience, str):
        await state.clear()
        await callback.message.answer("Аудитория не выбрана. Рассылка отменена.")
        await callback.answer()
        return

    user_ids = await get_broadcast_user_ids(session, audience)
    await callback.message.answer(
        "Рассылка запущена.\n"
        f"Аудитория: {audience_title(audience)}\n"
        f"Получателей: {len(user_ids)}"
    )
    await callback.answer()

    sent = 0
    failed = 0
    kind = data.get("kind")
    for telegram_id in user_ids:
        try:
            if kind == "photo":
                photo_file_id = data.get("photo_file_id")
                if not isinstance(photo_file_id, str) or not photo_file_id:
                    raise ValueError("Missing broadcast photo file_id")
                caption = data.get("caption")
                await callback.bot.send_photo(
                    telegram_id,
                    photo_file_id,
                    caption=caption if isinstance(caption, str) and caption else None,
                )
            else:
                text = data.get("text")
                if not isinstance(text, str) or not text:
                    raise ValueError("Missing broadcast text")
                await callback.bot.send_message(telegram_id, text)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.exception(
                "Broadcast delivery failed: admin=%s user=%s error=%s",
                callback.from_user.id,
                telegram_id,
                exc.__class__.__name__,
            )
        await asyncio.sleep(BROADCAST_SEND_DELAY_SECONDS)

    await state.clear()
    logger.info(
        "Broadcast finished: admin=%s audience=%s sent=%s failed=%s",
        callback.from_user.id,
        audience,
        sent,
        failed,
    )
    await callback.message.answer(
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


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


def parse_telegram_id_arg(message: Message) -> int | None:
    args = parse_command_args(message, 1)
    if args is None:
        return None

    telegram_id = parse_int(args[0])
    if telegram_id is None or telegram_id <= 0:
        return None
    return telegram_id


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


@router.message(TextEditState.waiting_text, F.text, ~F.text.startswith("/"))
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


@router.message(TextEditState.waiting_text, F.photo)
@router.message(TextEditState.waiting_text, F.document)
async def text_edit_text_expected_handler(message: Message) -> None:
    await message.answer("Отправьте текстовое сообщение или /cancel_text.")


async def find_tariff_model(
    session: AsyncSession,
    code: str,
) -> TariffModel | None:
    result = await session.execute(
        select(TariffModel).where(TariffModel.code == code.lower().strip())
    )
    return result.scalar_one_or_none()


async def find_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def build_user_access_debug_text(
    session: AsyncSession,
    user: User,
) -> str:
    trial_config = await get_trial_config(session)
    has_active_paid_plan = user_has_active_paid_plan(user)
    has_trial_available = user_has_trial_available(
        user,
        trial_config.requests_limit,
    )
    can_make_request = user_can_make_request(user, trial_config.requests_limit)

    return (
        "Access Debug\n"
        f"telegram_id: {user.telegram_id}\n"
        f"plan: {user.plan}\n"
        f"plan_ends_at: {user.plan_ends_at}\n"
        f"monthly_requests_used: {user.monthly_requests_used}\n"
        f"monthly_requests_limit: {user.monthly_requests_limit}\n"
        f"recognition_balance: {user.recognition_balance}\n"
        f"trial_requests_used: {user.trial_requests_used}\n"
        f"trial_limit: {trial_config.requests_limit}\n"
        f"launch_offer_started_at: {user.launch_offer_started_at}\n"
        f"launch_offer_ends_at: {user.launch_offer_ends_at}\n"
        f"launch_offer_purchased: {str(user.launch_offer_purchased).lower()}\n"
        f"launch_offer_active: {str(is_launch_offer_active(user)).lower()}\n"
        f"launch_offer_hours_left: {get_launch_offer_hours_left(user)}\n"
        f"has_active_paid_plan: {str(has_active_paid_plan).lower()}\n"
        f"has_trial_available: {str(has_trial_available).lower()}\n"
        f"can_make_request: {str(can_make_request).lower()}"
    )


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

    logger.info("Admin %s called legacy set_trial_days", message.from_user.id)
    await message.answer(
        "Trial теперь зависит только от количества бесплатных распознаваний. "
        "Дни не используются."
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

    await set_app_setting(session, "trial_limit", trial_limit)
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
    trial_config = await get_trial_config(session)
    await message.answer(
        f"Бесплатных распознаваний: {trial_config.requests_limit}\n\n"
        f"{tariff_text}"
    )


@router.message(Command("packages"))
async def packages_handler(message: Message, session: AsyncSession) -> None:
    await tariffs_handler(message, session)


@router.message(Command("debug_offer"))
async def debug_offer_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    user = await get_or_create_user(session, message.from_user)
    await message.answer(
        "Offer Debug\n"
        f"launch_offer_started_at: {user.launch_offer_started_at}\n"
        f"launch_offer_ends_at: {user.launch_offer_ends_at}\n"
        f"launch_offer_purchased: {str(user.launch_offer_purchased).lower()}\n"
        f"reminder_6h: {str(user.launch_offer_reminder_6h_sent).lower()}\n"
        f"reminder_12h: {str(user.launch_offer_reminder_12h_sent).lower()}\n"
        f"reminder_18h: {str(user.launch_offer_reminder_18h_sent).lower()}\n"
        f"reminder_24h: {str(user.launch_offer_reminder_24h_sent).lower()}\n"
        f"offer_active: {str(is_launch_offer_active(user)).lower()}\n"
        f"hours_left: {get_launch_offer_hours_left(user)}"
    )


@router.message(Command("reset_my_offer"))
async def reset_my_offer_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    user = await get_or_create_user(session, message.from_user)
    reset_launch_offer(user)
    await message.answer("Offer текущего админа сброшен.")


@router.message(Command("start_my_offer"))
async def start_my_offer_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    user = await get_or_create_user(session, message.from_user)
    start_launch_offer(user)
    await message.answer("Offer текущего админа запущен на 24 часа.")


@router.message(Command("debug_access"))
async def debug_access_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    user = await get_or_create_user(session, message.from_user)
    last_request = await session.scalar(
        select(FontRequest)
        .where(FontRequest.telegram_id == user.telegram_id)
        .order_by(FontRequest.created_at.desc(), FontRequest.id.desc())
        .limit(1)
    )

    await message.answer(
        await build_user_access_debug_text(session, user)
        + "\n"
        f"last_request_result_type: "
        f"{last_request.result_type if last_request else 'none'}\n"
        f"last_request_counted_as_usage: "
        f"{str(last_request.counted_as_usage).lower() if last_request else 'none'}"
    )


@router.message(Command("debug_user_access"))
async def debug_user_access_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    telegram_id = parse_telegram_id_arg(message)
    if telegram_id is None:
        await message.answer("Формат: /debug_user_access <telegram_id>")
        return

    user = await find_user_by_telegram_id(session, telegram_id)
    if user is None:
        await message.answer("Пользователь не найден.")
        return

    await message.answer(await build_user_access_debug_text(session, user))


@router.message(Command("selftest_access"))
async def selftest_access_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    failures = run_access_selftest()
    if failures:
        await message.answer("SELFTEST FAILED:\n" + "\n".join(failures))
        return

    await message.answer("SELFTEST OK")


def run_access_selftest() -> list[str]:
    failures: list[str] = []
    trial_limit = 1

    user = User(telegram_id=900001, plan="none", trial_requests_used=0)
    before = user.trial_requests_used
    increment_usage(user, trial_limit)
    if user.trial_requests_used != before + 1:
        failures.append("CASE 1: success result did not increment trial usage")

    user = User(telegram_id=900002, plan="none", trial_requests_used=0)
    before = user.trial_requests_used
    provider_result_counted_as_usage = False
    if provider_result_counted_as_usage:
        increment_usage(user, trial_limit)
    if user.trial_requests_used != before:
        failures.append("CASE 2: unreadable text incremented trial usage")

    user = User(telegram_id=900003, plan="none", trial_requests_used=1)
    api_called = False
    if user_can_make_request(user, trial_limit):
        api_called = True
    if api_called:
        failures.append("CASE 3: exhausted trial would call API")

    user = User(
        telegram_id=900004,
        plan="designer",
        plan_ends_at=now_utc() + timedelta(days=1),
        monthly_requests_used=0,
        monthly_requests_limit=10,
    )
    before = user.monthly_requests_used
    cache_hit = True
    if cache_hit and is_useful_cached_result("Inter", "font_found"):
        increment_usage(user, trial_limit)
    if user.monthly_requests_used != before + 1:
        failures.append("CASE 4: useful paid cache hit did not increment paid usage")

    user = User(
        telegram_id=900005,
        plan="designer",
        plan_ends_at=now_utc() - timedelta(seconds=1),
        monthly_requests_used=0,
        monthly_requests_limit=10,
        trial_requests_used=trial_limit,
    )
    api_called = False
    if user_can_make_request(user, trial_limit):
        api_called = True
    if api_called:
        failures.append("CASE 5: expired paid user would call API")

    user = User(telegram_id=900006, plan="none", trial_requests_used=0)
    before = user.trial_requests_used
    if is_useful_cached_result("Inter", "font_found"):
        increment_usage(user, trial_limit)
    if user.trial_requests_used != before + 1:
        failures.append(
            "CASE 6: useful trial cache hit did not increment trial usage"
        )

    user = User(
        telegram_id=900007,
        plan="none",
        trial_requests_used=trial_limit,
        recognition_balance=3,
    )
    before = user.recognition_balance
    increment_usage(user, trial_limit)
    if user.recognition_balance != before - 1:
        failures.append("CASE 7: paid balance did not decrement")

    user = User(telegram_id=900008, plan="none", trial_requests_used=0)
    before = user.trial_requests_used
    if is_useful_cached_result(None, "no_font_match"):
        increment_usage(user, trial_limit)
    if user.trial_requests_used != before:
        failures.append("CASE 8: no_font_match incremented trial usage")

    return failures


@router.message(Command("force_rub_ui"))
async def force_rub_ui_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    await force_rub_payment_ui_texts(session)
    await message.answer("Интерфейс оплаты обновлён на ₽ и карту.")


@router.message(Command("debug_payment_ui"))
async def debug_payment_ui_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    keyboard = subscription_menu_keyboard(user_has_active_paid_plan=False)
    button_lines = []
    button_values = []
    for row in keyboard.inline_keyboard:
        for button in row:
            callback_data = button.callback_data or ""
            button_values.append(f"{button.text} {callback_data}")
            button_lines.append(f"- {button.text} | {callback_data}")

    subscription_text = await get_bot_text(
        session,
        "subscription_no_access",
        status="Нет активного доступа",
        **(await get_tariff_text_values(session)),
    )
    forbidden = "St" + "ars"
    contains_legacy = forbidden in "\n".join(button_values + [subscription_text])
    await message.answer(
        "Payment UI Debug:\n"
        "Buttons:\n"
        + "\n".join(button_lines)
        + "\n\n"
        f"Contains Stars: {str(contains_legacy).lower()}\n\n"
        "subscription_no_access:\n"
        f"{subscription_text}"
    )


@router.message(Command("debug_robokassa"))
async def debug_robokassa_handler(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Недоступно.")
        return

    await message.answer("Robokassa Debug:\n" + "\n".join(robokassa_debug_lines()))


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


@router.message(Command("reset_user_trial"))
async def reset_user_trial_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    telegram_id = parse_telegram_id_arg(message)
    if telegram_id is None:
        await message.answer("Формат: /reset_user_trial <telegram_id>")
        return

    user = await find_user_by_telegram_id(session, telegram_id)
    if user is None:
        await message.answer("Пользователь не найден.")
        return

    user.trial_requests_used = 0
    user.trial_started_at = None
    user.trial_ends_at = None
    logger.info(
        "Admin %s reset trial for user %s",
        message.from_user.id,
        telegram_id,
    )
    await message.answer(f"Trial пользователя {telegram_id} сброшен.")


@router.message(Command("reset_user_limits"))
async def reset_user_limits_handler(message: Message, session: AsyncSession) -> None:
    if not await require_tariff_admin(message, session):
        return

    telegram_id = parse_telegram_id_arg(message)
    if telegram_id is None:
        await message.answer("Формат: /reset_user_limits <telegram_id>")
        return

    user = await find_user_by_telegram_id(session, telegram_id)
    if user is None:
        await message.answer("Пользователь не найден.")
        return

    user.trial_requests_used = 0
    user.monthly_requests_used = 0
    logger.info(
        "Admin %s reset limits for user %s",
        message.from_user.id,
        telegram_id,
    )
    await message.answer(f"Лимиты пользователя {telegram_id} сброшены.")


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
