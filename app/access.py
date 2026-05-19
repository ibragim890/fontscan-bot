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
FOUNDER_OFFER_CODE = "founder_offer"
FOUNDER_REGULAR_CODE = "founder_regular"
PACKAGE_TARIFF_CODES = {FOUNDER_OFFER_CODE, FOUNDER_REGULAR_CODE}
LAUNCH_OFFER_DURATION = timedelta(hours=24)
NON_USEFUL_RESULT_TYPES = {
    "unreadable_text",
    "no_text_detected",
    "service_error",
    "provider_error",
    "timeout",
    "invalid_image",
    "rate_limited",
    "internal_api_error",
    "invalid_response",
}


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


def is_launch_offer_active(user: User) -> bool:
    started_at = as_utc(user.launch_offer_started_at)
    ends_at = as_utc(user.launch_offer_ends_at)
    return (
        started_at is not None
        and ends_at is not None
        and ends_at > now_utc()
        and not user.launch_offer_purchased
    )


def get_launch_offer_hours_left(user: User) -> int:
    if not is_launch_offer_active(user):
        return 0

    ends_at = as_utc(user.launch_offer_ends_at)
    if ends_at is None:
        return 0

    seconds_left = (ends_at - now_utc()).total_seconds()
    if seconds_left <= 0:
        return 0
    return max(1, ceil(seconds_left / 3600))


def reset_launch_offer(user: User) -> None:
    user.launch_offer_started_at = None
    user.launch_offer_ends_at = None
    user.launch_offer_purchased = False
    user.launch_offer_reminder_6h_sent = False
    user.launch_offer_reminder_12h_sent = False
    user.launch_offer_reminder_18h_sent = False
    user.launch_offer_reminder_24h_sent = False


def start_launch_offer(user: User, started_at: datetime | None = None) -> None:
    start = started_at or now_utc()
    user.launch_offer_started_at = start
    user.launch_offer_ends_at = start + LAUNCH_OFFER_DURATION
    user.launch_offer_purchased = False
    user.launch_offer_reminder_6h_sent = False
    user.launch_offer_reminder_12h_sent = False
    user.launch_offer_reminder_18h_sent = False
    user.launch_offer_reminder_24h_sent = False


def start_launch_offer_if_eligible(user: User, free_limit: int) -> bool:
    if free_limit <= 0:
        return False
    if user.launch_offer_started_at is not None:
        return False
    if user.launch_offer_purchased:
        return False
    if user.trial_requests_used != free_limit:
        return False

    start_launch_offer(user)
    return True


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
    legacy_remaining = max(0, user.monthly_requests_limit - user.monthly_requests_used)
    return paid_recognition_balance(user) + legacy_remaining


def paid_recognition_balance(user: User) -> int:
    return max(0, int(user.recognition_balance or 0))


async def get_or_create_user(
    session: AsyncSession,
    telegram_user: TelegramUser,
    start_payload: str | None = None,
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
        apply_start_payload(user, start_payload)
        session.add(user)
        await session.flush()
        return user

    user.username = telegram_user.username
    user.first_name = telegram_user.first_name
    apply_start_payload(user, start_payload)
    return user


def normalize_start_payload(payload: str | None) -> str | None:
    if payload is None:
        return None

    normalized = payload.strip()
    if not normalized:
        return None

    return normalized[:255]


def apply_start_payload(user: User, payload: str | None) -> None:
    normalized = normalize_start_payload(payload)
    if normalized is None:
        return

    if not user.source:
        user.source = normalized

    if normalized.startswith("ref_") and not user.referred_by:
        referral = normalized.removeprefix("ref_").strip()
        user.referred_by = (referral or normalized)[:255]


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
        days=settings.trial_days,
        requests_limit=await get_app_setting_int(
            session,
            "trial_limit",
            settings.trial_requests_limit,
        ),
    )


def start_trial_if_needed(user: User, trial_days: int | None = None) -> None:
    return None


def user_has_active_legacy_paid_plan(user: User) -> bool:
    plan_ends_at = as_utc(user.plan_ends_at)
    return (
        user.plan in PAID_PLAN_CODES
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
        and user.monthly_requests_used < user.monthly_requests_limit
    )


def user_has_active_paid_plan(user: User) -> bool:
    return paid_recognition_balance(user) > 0 or user_has_active_legacy_paid_plan(user)


def user_has_current_paid_subscription(user: User) -> bool:
    plan_ends_at = as_utc(user.plan_ends_at)
    return (
        user.plan in PAID_PLAN_CODES
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
    )


def user_has_trial_available(
    user: User,
    trial_requests_limit: int | None = None,
) -> bool:
    requests_limit = trial_requests_limit or settings.trial_requests_limit
    plan_ends_at = as_utc(user.plan_ends_at)
    has_current_paid_period = (
        user.plan in PAID_PLAN_CODES
        and plan_ends_at is not None
        and plan_ends_at > now_utc()
    )
    if has_current_paid_period:
        return False

    plan_allows_trial = user.plan is None or user.plan == "none"
    return plan_allows_trial and user.trial_requests_used < requests_limit


def user_can_make_request(user: User, trial_requests_limit: int | None = None) -> bool:
    if user_has_active_paid_plan(user):
        return True

    if user_has_trial_available(user, trial_requests_limit):
        return True

    return False


def increment_usage(user: User, trial_requests_limit: int | None = None) -> None:
    if paid_recognition_balance(user) > 0:
        user.recognition_balance -= 1
        return

    if user_has_active_legacy_paid_plan(user):
        user.monthly_requests_used += 1
        return

    if user_has_trial_available(user, trial_requests_limit):
        user.trial_requests_used += 1


def grant_paid_recognitions(user: User, tariff: str, recognitions_count: int) -> None:
    if recognitions_count <= 0:
        raise ValueError("recognitions_count must be positive")

    user.recognition_balance = paid_recognition_balance(user) + recognitions_count
    if tariff in PACKAGE_TARIFF_CODES:
        user.launch_offer_purchased = True


def is_useful_cached_result(title: str | None, result_type: str | None) -> bool:
    normalized_title = (title or "").strip().lower()
    normalized_type = (result_type or "").strip()
    return (
        bool(normalized_title)
        and normalized_title != "не определён"
        and normalized_type not in NON_USEFUL_RESULT_TYPES
    )


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


async def get_access_text(session: AsyncSession, user: User) -> str:
    trial_config = await get_trial_config(session)
    free_remaining = remaining_trial_requests_for_limit(
        user,
        trial_config.requests_limit,
    )
    paid_balance = paid_recognition_balance(user)

    if is_launch_offer_active(user):
        return (
            "<b>Доступ Fontopus 🐙</b>\n\n"
            f"Бесплатные распознавания: <b>{free_remaining} / "
            f"{trial_config.requests_limit}</b>\n"
            f"Платный баланс: <b>{paid_balance}</b>\n\n"
            "Спец-доступ для первых пользователей:\n\n"
            "<b>50 распознаваний за 99 ₽</b>\n"
            "<s>199 ₽</s>\n\n"
            f"Осталось: <b>{get_launch_offer_hours_left(user)} ч.</b>"
        )

    return (
        "<b>Доступ Fontopus 🐙</b>\n\n"
        f"Бесплатные распознавания: <b>{free_remaining} / "
        f"{trial_config.requests_limit}</b>\n"
        f"Платный баланс: <b>{paid_balance}</b>\n\n"
        "Пакет:\n"
        "<b>50 распознаваний за 199 ₽</b>"
    )


async def get_no_access_text(session: AsyncSession, user: User) -> str:
    if is_launch_offer_active(user):
        return (
            "<b>Бесплатное распознавание использовано 🐙</b>\n\n"
            "Можно забрать спец-доступ:\n\n"
            "<b>50 распознаваний за 99 ₽</b>\n"
            "<s>199 ₽</s>\n\n"
            f"Осталось: <b>{get_launch_offer_hours_left(user)} ч.</b>\n\n"
            "1 распознавание = 1 найденный шрифт."
        )

    return (
        "<b>Бесплатное распознавание использовано 🐙</b>\n\n"
        "Можно купить пакет:\n\n"
        "<b>50 распознаваний за 199 ₽</b>\n\n"
        "1 распознавание = 1 найденный шрифт."
    )


def strip_payment_tariff_lines(text: str) -> str:
    blocked_parts = ("Designer", "Studio", "Выберите тариф", "St" + "ars")
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if not any(part in line for part in blocked_parts)
    ]
    return "\n".join(lines).strip()


def rub_tariff_block(
    values: dict[str, object],
    *,
    include_month: bool = True,
    terminal_period: bool = True,
) -> str:
    if not include_month:
        return (
            f"Designer — {values['price_designer']} ₽ / "
            f"{values['limit_designer']} распознаваний\n"
            f"Studio — {values['price_studio']} ₽ / "
            f"{values['limit_studio']} распознаваний"
        )
    period = " / месяц"
    line_end = "." if terminal_period else ""
    return (
        f"Designer — {values['price_designer']} ₽{period}, "
        f"{values['limit_designer']} распознаваний{line_end}\n"
        f"Studio — {values['price_studio']} ₽{period}, "
        f"{values['limit_studio']} распознаваний{line_end}"
    )


def append_rub_tariff_block(
    text: str,
    values: dict[str, object],
    *,
    include_month: bool = True,
    include_choose: bool = False,
    terminal_period: bool = True,
) -> str:
    parts = [
        text,
        rub_tariff_block(
            values,
            include_month=include_month,
            terminal_period=terminal_period,
        ),
    ]
    if include_choose:
        parts.append("Выберите тариф:")
    return "\n\n".join(part for part in parts if part)


def with_card_payment_unavailable_notice(text: str) -> str:
    if settings.robokassa_enabled:
        return text
    return text + "\n\nОплата картой временно недоступна."


async def get_profile_text(
    session: AsyncSession,
    user: User,
    include_title: bool = True,
) -> str:
    if not user_has_current_paid_subscription(user):
        return await get_access_text(session, user)

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


async def get_subscription_text(session: AsyncSession, user: User) -> str:
    return await get_access_text(session, user)


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
