import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import signature
from typing import Any
from uuid import uuid4

from aiogram import Bot
from aiogram.methods import SendInvoice
from aiogram.types import LabeledPrice, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Payment, PaymentIntent, User

logger = logging.getLogger(__name__)
SUBSCRIPTION_PERIOD_SECONDS = settings.subscription_period
STARS_CURRENCY = "XTR"

TARIFFS = {
    "designer": {
        "title": "Designer",
        "price_stars": settings.designer_price_stars,
        "monthly_limit": settings.designer_monthly_limit,
        "description": f"{settings.designer_monthly_limit} распознаваний шрифтов в месяц",
    },
    "studio": {
        "title": "Studio",
        "price_stars": settings.studio_price_stars,
        "monthly_limit": settings.studio_monthly_limit,
        "description": f"{settings.studio_monthly_limit} распознаваний шрифтов в месяц",
    },
}


@dataclass(frozen=True)
class Tariff:
    key: str
    title: str
    price_stars: int
    monthly_limit: int
    description: str


@dataclass(frozen=True)
class InvoiceDeliveryResult:
    method: str
    invoice_link: str | None = None


class InvoiceCreationError(RuntimeError):
    pass


def ensure_send_invoice_subscription_period_support() -> None:
    if "subscription_period" in signature(Bot.send_invoice).parameters:
        return

    async def send_invoice_with_subscription_period(
        self: Bot,
        chat_id: int | str,
        title: str,
        description: str,
        payload: str,
        currency: str,
        prices: list[LabeledPrice],
        provider_token: str | None = None,
        subscription_period: int | None = None,
        request_timeout: int | None = None,
        **extra_data: Any,
    ) -> Any:
        return await self(
            SendInvoice(
                chat_id=chat_id,
                title=title,
                description=description,
                payload=payload,
                provider_token=provider_token,
                currency=currency,
                prices=prices,
                subscription_period=subscription_period,
                **extra_data,
            ),
            request_timeout=request_timeout,
        )

    Bot.send_invoice = send_invoice_with_subscription_period


def get_tariff(tariff: str) -> Tariff | None:
    normalized = tariff.lower().strip()
    tariff_config = TARIFFS.get(normalized)
    if tariff_config is not None:
        return Tariff(key=normalized, **tariff_config)
    return None


def tariff_title(tariff: str) -> str:
    plan = get_tariff(tariff)
    return plan.title if plan else tariff


def provider_token_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "provider_token" in message or "provider token" in message


def build_invoice_payload(tariff_key: str, payload: str) -> dict[str, Any]:
    if tariff_key not in TARIFFS:
        raise ValueError("Unknown tariff")

    tariff = TARIFFS[tariff_key]
    title = f"{tariff['title']} подписка"
    description = tariff["description"]

    if not 1 <= len(payload.encode("utf-8")) <= 128:
        raise ValueError("Invoice payload must be 1-128 bytes")
    if not 1 <= len(title) <= 32:
        raise ValueError("Invoice title must be 1-32 characters")
    if not 1 <= len(description) <= 255:
        raise ValueError("Invoice description must be 1-255 characters")

    prices = [
        LabeledPrice(
            label=f"{tariff['title']} на 30 дней",
            amount=tariff["price_stars"],
        )
    ]

    return {
        "title": title,
        "description": description,
        "payload": payload,
        "provider_token": "",
        "currency": STARS_CURRENCY,
        "prices": prices,
        "subscription_period": SUBSCRIPTION_PERIOD_SECONDS,
    }


def normalize_telegram_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return None


async def create_payment_intent(
    session: AsyncSession,
    telegram_id: int,
    tariff: str,
) -> PaymentIntent:
    plan = get_tariff(tariff)
    if plan is None:
        raise ValueError("Unknown tariff")

    intent = PaymentIntent(
        payload=f"sub:{plan.key}:{telegram_id}:{uuid4()}",
        telegram_id=telegram_id,
        tariff=plan.key,
        amount_stars=plan.price_stars,
        status="pending",
    )
    if len(intent.payload.encode("utf-8")) > 128:
        raise ValueError("Payment payload is longer than 128 bytes")
    session.add(intent)
    await session.flush()
    return intent


async def create_subscription_invoice_link(
    bot: Bot,
    payment_intent: PaymentIntent,
    *,
    omit_provider_token: bool = False,
) -> str:
    invoice_payload = build_invoice_payload(
        payment_intent.tariff,
        payment_intent.payload,
    )
    if omit_provider_token:
        invoice_payload.pop("provider_token", None)

    return await bot.create_invoice_link(**invoice_payload)


async def send_subscription_invoice(
    bot: Bot,
    chat_id: int,
    payment_intent: PaymentIntent,
) -> InvoiceDeliveryResult:
    ensure_send_invoice_subscription_period_support()

    invoice_payload = build_invoice_payload(
        payment_intent.tariff,
        payment_intent.payload,
    )

    try:
        await bot.send_invoice(chat_id=chat_id, **invoice_payload)
        return InvoiceDeliveryResult(method="send_invoice")
    except Exception as send_exc:
        logger.exception(
            "send_invoice failed: tariff=%s amount=%s payload=%s error=%s",
            payment_intent.tariff,
            payment_intent.amount_stars,
            payment_intent.payload,
            str(send_exc),
        )
        if provider_token_error(send_exc):
            without_provider_token = dict(invoice_payload)
            without_provider_token.pop("provider_token", None)
            try:
                await bot.send_invoice(chat_id=chat_id, **without_provider_token)
                return InvoiceDeliveryResult(method="send_invoice_without_provider_token")
            except Exception as retry_exc:
                logger.exception(
                    "send_invoice without provider_token failed: "
                    "tariff=%s amount=%s payload=%s error=%s",
                    payment_intent.tariff,
                    payment_intent.amount_stars,
                    payment_intent.payload,
                    str(retry_exc),
                )

        try:
            invoice_link = await create_subscription_invoice_link(
                bot,
                payment_intent,
                omit_provider_token=False,
            )
            return InvoiceDeliveryResult(
                method="create_invoice_link",
                invoice_link=invoice_link,
            )
        except Exception as link_exc:
            logger.exception(
                "create_invoice_link failed: tariff=%s amount=%s payload=%s error=%s",
                payment_intent.tariff,
                payment_intent.amount_stars,
                payment_intent.payload,
                str(link_exc),
            )
            if provider_token_error(link_exc):
                try:
                    invoice_link = await create_subscription_invoice_link(
                        bot,
                        payment_intent,
                        omit_provider_token=True,
                    )
                    return InvoiceDeliveryResult(
                        method="create_invoice_link_without_provider_token",
                        invoice_link=invoice_link,
                    )
                except Exception as link_retry_exc:
                    logger.exception(
                        "create_invoice_link without provider_token failed: "
                        "tariff=%s amount=%s payload=%s error=%s",
                        payment_intent.tariff,
                        payment_intent.amount_stars,
                        payment_intent.payload,
                        str(link_retry_exc),
                    )

        raise InvoiceCreationError("Unable to create Stars invoice") from send_exc


async def find_intent_by_payload(
    session: AsyncSession,
    payload: str,
) -> PaymentIntent | None:
    result = await session.execute(
        select(PaymentIntent).where(PaymentIntent.payload == payload)
    )
    return result.scalar_one_or_none()


async def validate_pre_checkout(
    session: AsyncSession,
    query: PreCheckoutQuery,
) -> tuple[bool, str | None, PaymentIntent | None]:
    intent = await find_intent_by_payload(session, query.invoice_payload)
    if intent is None:
        return False, "Платёж не найден. Создайте счёт заново через /subscribe.", None

    plan = get_tariff(intent.tariff)
    if plan is None:
        intent.status = "rejected"
        return False, "Тариф не найден. Создайте счёт заново через /subscribe.", intent

    if intent.status != "pending":
        return False, "Этот счёт уже обработан. Создайте новый через /subscribe.", intent

    if intent.telegram_id != query.from_user.id:
        intent.status = "rejected"
        return False, "Этот счёт создан для другого пользователя.", intent

    if query.currency != STARS_CURRENCY:
        intent.status = "rejected"
        return False, "Неверная валюта платежа.", intent

    if query.total_amount != intent.amount_stars:
        intent.status = "rejected"
        return False, "Неверная сумма платежа.", intent

    if intent.amount_stars != plan.price_stars:
        intent.status = "rejected"
        return False, "Неверная сумма платежа.", intent

    return True, None, intent


async def payment_exists(
    session: AsyncSession,
    invoice_payload: str,
    telegram_payment_charge_id: str | None,
) -> Payment | None:
    if telegram_payment_charge_id:
        result = await session.execute(
            select(Payment).where(
                Payment.telegram_payment_charge_id == telegram_payment_charge_id
            )
        )
        payment = result.scalar_one_or_none()
        if payment is not None:
            return payment

    result = await session.execute(
        select(Payment).where(Payment.invoice_payload == invoice_payload)
    )
    return result.scalar_one_or_none()


async def save_successful_payment(
    session: AsyncSession,
    user: User,
    successful_payment: object,
) -> tuple[Payment, PaymentIntent]:
    from app.access import activate_plan

    intent = await find_intent_by_payload(
        session,
        successful_payment.invoice_payload,
    )
    if intent is None:
        raise ValueError("Payment intent not found")

    plan = get_tariff(intent.tariff)
    if plan is None:
        raise ValueError("Unknown tariff")

    telegram_payment_charge_id = (
        getattr(successful_payment, "telegram_payment_charge_id", None) or None
    )
    existing = await payment_exists(
        session,
        successful_payment.invoice_payload,
        telegram_payment_charge_id,
    )
    if existing is not None:
        intent.status = "paid"
        activate_plan(user, intent.tariff, existing.subscription_expiration_date)
        user.subscription_payment_charge_id = existing.telegram_payment_charge_id
        user.subscription_payload = existing.invoice_payload
        return existing, intent

    payment = Payment(
        telegram_id=user.telegram_id,
        tariff=plan.key,
        amount_stars=successful_payment.total_amount,
        currency=successful_payment.currency,
        invoice_payload=successful_payment.invoice_payload,
        telegram_payment_charge_id=telegram_payment_charge_id,
        provider_payment_charge_id=(
            getattr(successful_payment, "provider_payment_charge_id", None) or None
        ),
        subscription_expiration_date=normalize_telegram_datetime(
            getattr(successful_payment, "subscription_expiration_date", None)
        ),
        is_recurring=getattr(successful_payment, "is_recurring", None),
        is_first_recurring=getattr(successful_payment, "is_first_recurring", None),
    )
    session.add(payment)
    intent.status = "paid"
    activate_plan(user, intent.tariff, payment.subscription_expiration_date)
    user.subscription_payment_charge_id = payment.telegram_payment_charge_id
    user.subscription_payload = payment.invoice_payload
    await session.flush()
    return payment, intent
