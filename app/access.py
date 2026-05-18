from datetime import datetime, timedelta, timezone
from math import ceil

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User
from app.payments import get_tariff, tariff_title


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


def start_trial_if_needed(user: User) -> None:
    if user.trial_started_at is None and user.plan == "none":
        started_at = now_utc()
        user.trial_started_at = started_at
        user.trial_ends_at = started_at + timedelta(days=settings.trial_days)
        user.trial_requests_used = 0


def user_has_active_paid_plan(user: User) -> bool:
    plan = get_tariff(user.plan)
    plan_ends_at = as_utc(user.plan_ends_at)
    return (
        plan is not None
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
        and user.monthly_requests_used < user.monthly_requests_limit
    )


def user_has_current_paid_subscription(user: User) -> bool:
    plan = get_tariff(user.plan)
    plan_ends_at = as_utc(user.plan_ends_at)
    return plan is not None and plan_ends_at is not None and plan_ends_at > now_utc()


def user_has_active_trial(user: User) -> bool:
    trial_ends_at = as_utc(user.trial_ends_at)
    return (
        user.trial_started_at is not None
        and trial_ends_at is not None
        and trial_ends_at > now_utc()
        and user.trial_requests_used < settings.trial_requests_limit
    )


def user_can_make_request(user: User) -> bool:
    if user_has_current_paid_subscription(user):
        return user_has_active_paid_plan(user)

    if user_has_active_trial(user):
        return True

    return False


def increment_usage(user: User) -> None:
    if user_has_current_paid_subscription(user):
        if user_has_active_paid_plan(user):
            user.monthly_requests_used += 1
        return

    if user_has_active_trial(user):
        user.trial_requests_used += 1


def get_status_text(user: User) -> str:
    return get_profile_text(user, include_title=False)


def get_profile_text(user: User, include_title: bool = True) -> str:
    prefix = "Профиль\n\n" if include_title else ""

    if user_has_current_paid_subscription(user):
        title = tariff_title(user.plan)
        if user.subscription_canceled:
            return prefix + (
                f"Статус: {title} ✅\n"
                "Продление отменено\n"
                f"Доступ активен до: {format_date(user.plan_ends_at)}\n"
                f"Осталось дней: {days_left_until(user.plan_ends_at)}\n"
                f"Распознаваний осталось: "
                f"{remaining_paid_requests(user)} / {user.monthly_requests_limit}"
            )

        return prefix + (
            f"Статус: {title} ✅\n"
            f"Подписка активна до: {format_date(user.plan_ends_at)}\n"
            f"Осталось дней: {days_left_until(user.plan_ends_at)}\n"
            f"Распознаваний осталось: "
            f"{remaining_paid_requests(user)} / {user.monthly_requests_limit}"
        )

    if user_has_active_trial(user):
        return prefix + (
            "Статус: Trial\n"
            f"Осталось: {format_duration_until(user.trial_ends_at)}\n"
            f"Распознаваний осталось: "
            f"{remaining_trial_requests(user)} / {settings.trial_requests_limit}"
        )

    if user.trial_started_at is not None:
        return prefix + (
            "Статус: Нет активной подписки\n"
            "Пробный доступ закончился.\n\n"
            "Оформите подписку, чтобы продолжить."
        )

    return prefix + "Статус: Нет активного доступа"


def get_subscription_text(user: User) -> str:
    if user_has_current_paid_subscription(user):
        title = tariff_title(user.plan)
        if user.subscription_canceled:
            return (
                "Подписка\n\n"
                f"Статус: {title} ✅\n"
                "Продление отменено\n"
                f"Доступ активен до: {format_date(user.plan_ends_at)}\n"
                f"Распознаваний осталось: "
                f"{remaining_paid_requests(user)} / {user.monthly_requests_limit}"
            )

        return (
            "Подписка\n\n"
            f"Статус: {title} ✅\n"
            f"Доступ до: {format_date(user.plan_ends_at)}\n"
            f"Распознаваний осталось: "
            f"{remaining_paid_requests(user)} / {user.monthly_requests_limit}"
        )

    if user_has_active_trial(user):
        return (
            "Подписка\n\n"
            "Статус: Trial\n"
            f"Осталось: {format_duration_until(user.trial_ends_at)}\n"
            f"Распознаваний осталось: "
            f"{remaining_trial_requests(user)} / {settings.trial_requests_limit}\n\n"
            "После пробного доступа нужна подписка.\n\n"
            "Designer — 99 Stars / 20 распознаваний\n"
            "Studio — 199 Stars / 50 распознаваний"
        )

    return (
        "Подписка\n\n"
        "Статус: Нет активного доступа\n\n"
        "Designer — 99 Stars / 20 распознаваний\n"
        "Studio — 199 Stars / 50 распознаваний"
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
    subscription_expiration_date: object = None,
) -> None:
    plan = get_tariff(tariff)
    if plan is None:
        raise ValueError("Unknown tariff")

    user.plan = plan.key
    user.plan_started_at = now_utc()
    user.plan_ends_at = normalize_subscription_expiration(
        subscription_expiration_date
    ) or (now_utc() + timedelta(days=30))
    user.monthly_requests_used = 0
    user.monthly_requests_limit = plan.monthly_limit
    user.subscription_canceled = False
