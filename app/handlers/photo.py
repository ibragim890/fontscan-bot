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
    now_utc,
    start_trial_if_needed,
    user_can_make_request,
)
from app.config import settings
from app.font_services.whatfontis import WhatFontIsClient
from app.keyboards import no_access_subscription_keyboard, result_actions_keyboard
from app.models import ApiKeyUsage, FontRequest
from app.texts import (
    DOWNLOAD_ERROR_TEXT,
    NOT_PHOTO_TEXT,
    PROCESSING_TEXT,
    TEMP_UNAVAILABLE_TEXT,
    font_result_text,
)

logger = logging.getLogger(__name__)
router = Router(name="photo")


@router.message(F.photo)
async def photo_handler(message: Message, session: AsyncSession) -> None:
    if message.caption and message.caption.lstrip().startswith("/"):
        return

    user = await get_or_create_user(session, message.from_user)
    trial_config = await get_trial_config(session)
    start_trial_if_needed(user, trial_config.days)

    if not user_can_make_request(user, trial_config.requests_limit):
        await message.answer(
            await get_no_access_text(session),
            reply_markup=no_access_subscription_keyboard(),
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
                counted_as_usage=False,
                is_cached_response=True,
            )
        )
        await safe_edit_text(
            processing_message,
            await font_result_text(session, cached_request.top_font),
            reply_markup=result_actions_keyboard(),
        )
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

    if result.counted_as_usage:
        increment_usage(user, trial_config.requests_limit)

    font_request = FontRequest(
        telegram_id=message.from_user.id,
        provider="whatfontis",
        image_hash=image_hash,
        top_font=result.title,
        result_json=json.dumps(result.result_json, ensure_ascii=False),
        status=result.status,
        counted_as_usage=result.counted_as_usage,
        is_cached_response=False,
    )
    session.add(font_request)

    if not result.counted_as_usage:
        await safe_edit_text(
            processing_message,
            result.user_message or TEMP_UNAVAILABLE_TEXT,
        )
        return

    await safe_edit_text(
        processing_message,
        await font_result_text(session, result.title),
        reply_markup=result_actions_keyboard(),
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
    if message.text and message.text.lstrip().startswith("/"):
        return

    await message.answer(NOT_PHOTO_TEXT)
