import hashlib
import io
import json
import logging

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    get_no_access_text,
    get_or_create_user,
    get_trial_config,
    increment_usage,
    is_launch_offer_active,
    is_useful_cached_result,
    now_utc,
    start_launch_offer_if_eligible,
    user_has_active_paid_plan,
    user_can_make_request,
)
from app.config import settings
from app.font_services.whatfontis import WhatFontIsClient
from app.keyboards import (
    no_access_subscription_keyboard,
    offer_purchase_keyboard,
    result_actions_keyboard,
)
from app.models import ApiKeyUsage, FontRequest
from app.texts import (
    DOWNLOAD_ERROR_TEXT,
    DOCUMENT_TEXT,
    LAUNCH_OFFER_TEXT,
    NOT_PHOTO_TEXT,
    PROCESSING_TEXT,
    TEMP_UNAVAILABLE_TEXT,
    UNREADABLE_TEXT,
    font_result_text,
)

logger = logging.getLogger(__name__)
router = Router(name="photo")

UNREADABLE_RESULT_TYPES = {"unreadable_text", "no_text_detected", "invalid_image"}


@router.message(F.photo)
async def photo_handler(message: Message, session: AsyncSession) -> None:
    if message.caption and message.caption.lstrip().startswith("/"):
        return

    user = await get_or_create_user(session, message.from_user)
    if user.first_photo_at is None:
        user.first_photo_at = now_utc()
    trial_config = await get_trial_config(session)

    if not user_can_make_request(user, trial_config.requests_limit):
        if user.paywall_hit_at is None:
            user.paywall_hit_at = now_utc()
        await message.answer(
            await get_no_access_text(session, user),
            reply_markup=no_access_subscription_keyboard(is_launch_offer_active(user)),
            parse_mode="HTML",
        )
        return

    processing_message = await message.answer(PROCESSING_TEXT)
    image_bytes = await download_largest_photo(message)
    if image_bytes is None:
        await safe_edit_text(processing_message, DOWNLOAD_ERROR_TEXT)
        return

    image_hash = hashlib.sha256(image_bytes).hexdigest()
    cached_request = await find_cached_font_request(session, image_hash)
    if cached_request is not None:
        logger.info("Cache hit: %s", image_hash)
        result_type = cached_result_type(cached_request)
        provider_success = cached_provider_success(cached_request, result_type)
        useful = is_useful_cached_result(cached_request.top_font, result_type)
        counted_as_usage = useful
        offer_started = False
        access_type = access_type_for_user(user)
        if counted_as_usage:
            trial_used_before = user.trial_requests_used
            increment_usage(user, trial_config.requests_limit)
            if user.trial_requests_used > trial_used_before:
                offer_started = start_launch_offer_if_eligible(
                    user,
                    trial_config.requests_limit,
                )
            logger.info("Usage incremented from cache hit")
            log_usage_incremented(
                reason="cache_hit",
                access_type=access_type,
                provider_result_type=result_type,
            )
        else:
            logger.info("Cache hit not useful, usage skipped")
            log_usage_skipped(
                reason=usage_skip_reason(result_type, provider_success, user),
                access_type=access_type,
                provider_result_type=result_type,
            )
        cached_result_json = cached_request.result_json or json.dumps(
            {"cached_from": cached_request.id},
            ensure_ascii=False,
        )
        session.add(
            FontRequest(
                telegram_id=message.from_user.id,
                provider="cache",
                image_hash=image_hash,
                top_font=cached_request.top_font,
                result_json=cached_result_json,
                status=cached_request.status,
                result_type=result_type,
                provider_success=provider_success,
                counted_as_usage=counted_as_usage,
                is_cached_response=True,
            )
        )
        await session.commit()
        await safe_edit_text(
            processing_message,
            await response_text_for_result(
                session,
                cached_request.top_font,
                result_type,
                provider_success,
            ),
            reply_markup=result_actions_keyboard(),
        )
        if offer_started:
            await send_launch_offer_message(message)
        return

    api_requests_today = await get_total_api_usage_today(session, provider="whatfontis")
    safety_limit = settings.daily_api_safety_limit
    if safety_limit and api_requests_today >= safety_limit:
        logger.warning(
            "Daily API safety limit reached: %s/%s",
            api_requests_today,
            safety_limit,
        )
        await safe_edit_text(processing_message, TEMP_UNAVAILABLE_TEXT)
        return

    client = WhatFontIsClient(settings.whatfontis_api_keys)
    result = await client.recognize(image_bytes)
    if result.api_request_made:
        await increment_api_key_usage(
            session=session,
            provider="whatfontis",
            key_index=result.key_index,
            status=result.http_status,
            rate_limited=result.rate_limited,
        )

    offer_started = False
    access_type = access_type_for_user(user)
    if result.counted_as_usage:
        trial_used_before = user.trial_requests_used
        increment_usage(user, trial_config.requests_limit)
        if user.trial_requests_used > trial_used_before:
            offer_started = start_launch_offer_if_eligible(
                user,
                trial_config.requests_limit,
            )
        log_usage_incremented(
            reason="provider_result",
            access_type=access_type,
            provider_result_type=result.result_type,
        )
    else:
        log_usage_skipped(
            reason=usage_skip_reason(result.result_type, result.success, user),
            access_type=access_type,
            provider_result_type=result.result_type,
        )

    font_request = FontRequest(
        telegram_id=message.from_user.id,
        provider="whatfontis",
        image_hash=image_hash,
        top_font=result.title,
        result_json=json.dumps(result.result_json, ensure_ascii=False),
        status=result.status,
        result_type=result.result_type,
        provider_success=result.success,
        counted_as_usage=result.counted_as_usage,
        is_cached_response=False,
    )
    session.add(font_request)

    if not result.counted_as_usage:
        await safe_edit_text(
            processing_message,
            result.user_message
            or await response_text_for_result(
                session,
                result.title,
                result.result_type,
                result.success,
            ),
            reply_markup=(
                result_actions_keyboard()
                if result.result_type in UNREADABLE_RESULT_TYPES | {"no_font_match"}
                else None
            ),
        )
        return

    await session.commit()
    await safe_edit_text(
        processing_message,
        await response_text_for_result(
            session,
            result.title,
            result.result_type,
            result.success,
        ),
        reply_markup=result_actions_keyboard(),
    )
    if offer_started:
        await send_launch_offer_message(message)


def access_type_for_user(user) -> str:
    return "paid" if user_has_active_paid_plan(user) else "trial"


def cached_result_type(font_request: FontRequest) -> str:
    result_type = (font_request.result_type or "").strip()
    if result_type and result_type != "unknown":
        return result_type
    if font_request.top_font:
        return "font_found"
    return "unreadable_text"


def cached_provider_success(font_request: FontRequest, result_type: str) -> bool:
    if font_request.provider_success:
        return True
    if result_type == "no_font_match":
        return True
    return is_useful_cached_result(font_request.top_font, result_type)


def usage_skip_reason(result_type: str, provider_success: bool, user) -> str:
    if result_type in {"unreadable_text", "invalid_image"}:
        return "unreadable_text"
    if result_type == "no_text_detected":
        return "no_text_detected"
    if result_type == "timeout":
        return "timeout"
    if result_type in {"provider_error", "rate_limited", "internal_api_error"}:
        return "provider_error"
    if provider_success and user_has_active_paid_plan(user):
        return "paid_cache_hit"
    return "provider_error"


def log_usage_incremented(
    *,
    reason: str,
    access_type: str,
    provider_result_type: str,
) -> None:
    logger.info(
        "usage incremented reason=%s access_type=%s provider_result_type=%s",
        reason,
        access_type,
        provider_result_type,
    )


def log_usage_skipped(
    *,
    reason: str,
    access_type: str,
    provider_result_type: str,
) -> None:
    logger.info(
        "usage skipped reason=%s access_type=%s provider_result_type=%s",
        reason,
        access_type,
        provider_result_type,
    )


async def response_text_for_result(
    session: AsyncSession,
    title: str | None,
    result_type: str,
    provider_success: bool,
) -> str:
    if result_type == "no_font_match":
        return await font_result_text(session, None)
    if result_type in UNREADABLE_RESULT_TYPES or not provider_success:
        return UNREADABLE_TEXT
    return await font_result_text(session, title)


async def send_launch_offer_message(message: Message) -> None:
    await message.answer(
        LAUNCH_OFFER_TEXT,
        reply_markup=offer_purchase_keyboard(),
        parse_mode="HTML",
    )


async def safe_edit_text(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logger.warning("Failed to edit loading message: %s", exc.__class__.__name__)
        await message.answer(text, reply_markup=reply_markup)


async def find_cached_font_request(
    session: AsyncSession,
    image_hash: str,
) -> FontRequest | None:
    result = await session.execute(
        select(FontRequest)
        .where(
            FontRequest.image_hash == image_hash,
            FontRequest.status.in_(["success", "no_result"]),
        )
        .order_by(desc(FontRequest.created_at), desc(FontRequest.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def increment_api_key_usage(
    session: AsyncSession,
    provider: str,
    key_index: int,
    status: int | None,
    rate_limited: bool,
) -> None:
    today = now_utc().date()
    query_result = await session.execute(
        select(ApiKeyUsage).where(
            ApiKeyUsage.provider == provider,
            ApiKeyUsage.key_index == key_index,
            ApiKeyUsage.date == today,
        ).limit(1)
    )
    usage = query_result.scalar_one_or_none()
    if usage is None:
        usage = ApiKeyUsage(
            provider=provider,
            key_index=key_index,
            date=today,
            requests_count=0,
            rate_limited=False,
        )
        session.add(usage)
        await session.flush()

    usage.requests_count += 1
    usage.last_status = str(status) if status is not None else "transport_error"
    if rate_limited:
        usage.rate_limited = True


async def get_total_api_usage_today(
    session: AsyncSession,
    provider: str,
) -> int:
    today = now_utc().date()
    total = await session.scalar(
        select(func.coalesce(func.sum(ApiKeyUsage.requests_count), 0)).where(
            ApiKeyUsage.provider == provider,
            ApiKeyUsage.date == today,
        )
    )
    return int(total or 0)


async def download_largest_photo(message: Message) -> bytes | None:
    if not message.photo:
        return None

    photo = message.photo[-1]
    try:
        file = await message.bot.get_file(photo.file_id)
        buffer = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=buffer)
    except Exception as exc:
        logger.exception("Failed to download Telegram file: %s", exc.__class__.__name__)
        return None

    return buffer.getvalue()


@router.message(F.text)
async def non_photo_handler(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return

    await message.answer(NOT_PHOTO_TEXT)


@router.message(F.document)
async def document_handler(message: Message) -> None:
    if message.caption and message.caption.lstrip().startswith("/"):
        return

    await message.answer(DOCUMENT_TEXT)
