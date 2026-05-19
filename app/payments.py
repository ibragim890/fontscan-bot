import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5
from inspect import signature
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from aiogram import Bot
from aiogram.methods import SendInvoice
from aiogram.types import LabeledPrice, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    ExternalPaymentIntent,
    Payment,
    PaymentIntent,
    Tariff as TariffModel,
    User,
)

logger = logging.getLogger(__name__)
SUBSCRIPTION_PERIOD_SECONDS = settings.subscription_period
STARS_CURRENCY = "XTR"
SUBSCRIPTION_EXPORT_MISSING = "SUBSCRIPTION_EXPORT_MISSING"


@dataclass(frozen=True)
class TariffPlan:
    code: str
    title: str
    price_stars: int
    monthly_limit: int
    description: str

    @property
    def key(self) -> str:
        return self.code

    @property
    def price_rub(self) -> int:
        return self.price_stars

    @property
    def recognitions_count(self) -> int:
        return self.monthly_limit


@dataclass(frozen=True)
class InvoiceDeliveryResult:
    method: str
    invoice_link: str | None = None


class InvoiceCreationError(RuntimeError):
    pass


class RobokassaPaymentUnavailableError(RuntimeError):
    pass


def ensure_send_invoice_subscription_period_support() -> None:
    parameters = signature(Bot.send_invoice).parameters
    if (
        "subscription_period" in parameters
        and "subscription_product_id" in parameters
    ):
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
        subscription_product_id: str | None = None,
        request_timeout: int | None = None,
        **extra_data: Any,
    ) -> Any:
        if subscription_product_id:
            extra_data["subscription_product_id"] = subscription_product_id
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


def tariff_from_model(tariff: TariffModel) -> TariffPlan:
    description = (
        f"{tariff.monthly_limit} распознаваний"
        if tariff.code in {"founder_offer", "founder_regular"}
        else f"{tariff.monthly_limit} распознаваний шрифтов в месяц"
    )
    return TariffPlan(
        code=tariff.code,
        title=tariff.title,
        price_stars=tariff.price_stars,
        monthly_limit=tariff.monthly_limit,
        description=description,
    )


async def get_tariff(
    session: AsyncSession,
    tariff: str,
    *,
    active_only: bool = True,
) -> TariffPlan | None:
    normalized = tariff.lower().strip()
    query = select(TariffModel).where(TariffModel.code == normalized)
    if active_only:
        query = query.where(TariffModel.is_active.is_(True))
    result = await session.execute(query)
    tariff_model = result.scalar_one_or_none()
    return tariff_from_model(tariff_model) if tariff_model is not None else None


async def list_tariffs(
    session: AsyncSession,
    *,
    active_only: bool = False,
) -> list[TariffPlan]:
    query = select(TariffModel).order_by(TariffModel.id)
    if active_only:
        query = query.where(TariffModel.is_active.is_(True))
    result = await session.execute(query)
    return [tariff_from_model(tariff) for tariff in result.scalars().all()]


async def tariff_title(session: AsyncSession, tariff: str) -> str:
    plan = await get_tariff(session, tariff, active_only=False)
    return plan.title if plan else tariff


def provider_token_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "provider_token" in message or "provider token" in message


def subscription_export_missing_error(exc: Exception) -> bool:
    return SUBSCRIPTION_EXPORT_MISSING.lower() in str(exc).lower()


def invoice_prices_for_log(invoice_payload: dict[str, Any]) -> list[dict[str, Any]]:
    prices = invoice_payload.get("prices") or []
    return [
        {
            "label": getattr(price, "label", None),
            "amount": getattr(price, "amount", None),
        }
        for price in prices
    ]


def log_invoice_payload(
    stage: str,
    chat_id: int | str | None,
    payment_intent: PaymentIntent,
    invoice_payload: dict[str, Any],
) -> None:
    logger.info(
        "Stars invoice payload: stage=%s chat_id=%s tariff=%s amount=%s "
        "payload=%s currency=%s provider_token_empty=%s prices=%s "
        "subscription_period=%s subscription_product_id_present=%s",
        stage,
        chat_id,
        payment_intent.tariff,
        payment_intent.amount_stars,
        payment_intent.payload,
        invoice_payload.get("currency"),
        invoice_payload.get("provider_token") == "",
        invoice_prices_for_log(invoice_payload),
        invoice_payload.get("subscription_period"),
        bool(invoice_payload.get("subscription_product_id")),
    )


def without_subscription_fields(invoice_payload: dict[str, Any]) -> dict[str, Any]:
    ordinary_payload = dict(invoice_payload)
    ordinary_payload.pop("subscription_period", None)
    ordinary_payload.pop("subscription_product_id", None)
    return ordinary_payload


def build_invoice_payload(plan: TariffPlan, payload: str) -> dict[str, Any]:
    title = f"{plan.title} подписка"
    description = plan.description

    if not 1 <= len(payload.encode("utf-8")) <= 128:
        raise ValueError("Invoice payload must be 1-128 bytes")
    if not 1 <= len(title) <= 32:
        raise ValueError("Invoice title must be 1-32 characters")
    if not 1 <= len(description) <= 255:
        raise ValueError("Invoice description must be 1-255 characters")

    prices = [
        LabeledPrice(
            label=f"{plan.title} на 30 дней",
            amount=plan.price_stars,
        )
    ]

    invoice_payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "payload": payload,
        "provider_token": "",
        "currency": STARS_CURRENCY,
        "prices": prices,
    }

    subscription_product_id = settings.subscription_product_id.strip()
    if subscription_product_id:
        invoice_payload["subscription_period"] = SUBSCRIPTION_PERIOD_SECONDS
        invoice_payload["subscription_product_id"] = subscription_product_id
    else:
        logger.info(
            "Telegram Stars subscription product is not configured; "
            "sending ordinary Stars invoice without subscription_period"
        )

    return invoice_payload


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


def robokassa_payment_enabled() -> bool:
    return settings.robokassa_enabled


def robokassa_payment_available() -> bool:
    return settings.robokassa_enabled and settings.robokassa_is_configured


def make_md5_signature(value: str) -> str:
    return md5(value.encode("utf-8")).hexdigest()


def make_robokassa_payment_url(
    intent: ExternalPaymentIntent,
    tariff_title: str,
) -> str:
    out_sum = str(intent.amount_rub)
    inv_id = str(intent.id)
    signature_value = make_md5_signature(
        ":".join(
            (
                settings.robokassa_merchant_login.strip(),
                out_sum,
                inv_id,
                settings.robokassa_password1.strip(),
            )
        )
    )
    params = {
        "MerchantLogin": settings.robokassa_merchant_login.strip(),
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": f"{tariff_title}: распознавания шрифтов",
        "SignatureValue": signature_value,
        "Culture": "ru",
        "Encoding": "utf-8",
    }
    if settings.robokassa_test_mode:
        params["IsTest"] = "1"

    return f"{settings.robokassa_base_url.strip()}?{urlencode(params)}"


def verify_robokassa_result_signature(
    out_sum: str,
    inv_id: str,
    signature_value: str,
) -> bool:
    expected_signature = calculate_robokassa_result_signature(out_sum, inv_id)
    return (
        bool(expected_signature)
        and expected_signature.lower() == signature_value.lower()
    )


def calculate_robokassa_result_signature(out_sum: str, inv_id: str) -> str:
    if not settings.robokassa_password2.strip():
        return ""

    return make_md5_signature(
        ":".join(
            (
                out_sum,
                inv_id,
                settings.robokassa_password2.strip(),
            )
        )
    )


def robokassa_webhook_urls() -> dict[str, str]:
    public_base_url = settings.public_base_url.strip().rstrip("/")
    if not public_base_url:
        return {
            "result_url": "",
            "success_url": "",
            "fail_url": "",
        }
    return {
        "result_url": f"{public_base_url}/robokassa/result",
        "success_url": f"{public_base_url}/robokassa/success",
        "fail_url": f"{public_base_url}/robokassa/fail",
    }


def robokassa_debug_lines() -> list[str]:
    urls = robokassa_webhook_urls()
    return [
        f"ROBOKASSA_ENABLED={settings.robokassa_enabled}",
        f"ROBOKASSA_TEST_MODE={settings.robokassa_test_mode}",
        f"PUBLIC_BASE_URL={settings.public_base_url.strip()}",
        f"Result URL={urls['result_url']}",
        f"Success URL={urls['success_url']}",
        f"Fail URL={urls['fail_url']}",
        f"merchant_login_exists={bool(settings.robokassa_merchant_login.strip())}",
        f"password1_exists={bool(settings.robokassa_password1.strip())}",
        f"password2_exists={bool(settings.robokassa_password2.strip())}",
    ]


async def create_robokassa_payment(
    session: AsyncSession,
    telegram_id: int,
    tariff_code: str,
) -> str:
    if not robokassa_payment_available():
        raise RobokassaPaymentUnavailableError("Robokassa payment is unavailable")

    plan = await get_tariff(session, tariff_code)
    if plan is None:
        raise ValueError("Unknown tariff")

    price_rub = plan.price_stars
    intent = ExternalPaymentIntent(
        provider="robokassa",
        telegram_id=telegram_id,
        tariff=plan.code,
        amount_rub=price_rub,
        status="pending",
    )
    session.add(intent)
    await session.flush()

    payment_url = make_robokassa_payment_url(intent, plan.title)
    intent.invoice_url = payment_url
    await session.flush()
    return payment_url


async def create_payment_intent(
    session: AsyncSession,
    telegram_id: int,
    tariff: str,
) -> PaymentIntent:
    plan = await get_tariff(session, tariff)
    if plan is None:
        raise ValueError("Unknown tariff")

    intent = PaymentIntent(
        payload=f"sub:{plan.code}:{telegram_id}:{uuid4()}",
        telegram_id=telegram_id,
        tariff=plan.code,
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
    plan: TariffPlan,
    *,
    omit_provider_token: bool = False,
    omit_subscription_fields: bool = False,
) -> str:
    invoice_payload = build_invoice_payload(
        plan,
        payment_intent.payload,
    )
    if omit_provider_token:
        invoice_payload.pop("provider_token", None)
    if omit_subscription_fields:
        invoice_payload = without_subscription_fields(invoice_payload)

    log_invoice_payload(
        "create_invoice_link",
        None,
        payment_intent,
        invoice_payload,
    )
    return await bot.create_invoice_link(**invoice_payload)


async def send_subscription_invoice(
    bot: Bot,
    chat_id: int,
    payment_intent: PaymentIntent,
    plan: TariffPlan,
) -> InvoiceDeliveryResult:
    ensure_send_invoice_subscription_period_support()

    invoice_payload = build_invoice_payload(
        plan,
        payment_intent.payload,
    )

    try:
        log_invoice_payload(
            "send_invoice",
            chat_id,
            payment_intent,
            invoice_payload,
        )
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
        if subscription_export_missing_error(send_exc):
            ordinary_payload = without_subscription_fields(invoice_payload)
            try:
                log_invoice_payload(
                    "send_invoice_without_subscription_fields",
                    chat_id,
                    payment_intent,
                    ordinary_payload,
                )
                await bot.send_invoice(chat_id=chat_id, **ordinary_payload)
                return InvoiceDeliveryResult(
                    method="send_invoice_without_subscription_fields"
                )
            except Exception as ordinary_exc:
                logger.exception(
                    "send_invoice without subscription fields failed: "
                    "tariff=%s amount=%s payload=%s error=%s",
                    payment_intent.tariff,
                    payment_intent.amount_stars,
                    payment_intent.payload,
                    str(ordinary_exc),
                )

        if provider_token_error(send_exc):
            without_provider_token = dict(invoice_payload)
            without_provider_token.pop("provider_token", None)
            try:
                log_invoice_payload(
                    "send_invoice_without_provider_token",
                    chat_id,
                    payment_intent,
                    without_provider_token,
                )
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
                plan,
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
            if subscription_export_missing_error(link_exc):
                try:
                    invoice_link = await create_subscription_invoice_link(
                        bot,
                        payment_intent,
                        plan,
                        omit_subscription_fields=True,
                    )
                    return InvoiceDeliveryResult(
                        method="create_invoice_link_without_subscription_fields",
                        invoice_link=invoice_link,
                    )
                except Exception as link_ordinary_exc:
                    logger.exception(
                        "create_invoice_link without subscription fields failed: "
                        "tariff=%s amount=%s payload=%s error=%s",
                        payment_intent.tariff,
                        payment_intent.amount_stars,
                        payment_intent.payload,
                        str(link_ordinary_exc),
                    )
            if provider_token_error(link_exc):
                try:
                    invoice_link = await create_subscription_invoice_link(
                        bot,
                        payment_intent,
                        plan,
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

    plan = await get_tariff(session, intent.tariff)
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
    from app.access import PACKAGE_TARIFF_CODES, activate_plan, grant_paid_recognitions

    intent = await find_intent_by_payload(
        session,
        successful_payment.invoice_payload,
    )
    if intent is None:
        raise ValueError("Payment intent not found")

    plan = await get_tariff(session, intent.tariff, active_only=False)
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
        if intent.tariff not in PACKAGE_TARIFF_CODES:
            activate_plan(
                user,
                intent.tariff,
                plan.monthly_limit,
                existing.subscription_expiration_date,
            )
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
    if intent.tariff in PACKAGE_TARIFF_CODES:
        grant_paid_recognitions(user, intent.tariff, plan.recognitions_count)
    else:
        activate_plan(
            user,
            intent.tariff,
            plan.monthly_limit,
            payment.subscription_expiration_date,
        )
    user.subscription_payment_charge_id = payment.telegram_payment_charge_id
    user.subscription_payload = payment.invoice_payload
    await session.flush()
    return payment, intent
