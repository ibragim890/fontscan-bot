import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import now_utc
from app.config import settings
from app.models import AdminAccess, ApiKeyUsage, FontRequest, Payment, User

router = Router(name="admin")
logger = logging.getLogger(__name__)


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
            User.trial_requests_used < settings.trial_requests_limit,
        )
    )
    trial_finished = await session.scalar(
        select(func.count(User.id)).where(
            User.trial_started_at.is_not(None),
            or_(
                User.trial_ends_at <= now,
                User.trial_requests_used >= settings.trial_requests_limit,
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
        f"Stars всего: {stars_total or 0}\n"
        f"Stars сегодня: {stars_today or 0}\n\n"
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
