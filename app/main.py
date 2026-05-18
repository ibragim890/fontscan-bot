import asyncio
import json
import logging
import os
from urllib.parse import parse_qs

from aiogram import Bot, Dispatcher
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
import uvicorn

from app.access import activate_plan, now_utc
from app.config import settings
from app.db import DbSessionMiddleware, async_session_factory, engine, init_db
from app.handlers import admin, payments, photo, start, status, support
from app.models import ExternalPayment, ExternalPaymentIntent, User
from app.payments import (
    calculate_robokassa_result_signature,
    ensure_send_invoice_subscription_period_support,
    get_tariff,
    robokassa_debug_lines,
    verify_robokassa_result_signature,
)


logger = logging.getLogger(__name__)
web_app = FastAPI()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@web_app.exception_handler(Exception)
async def fastapi_exception_handler(request: Request, exc: Exception) -> PlainTextResponse:
    logger.exception(
        "Unhandled FastAPI exception: method=%s url=%s error=%s",
        request.method,
        request.url,
        exc.__class__.__name__,
    )
    return PlainTextResponse("internal error")


async def read_robokassa_params(request: Request) -> tuple[dict[str, str], str]:
    params = {key: value for key, value in request.query_params.items()}
    raw_body = ""
    if request.method == "POST":
        body = await request.body()
        raw_body = body.decode("utf-8", errors="replace")
        if raw_body:
            parsed_body = parse_qs(
                raw_body,
                keep_blank_values=True,
                encoding="utf-8",
                errors="replace",
            )
            params.update(
                {
                    key: values[-1] if values else ""
                    for key, values in parsed_body.items()
                }
            )
    return params, raw_body


@web_app.get("/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("OK")


@web_app.get("/debug/robokassa")
async def debug_robokassa() -> PlainTextResponse:
    try:
        return PlainTextResponse("\n".join(robokassa_debug_lines()))
    except Exception:
        logger.exception("Failed to render Robokassa debug endpoint")
        return PlainTextResponse("internal error")


@web_app.api_route("/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request) -> PlainTextResponse:
    try:
        params, raw_body = await read_robokassa_params(request)
        out_sum = params.get("OutSum", "")
        inv_id_raw = params.get("InvId", "")
        signature_value = params.get("SignatureValue", "")
        calculated_signature = calculate_robokassa_result_signature(
            out_sum,
            inv_id_raw,
        )
        signature_matches = verify_robokassa_result_signature(
            out_sum,
            inv_id_raw,
            signature_value,
        )

        logger.info(
            "Robokassa result request: method=%s query_params=%s body=%s "
            "out_sum=%s inv_id=%s signature_value=%s signature_match=%s",
            request.method,
            dict(request.query_params),
            raw_body,
            out_sum,
            inv_id_raw,
            signature_value,
            signature_matches,
        )
        logger.info(
            "Robokassa signature debug: out_sum=%s inv_id=%s received=%s calculated=%s",
            out_sum,
            inv_id_raw,
            signature_value,
            calculated_signature,
        )

        if not out_sum or not inv_id_raw or not signature_value:
            logger.warning("Robokassa result rejected: missing required params")
            return PlainTextResponse("bad sign")

        if not signature_matches:
            logger.warning(
                "Robokassa result rejected: bad signature inv_id=%s",
                inv_id_raw,
            )
            return PlainTextResponse("bad sign")

        try:
            inv_id = int(inv_id_raw)
        except ValueError:
            logger.warning("Robokassa result rejected: invalid InvId=%s", inv_id_raw)
            return PlainTextResponse("order not found")

        try:
            async with async_session_factory() as session:
                intent = await session.get(ExternalPaymentIntent, inv_id)
                if intent is None or intent.provider != "robokassa":
                    logger.warning("Robokassa order not found: inv_id=%s", inv_id)
                    return PlainTextResponse("order not found")

                if intent.status == "paid":
                    logger.info("Robokassa order already paid: inv_id=%s", inv_id)
                    return PlainTextResponse(f"OK{inv_id_raw}")

                plan = await get_tariff(session, intent.tariff, active_only=False)
                if plan is None:
                    logger.error(
                        "Robokassa order tariff not found: inv_id=%s tariff=%s",
                        inv_id,
                        intent.tariff,
                    )
                    return PlainTextResponse("order not found")

                user_result = await session.execute(
                    select(User).where(User.telegram_id == intent.telegram_id)
                )
                user = user_result.scalar_one_or_none()
                if user is None:
                    user = User(telegram_id=intent.telegram_id)
                    session.add(user)
                    await session.flush()

                payment = ExternalPayment(
                    provider="robokassa",
                    telegram_id=intent.telegram_id,
                    tariff=plan.code,
                    amount_rub=intent.amount_rub,
                    inv_id=inv_id,
                    out_sum=out_sum,
                    signature_value=signature_value,
                    raw_payload=json.dumps(
                        {
                            "method": request.method,
                            "query": dict(request.query_params),
                            "body": raw_body,
                            "params": params,
                        },
                        ensure_ascii=False,
                    ),
                )
                session.add(payment)
                intent.status = "paid"
                intent.provider_invoice_id = inv_id_raw
                intent.paid_at = now_utc()

                try:
                    activate_plan(user, plan.code, plan.monthly_limit)
                except Exception:
                    logger.exception(
                        "Robokassa activate_plan failed: inv_id=%s user=%s tariff=%s",
                        inv_id,
                        intent.telegram_id,
                        plan.code,
                    )
                    await session.rollback()
                    return PlainTextResponse("internal error")

                await session.commit()
                telegram_id = intent.telegram_id
                tariff_title = plan.title
                monthly_limit = plan.monthly_limit
        except Exception:
            logger.exception("Robokassa result DB processing failed")
            return PlainTextResponse("internal error")

        bot = getattr(request.app.state, "bot", None)
        if bot is not None:
            try:
                await bot.send_message(
                    telegram_id,
                    "Оплата прошла ✅\n\n"
                    f"Тариф: {tariff_title}\n"
                    "Доступ активирован на 30 дней.\n"
                    f"Распознаваний: {monthly_limit}\n\n"
                    "Теперь отправьте фото со шрифтом.",
                )
            except Exception:
                logger.exception(
                    "Failed to notify user about Robokassa payment: user=%s",
                    telegram_id,
                )

        return PlainTextResponse(f"OK{inv_id_raw}")
    except Exception:
        logger.exception("Robokassa result endpoint failed")
        return PlainTextResponse("internal error")


@web_app.get("/robokassa/success")
async def robokassa_success() -> HTMLResponse:
    try:
        return HTMLResponse("Оплата прошла успешно.<br>Вернитесь в Telegram.")
    except Exception:
        logger.exception("Robokassa success endpoint failed")
        return HTMLResponse("Оплата прошла успешно.<br>Вернитесь в Telegram.")


@web_app.get("/robokassa/fail")
async def robokassa_fail() -> HTMLResponse:
    try:
        return HTMLResponse("Оплата не завершена.<br>Попробуйте ещё раз.")
    except Exception:
        logger.exception("Robokassa fail endpoint failed")
        return HTMLResponse("Оплата не завершена.<br>Попробуйте ещё раз.")


def build_dispatcher() -> Dispatcher:
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
    return dp


async def start_web_server() -> None:
    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def start_bot_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def run_logged_task(name: str, coro: object) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("%s task failed", name)
        raise


async def run() -> None:
    configure_logging()

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not settings.whatfontis_api_keys:
        raise RuntimeError("WHATFONTIS_API_KEYS is not set")

    ensure_send_invoice_subscription_period_support()
    await init_db()

    bot = Bot(token=settings.bot_token)
    web_app.state.bot = bot
    dp = build_dispatcher()

    tasks: list[asyncio.Task[object]] = []
    try:
        tasks = [
            asyncio.create_task(run_logged_task("polling", start_bot_polling(bot, dp))),
            asyncio.create_task(run_logged_task("uvicorn", start_web_server())),
        ]
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
