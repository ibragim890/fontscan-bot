import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import now_utc
from app.config import settings
from app.models import ApiKeyUsage, FontRequest, Payment, User

router = Router(name="admin")
logger = logging.getLogger(__name__)


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_set


async def build_admin_stats(session: AsyncSession) -> str:
    now = now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_users = await session.scalar(select(func.count(User.id)))
    active_trial = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_started_at.is_not(None),
            User.trial_ends_at > now,
            User.trial_requests_used < settings.trial_requests_limit,
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
    usage_rows_result = await session.execute(
        select(ApiKeyUsage).where(
            ApiKeyUsage.provider == "whatfontis",
            ApiKeyUsage.date == now.date(),
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
            "API safety limit:\n"
            f"Used today: {total_api_usage_today} / {safety_limit}\n"
            f"Status: {safety_status}"
        )
    else:
        safety_text = (
            "API safety limit:\n"
            f"Used today: {total_api_usage_today} / disabled\n"
            "Status: disabled"
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
