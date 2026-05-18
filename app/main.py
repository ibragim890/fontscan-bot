import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.config import settings
from app.db import DbSessionMiddleware, async_session_factory, engine, init_db
from app.handlers import admin, payments, photo, start, status, support
from app.payments import ensure_send_invoice_subscription_period_support


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run() -> None:
    configure_logging()

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not settings.whatfontis_api_keys:
        raise RuntimeError("WHATFONTIS_API_KEYS is not set")

    ensure_send_invoice_subscription_period_support()
    await init_db()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    db_middleware = DbSessionMiddleware()
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)
    dp.pre_checkout_query.middleware(db_middleware)

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(status.router)
    dp.include_router(support.router)
    dp.include_router(payments.router)
    dp.include_router(photo.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
