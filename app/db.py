from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    import app.models  # noqa: F401

    logger.info("Database init started")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns = await conn.execute(text("PRAGMA table_info(font_requests)"))
        column_names = {row[1] for row in columns.fetchall()}
        if "is_cached_response" not in column_names:
            await conn.execute(
                text(
                    "ALTER TABLE font_requests "
                    "ADD COLUMN is_cached_response BOOLEAN DEFAULT 0 NOT NULL"
                )
            )

        now = datetime.now(timezone.utc)
        default_tariffs = [
            (
                "designer",
                "Designer",
                settings.designer_price_stars,
                settings.designer_monthly_limit,
            ),
            (
                "studio",
                "Studio",
                settings.studio_price_stars,
                settings.studio_monthly_limit,
            ),
        ]
        for code, title, price_stars, monthly_limit in default_tariffs:
            await conn.execute(
                text(
                    "INSERT INTO tariffs "
                    "(code, title, price_stars, monthly_limit, is_active, "
                    "created_at, updated_at) "
                    "SELECT :code, :title, :price_stars, :monthly_limit, 1, "
                    ":created_at, :updated_at "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM tariffs WHERE code = :code"
                    ")"
                ),
                {
                    "code": code,
                    "title": title,
                    "price_stars": price_stars,
                    "monthly_limit": monthly_limit,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        default_settings = [
            ("trial_days", str(settings.trial_days)),
            ("trial_requests_limit", str(settings.trial_requests_limit)),
        ]
        for key, value in default_settings:
            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, updated_at) "
                    "SELECT :key, :value, :updated_at "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM app_settings WHERE key = :key"
                    ")"
                ),
                {"key": key, "value": value, "updated_at": now},
            )

        from app.texts import (
            DEFAULT_BOT_TEXTS,
            FORCED_TEXT_UPDATE_KEY,
            FORCED_TEXT_UPDATE_KEYS,
        )

        forced_update_exists = await conn.execute(
            text("SELECT 1 FROM app_settings WHERE key = :key"),
            {"key": FORCED_TEXT_UPDATE_KEY},
        )
        should_force_text_update = forced_update_exists.first() is None
        logger.info(
            "Bot text migration check: key=%s should_force_update=%s",
            FORCED_TEXT_UPDATE_KEY,
            should_force_text_update,
        )

        for key, (title, text_value) in DEFAULT_BOT_TEXTS.items():
            await conn.execute(
                text(
                    "INSERT INTO bot_texts "
                    "(key, title, text, created_at, updated_at) "
                    "SELECT :key, :title, :text, :created_at, :updated_at "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM bot_texts WHERE key = :key"
                    ")"
                ),
                {
                    "key": key,
                    "title": title,
                    "text": text_value,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if should_force_text_update and key in FORCED_TEXT_UPDATE_KEYS:
                result = await conn.execute(
                    text(
                        "UPDATE bot_texts "
                        "SET title = :title, text = :new_text, updated_at = :updated_at "
                        "WHERE key = :key"
                    ),
                    {
                        "key": key,
                        "title": title,
                        "new_text": text_value,
                        "updated_at": now,
                    },
                )
                logger.info(
                    "Bot text forced update: key=%s rows=%s",
                    key,
                    result.rowcount,
                )

        if should_force_text_update:
            await conn.execute(
                text(
                    "UPDATE bot_texts "
                    "SET text = replace(text, 'Telegram Stars', 'Робокассу'), "
                    "updated_at = :updated_at "
                    "WHERE text LIKE '%Telegram Stars%'"
                ),
                {"updated_at": now},
            )
            result = await conn.execute(
                text(
                    "UPDATE bot_texts "
                    "SET text = replace(text, ' Stars', ' ₽'), "
                    "updated_at = :updated_at "
                    "WHERE text LIKE '% Stars%'"
                ),
                {"updated_at": now},
            )
            logger.info(
                "Bot text Stars-to-rub cleanup: rows=%s",
                result.rowcount,
            )
            result = await conn.execute(
                text(
                    "UPDATE bot_texts "
                    "SET text = replace(text, 'Stars', '₽'), "
                    "updated_at = :updated_at "
                    "WHERE text LIKE '%Stars%'"
                ),
                {"updated_at": now},
            )
            logger.info(
                "Bot text remaining Stars cleanup: rows=%s",
                result.rowcount,
            )

            main_menu_title, main_menu_text = DEFAULT_BOT_TEXTS["main_menu"]
            result = await conn.execute(
                text(
                    "UPDATE bot_texts "
                    "SET title = :title, text = :new_text, updated_at = :updated_at "
                    "WHERE key = :key"
                ),
                {
                    "key": "start_message",
                    "title": main_menu_title,
                    "new_text": main_menu_text,
                    "updated_at": now,
                },
            )
            logger.info(
                "Bot text forced update alias: key=start_message rows=%s",
                result.rowcount,
            )

            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, updated_at) "
                    "VALUES (:key, :value, :updated_at)"
                ),
                {
                    "key": FORCED_TEXT_UPDATE_KEY,
                    "value": "1",
                    "updated_at": now,
                },
            )
            logger.info(
                "Bot text migration marker stored: key=%s",
                FORCED_TEXT_UPDATE_KEY,
            )
    logger.info("Database init finished")


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
