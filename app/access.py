from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting, User
from app.payments import list_tariffs, tariff_title
from app.texts import get_bot_text

PAID_PLAN_CODES = {"designer", "studio"}


@dataclass(frozen=True)
class TrialConfig:
    days: int
    requests_limit: int


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_date(value: datetime | None) -> str:
    normalized = as_utc(value)
    if normalized is None:
        return "неизвестно"
    return normalized.strftime("%d.%m.%Y")


def format_duration_until(value: datetime | None) -> str:
    normalized = as_utc(value)
    if normalized is None:
        return "0 ч."

    delta = normalized - now_utc()
    if delta.total_seconds() <= 0:
        return "0 ч."

    total_hours = int(delta.total_seconds() // 3600)
    days = total_hours // 24
    hours = total_hours % 24
    if days > 0:
        return f"{days} д. {hours} ч."
    return f"{hours} ч."


def duration_parts_until(value: datetime | None) -> tuple[int, int]:
    normalized = as_utc(value)
    if normalized is None:
        return 0, 0

    delta = normalized - now_utc()
    if delta.total_seconds() <= 0:
        return 0, 0

    total_hours = int(delta.total_seconds() // 3600)
    return total_hours // 24, total_hours % 24


def days_left_until(value: datetime | None) -> int:
    normalized = as_utc(value)
    if normalized is None:
        return 0

    seconds = (normalized - now_utc()).total_seconds()
    if seconds <= 0:
        return 0
    return ceil(seconds / 86400)


def remaining_trial_requests(user: User) -> int:
    return max(0, settings.trial_requests_limit - user.trial_requests_used)


def remaining_trial_requests_for_limit(user: User, requests_limit: int) -> int:
    return max(0, requests_limit - user.trial_requests_used)


def remaining_paid_requests(user: User) -> int:
    return max(0, user.monthly_requests_limit - user.monthly_requests_used)


async def get_or_create_user(
    session: AsyncSession,
    telegram_user: TelegramUser,
) -> User:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_user.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
        )
        session.add(user)
        await session.flush()
        return user

    user.username = telegram_user.username
    user.first_name = telegram_user.first_name
    return user


async def get_app_setting_int(
    session: AsyncSession,
    key: str,
    default: int,
) -> int:
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        return default

    try:
        value = int(setting.value)
    except (TypeError, ValueError):
        return default

    return value if value > 0 else default


async def set_app_setting(
    session: AsyncSession,
    key: str,
    value: int,
) -> AppSetting:
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = AppSetting(key=key, value=str(value))
        session.add(setting)
        await session.flush()
        return setting

    setting.value = str(value)
    return setting


async def get_trial_config(session: AsyncSession) -> TrialConfig:
    return TrialConfig(
        days=await get_app_setting_int(session, "trial_days", settings.trial_days),
        requests_limit=await get_app_setting_int(
            session,
            "trial_requests_limit",
            settings.trial_requests_limit,
        ),
    )


def start_trial_if_needed(user: User, trial_days: int | None = None) -> None:
    if user.trial_started_at is None and user.plan == "none":
        started_at = now_utc()
        days = trial_days or settings.trial_days
        user.trial_started_at = started_at
        user.trial_ends_at = started_at + timedelta(days=days)
        user.trial_requests_used = 0


def user_has_active_paid_plan(user: User) -> bool:
    plan_ends_at = as_utc(user.plan_ends_at)
    return (
        user.plan in PAID_PLAN_CODES
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
        and user.monthly_requests_used < user.monthly_requests_limit
    )


def user_has_current_paid_subscription(user: User) -> bool:
    plan_ends_at = as_utc(user.plan_ends_at)
    return (
        user.plan in PAID_PLAN_CODES
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
    )


def user_has_active_trial(user: User, trial_requests_limit: int | None = None) -> bool:
    trial_ends_at = as_utc(user.trial_ends_at)
    requests_limit = trial_requests_limit or settings.trial_requests_limit
    return (
        user.trial_started_at is not None
        and trial_ends_at is not None
        and trial_ends_at > now_utc()
        and user.trial_requests_used < requests_limit
    )


def user_can_make_request(user: User, trial_requests_limit: int | None = None) -> bool:
    if user_has_current_paid_subscription(user):
        return user_has_active_paid_plan(user)

    if user_has_active_trial(user, trial_requests_limit):
        return True

    return False


def increment_usage(user: User, trial_requests_limit: int | None = None) -> None:
    if user_has_current_paid_subscription(user):
        if user_has_active_paid_plan(user):
            user.monthly_requests_used += 1
        return

    if user_has_active_trial(user, trial_requests_limit):
        user.trial_requests_used += 1


async def get_status_text(session: AsyncSession, user: User) -> str:
    return await get_profile_text(session, user, include_title=False)


async def get_tariff_text_values(session: AsyncSession) -> dict[str, object]:
    tariffs = await list_tariffs(session, active_only=True)
    values: dict[str, object] = {
        "price_designer": 0,
        "limit_designer": 0,
        "price_studio": 0,
        "limit_studio": 0,
    }
    for plan in tariffs:
        if plan.code in {"designer", "studio"}:
            values[f"price_{plan.code}"] = plan.price_stars
            values[f"limit_{plan.code}"] = plan.monthly_limit
    return values


async def get_main_menu_text(session: AsyncSession) -> str:
    trial_config = await get_trial_config(session)
    return await get_bot_text(
        session,
        "main_menu",
        trial_days=trial_config.days,
        trial_limit=trial_config.requests_limit,
    )


async def get_no_access_text(session: AsyncSession) -> str:
    return await get_bot_text(
        session,
        "no_access",
        **(await get_tariff_text_values(session)),
    )


async def get_profile_text(
    session: AsyncSession,
    user: User,
    include_title: bool = True,
) -> str:
    trial_config = await get_trial_config(session)

    if user_has_current_paid_subscription(user):
        title = await tariff_title(session, user.plan)
        text_key = f"profile_{user.plan}"
        if user.subscription_canceled:
            return await get_bot_text(
                session,
                text_key,
                status=f"{title} ✅",
                tariff=title,
                date=format_date(user.plan_ends_at),
                days_left=days_left_until(user.plan_ends_at),
                remaining=remaining_paid_requests(user),
                limit=user.monthly_requests_limit,
            )

        return await get_bot_text(
            session,
            text_key,
            status=f"{title} ✅",
            tariff=title,
            date=format_date(user.plan_ends_at),
            days_left=days_left_until(user.plan_ends_at),
            remaining=remaining_paid_requests(user),
            limit=user.monthly_requests_limit,
        )

    if user_has_active_trial(user, trial_config.requests_limit):
        days_left, hours_left = duration_parts_until(user.trial_ends_at)
        return await get_bot_text(
            session,
            "profile_trial" if include_title else "profile_trial",
            status="Trial",
            days_left=days_left,
            hours_left=hours_left,
            remaining=remaining_trial_requests_for_limit(
                user,
                trial_config.requests_limit,
            ),
            limit=trial_config.requests_limit,
        )

    return await get_bot_text(session, "profile_no_access", status="Нет активной подписки")


async def get_subscription_text(session: AsyncSession, user: User) -> str:
    trial_config = await get_trial_config(session)

    if user_has_current_paid_subscription(user):
        title = await tariff_title(session, user.plan)
        return await get_bot_text(
            session,
            f"subscription_{user.plan}",
            status=f"{title} ✅",
            tariff=title,
            date=format_date(user.plan_ends_at),
            days_left=days_left_until(user.plan_ends_at),
            remaining=remaining_paid_requests(user),
            limit=user.monthly_requests_limit,
        )

    if user_has_active_trial(user, trial_config.requests_limit):
        days_left, hours_left = duration_parts_until(user.trial_ends_at)
        return await get_bot_text(
            session,
            "subscription_trial",
            status="Trial",
            days_left=days_left,
            hours_left=hours_left,
            remaining=remaining_trial_requests_for_limit(
                user,
                trial_config.requests_limit,
            ),
            limit=trial_config.requests_limit,
            **(await get_tariff_text_values(session)),
        )

    return await get_bot_text(
        session,
        "subscription_no_access",
        status="Нет активного доступа",
        **(await get_tariff_text_values(session)),
    )


def normalize_subscription_expiration(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return None


def activate_plan(
    user: User,
    tariff: str,
    monthly_limit: int,
    subscription_expiration_date: object = None,
) -> None:
    if tariff not in PAID_PLAN_CODES:
        raise ValueError("Unknown tariff")

    user.plan = tariff
    user.plan_started_at = now_utc()
    user.plan_ends_at = normalize_subscription_expiration(
        subscription_expiration_date
    ) or (now_utc() + timedelta(days=30))
    user.monthly_requests_used = 0
    user.monthly_requests_limit = monthly_limit
    user.subscription_canceled = False
