from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy import inspect, text
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


def database_dialect() -> str:
    return engine.url.get_backend_name()


def quote_sqlite_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError("Invalid SQL identifier")
    return '"' + identifier.replace('"', '""') + '"'


async def get_table_columns(conn: Any, table_name: str) -> set[str]:
    dialect = database_dialect()
    if dialect == "sqlite":
        result = await conn.execute(
            text(f"PRAGMA table_info({quote_sqlite_identifier(table_name)})")
        )
        return {row[1] for row in result.fetchall()}

    if dialect == "postgresql":
        result = await conn.execute(
            text(
                "SELECT column_name "
                "FROM information_schema.columns "
                "WHERE table_name = :table_name "
                "AND table_schema = current_schema()"
            ),
            {"table_name": table_name},
        )
        return {row[0] for row in result.fetchall()}

    def inspect_columns(sync_conn: Any) -> set[str]:
        inspector = inspect(sync_conn)
        return {column["name"] for column in inspector.get_columns(table_name)}

    return await conn.run_sync(inspect_columns)


def datetime_column_type() -> str:
    if database_dialect() == "postgresql":
        return "TIMESTAMP WITH TIME ZONE"
    return "DATETIME"


def boolean_not_null_default_column_type(default: bool = False) -> str:
    if database_dialect() == "postgresql":
        return f"BOOLEAN DEFAULT {'TRUE' if default else 'FALSE'} NOT NULL"
    return f"BOOLEAN DEFAULT {1 if default else 0} NOT NULL"


async def force_rub_payment_ui_texts(db: Any) -> None:
    from app.texts import DEFAULT_BOT_TEXTS, FORCED_TEXT_UPDATE_KEYS

    now = datetime.now(timezone.utc)

    for key in FORCED_TEXT_UPDATE_KEYS:
        if key not in DEFAULT_BOT_TEXTS:
            continue
        title, text_value = DEFAULT_BOT_TEXTS[key]
        await db.execute(
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

    main_menu_title, main_menu_text = DEFAULT_BOT_TEXTS["main_menu"]
    await db.execute(
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

    replacements = [
        ("Telegram Stars", "Робокассу"),
        ("99 Stars", "99 ₽"),
        ("199 Stars", "199 ₽"),
        (" Stars", " ₽"),
        ("Stars", "₽"),
    ]
    for old_value, new_value in replacements:
        result = await db.execute(
            text(
                "UPDATE bot_texts "
                "SET text = replace(text, :old_value, :new_value), "
                "updated_at = :updated_at "
                "WHERE text LIKE :pattern"
            ),
            {
                "old_value": old_value,
                "new_value": new_value,
                "updated_at": now,
                "pattern": f"%{old_value}%",
            },
        )
        logger.info(
            "Bot text rub UI cleanup: old=%s rows=%s",
            old_value,
            result.rowcount,
        )


async def init_db() -> None:
    import app.models  # noqa: F401

    dialect = database_dialect()
    logger.info("Database init started: dialect=%s", dialect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        column_names = await get_table_columns(conn, "font_requests")
        if "is_cached_response" not in column_names:
            await conn.execute(
                text(
                    "ALTER TABLE font_requests "
                    "ADD COLUMN is_cached_response "
                    f"{boolean_not_null_default_column_type()}"
                )
            )
        if "result_type" not in column_names:
            await conn.execute(
                text(
                    "ALTER TABLE font_requests "
                    "ADD COLUMN result_type VARCHAR(64) DEFAULT 'unknown' NOT NULL"
                )
            )
        if "provider_success" not in column_names:
            await conn.execute(
                text(
                    "ALTER TABLE font_requests "
                    "ADD COLUMN provider_success "
                    f"{boolean_not_null_default_column_type()}"
                )
            )

        user_column_names = await get_table_columns(conn, "users")
        user_columns_to_add = {
            "source": "VARCHAR(255)",
            "referred_by": "VARCHAR(255)",
            "first_photo_at": datetime_column_type(),
            "paywall_hit_at": datetime_column_type(),
            "payment_opened_at": datetime_column_type(),
            "recognition_balance": "INTEGER DEFAULT 0 NOT NULL",
            "launch_offer_started_at": datetime_column_type(),
            "launch_offer_ends_at": datetime_column_type(),
            "launch_offer_purchased": boolean_not_null_default_column_type(),
            "launch_offer_reminder_6h_sent": boolean_not_null_default_column_type(),
            "launch_offer_reminder_12h_sent": boolean_not_null_default_column_type(),
            "launch_offer_reminder_18h_sent": boolean_not_null_default_column_type(),
            "launch_offer_reminder_24h_sent": boolean_not_null_default_column_type(),
        }
        for column_name, column_type in user_columns_to_add.items():
            if column_name not in user_column_names:
                await conn.execute(
                    text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                )

        external_intent_column_names = await get_table_columns(
            conn,
            "external_payment_intents",
        )
        external_intent_columns_to_add = {
            "recognitions_count": "INTEGER",
            "offer_broadcast_sent_at": datetime_column_type(),
            "offer_broadcast_clicked_at": datetime_column_type(),
        }
        for column_name, column_type in external_intent_columns_to_add.items():
            if column_name not in external_intent_column_names:
                await conn.execute(
                    text(
                        "ALTER TABLE external_payment_intents "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        for table_name in (
            "payments",
            "recognition_packages",
            "recognition_transactions",
        ):
            logger.info(
                "Database table column check: table=%s columns=%s",
                table_name,
                len(await get_table_columns(conn, table_name)),
            )

        await conn.execute(
            text(
                "UPDATE users "
                "SET first_photo_at = ("
                "SELECT MIN(font_requests.created_at) "
                "FROM font_requests "
                "WHERE font_requests.telegram_id = users.telegram_id"
                ") "
                "WHERE first_photo_at IS NULL "
                "AND EXISTS ("
                "SELECT 1 FROM font_requests "
                "WHERE font_requests.telegram_id = users.telegram_id"
                ")"
            )
        )
        await conn.execute(
            text(
                "UPDATE users "
                "SET payment_opened_at = ("
                "SELECT MIN(external_payment_intents.created_at) "
                "FROM external_payment_intents "
                "WHERE external_payment_intents.telegram_id = users.telegram_id"
                ") "
                "WHERE payment_opened_at IS NULL "
                "AND EXISTS ("
                "SELECT 1 FROM external_payment_intents "
                "WHERE external_payment_intents.telegram_id = users.telegram_id"
                ")"
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
                    "SELECT :code, :title, :price_stars, :monthly_limit, :is_active, "
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
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        package_tariffs = [
            ("founder_offer", "Founder offer", 99, 50),
            ("founder_regular", "Founder regular", 199, 50),
        ]
        for code, title, price_rub, recognitions_count in package_tariffs:
            await conn.execute(
                text(
                    "INSERT INTO tariffs "
                    "(code, title, price_stars, monthly_limit, is_active, "
                    "created_at, updated_at) "
                    "SELECT :code, :title, :price_rub, :recognitions_count, "
                    ":is_active, "
                    ":created_at, :updated_at "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM tariffs WHERE code = :code"
                    ")"
                ),
                {
                    "code": code,
                    "title": title,
                    "price_rub": price_rub,
                    "recognitions_count": recognitions_count,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await conn.execute(
                text(
                    "UPDATE tariffs "
                    "SET title = :title, price_stars = :price_rub, "
                    "monthly_limit = :recognitions_count, "
                    "is_active = :is_active, "
                    "updated_at = :updated_at "
                    "WHERE code = :code"
                ),
                {
                    "code": code,
                    "title": title,
                    "price_rub": price_rub,
                    "recognitions_count": recognitions_count,
                    "is_active": True,
                    "updated_at": now,
                },
            )

        default_settings = [
            ("trial_limit", "1"),
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
            ACCESS_TRIAL_TEXT_UPDATE_KEY,
            DEFAULT_BOT_TEXTS,
            FORCED_TEXT_UPDATE_KEY,
            FORCED_TEXT_UPDATE_KEYS,
            USAGE_RESULT_TEXT_UPDATE_KEY,
            USAGE_RESULT_TEXT_UPDATE_KEYS,
        )

        forced_update_exists = await conn.execute(
            text("SELECT 1 FROM app_settings WHERE key = :key"),
            {"key": FORCED_TEXT_UPDATE_KEY},
        )
        should_force_text_update = forced_update_exists.first() is None
        access_trial_update_exists = await conn.execute(
            text("SELECT 1 FROM app_settings WHERE key = :key"),
            {"key": ACCESS_TRIAL_TEXT_UPDATE_KEY},
        )
        should_force_access_trial_update = access_trial_update_exists.first() is None
        usage_result_update_exists = await conn.execute(
            text("SELECT 1 FROM app_settings WHERE key = :key"),
            {"key": USAGE_RESULT_TEXT_UPDATE_KEY},
        )
        should_force_usage_result_update = usage_result_update_exists.first() is None
        logger.info(
            "Bot text migration check: key=%s should_force_update=%s",
            FORCED_TEXT_UPDATE_KEY,
            should_force_text_update,
        )
        logger.info(
            "Access trial migration check: key=%s should_force_update=%s",
            ACCESS_TRIAL_TEXT_UPDATE_KEY,
            should_force_access_trial_update,
        )
        logger.info(
            "Usage result migration check: key=%s should_force_update=%s",
            USAGE_RESULT_TEXT_UPDATE_KEY,
            should_force_usage_result_update,
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
            should_update_text = (
                should_force_text_update and key in FORCED_TEXT_UPDATE_KEYS
            ) or (
                should_force_access_trial_update and key in FORCED_TEXT_UPDATE_KEYS
            ) or (
                should_force_usage_result_update
                and key in USAGE_RESULT_TEXT_UPDATE_KEYS
            )
            if should_update_text:
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
            await force_rub_payment_ui_texts(conn)

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
        if should_force_access_trial_update:
            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, updated_at) "
                    "VALUES (:key, :value, :updated_at)"
                ),
                {
                    "key": ACCESS_TRIAL_TEXT_UPDATE_KEY,
                    "value": "1",
                    "updated_at": now,
                },
            )
            logger.info(
                "Access trial migration marker stored: key=%s",
                ACCESS_TRIAL_TEXT_UPDATE_KEY,
            )
        if should_force_usage_result_update:
            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, updated_at) "
                    "VALUES (:key, :value, :updated_at)"
                ),
                {
                    "key": USAGE_RESULT_TEXT_UPDATE_KEY,
                    "value": "1",
                    "updated_at": now,
                },
            )
            logger.info(
                "Usage result migration marker stored: key=%s",
                USAGE_RESULT_TEXT_UPDATE_KEY,
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
