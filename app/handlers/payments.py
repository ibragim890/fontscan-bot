import logging

import aiogram
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    format_date,
    get_or_create_user,
    get_subscription_text,
    user_has_current_paid_subscription,
)
from app.keyboards import (
    card_payment_keyboard,
    cancel_subscription_keyboard,
    invoice_link_keyboard,
    main_menu_keyboard,
    subscription_menu_keyboard,
)
from app.payments import (
    RobokassaPaymentUnavailableError,
    create_payment_intent,
    create_robokassa_payment,
    get_tariff,
    list_tariffs,
    save_successful_payment,
    send_subscription_invoice,
    validate_pre_checkout,
)
from app.config import settings
from app.models import Payment, PaymentIntent
from app.texts import NO_ACTIVE_SUBSCRIPTION_TEXT, get_bot_text

logger = logging.getLogger(__name__)
router = Router(name="payments")


@router.message(Command("subscribe"))
async def subscribe_handler(message: Message, session: AsyncSession) -> None:
    user = await get_or_create_user(session, message.from_user)
    await message.answer(
        await get_subscription_text(session, user),
        reply_markup=subscription_menu_keyboard(
            user_has_current_paid_subscription(user)
        ),
    )


@router.callback_query(F.data.startswith("pay:"))
async def subscribe_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    if user_has_current_paid_subscription(user):
        await callback.answer(
            "У вас уже есть активная подписка.",
            show_alert=True,
        )
        return

    tariff = callback.data.split(":", maxsplit=1)[1]
    plan = await get_tariff(session, tariff)
    if plan is None:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    intent = await create_payment_intent(session, callback.from_user.id, plan.code)
    await session.commit()

    try:
        delivery = await send_subscription_invoice(
            callback.bot,
            callback.from_user.id,
            intent,
            plan,
        )
        if delivery.invoice_link:
            await callback.message.answer(
                "Нажмите кнопку ниже, чтобы оплатить подписку.",
                reply_markup=invoice_link_keyboard(plan.title, delivery.invoice_link),
            )
        await callback.answer()
    except Exception as exc:
        intent.status = "failed"
        await session.commit()
        logger.exception(
            "Failed to create Stars invoice: tariff=%s amount=%s payload=%s error=%s",
            plan.code,
            plan.price_stars,
            intent.payload,
            str(exc),
        )
        await callback.message.answer("Не удалось создать счёт. Попробуйте позже.")
        await callback.answer()


@router.callback_query(F.data.startswith("pay_card:"))
async def card_payment_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    user = await get_or_create_user(session, callback.from_user)
    if user_has_current_paid_subscription(user):
        await callback.answer(
            "У вас уже есть активная подписка.",
            show_alert=True,
        )
        return

    tariff = callback.data.split(":", maxsplit=1)[1]
    plan = await get_tariff(session, tariff)
    if plan is None:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    try:
        invoice_url = await create_robokassa_payment(
            session,
            callback.from_user.id,
            plan.code,
        )
        await session.commit()
    except RobokassaPaymentUnavailableError:
        await callback.answer(
            "Оплата картой временно недоступна.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "Оплата картой\n\n"
        f"Тариф: {plan.title}\n"
        "После оплаты подписка активируется автоматически.",
        reply_markup=card_payment_keyboard(invoice_url),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(
    pre_checkout_query: PreCheckoutQuery,
    session: AsyncSession,
) -> None:
    ok, error_message, _intent = await validate_pre_checkout(
        session,
        pre_checkout_query,
    )
    if ok:
        await pre_checkout_query.bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=True,
        )
        return

    logger.warning(
        "Rejected pre_checkout_query from user=%s payload=%s reason=%s",
        pre_checkout_query.from_user.id,
        pre_checkout_query.invoice_payload,
        error_message,
    )
    await pre_checkout_query.bot.answer_pre_checkout_query(
        pre_checkout_query.id,
        ok=False,
        error_message="Ошибка оплаты. Попробуйте ещё раз.",
    )


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, session: AsyncSession) -> None:
    try:
        user = await get_or_create_user(session, message.from_user)
        payment, intent = await save_successful_payment(
            session,
            user,
            message.successful_payment,
        )
        plan = await get_tariff(session, intent.tariff, active_only=False)
        if plan is None:
            raise ValueError("Unknown tariff")
        logger.info(
            "Successful payment user=%s tariff=%s amount=%s charge_id=%s",
            user.telegram_id,
            intent.tariff,
            payment.amount_stars,
            payment.telegram_payment_charge_id,
        )
        await message.answer(
            await get_bot_text(
                session,
                f"payment_success_{plan.code}",
                tariff=plan.title,
                limit=plan.monthly_limit,
                date=format_date(user.plan_ends_at),
            ),
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.exception("Failed to process successful payment: %s", exc.__class__.__name__)
        await message.answer(
            "Платёж получен, но не удалось автоматически активировать доступ. "
            "Напишите в /paysupport."
        )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, session: AsyncSession) -> None:
    user = await get_or_create_user(session, message.from_user)
    if not user_has_current_paid_subscription(user):
        await message.answer(NO_ACTIVE_SUBSCRIPTION_TEXT)
        return

    if user.subscription_canceled:
        await message.answer(
            "Продление подписки уже отменено.\n"
            f"Доступ сохранится до: {format_date(user.plan_ends_at)}."
        )
        return

    await message.answer(
        "Вы можете отменить продление подписки.\n"
        f"Доступ до: {format_date(user.plan_ends_at)}.",
        reply_markup=cancel_subscription_keyboard(),
    )


@router.callback_query(F.data == "cancel_subscription_confirm")
async def cancel_subscription_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    user = await get_or_create_user(session, callback.from_user)

    if not user_has_current_paid_subscription(user):
        await callback.message.edit_text(NO_ACTIVE_SUBSCRIPTION_TEXT)
        await callback.answer()
        return

    if not user.subscription_payment_charge_id:
        logger.error("Cannot cancel subscription without Telegram charge id")
        await callback.answer("Не удалось отменить продление.", show_alert=True)
        return

    try:
        await callback.bot.edit_user_star_subscription(
            user_id=user.telegram_id,
            telegram_payment_charge_id=user.subscription_payment_charge_id,
            is_canceled=True,
        )
    except Exception as exc:
        logger.exception("Failed to cancel Stars subscription: %s", exc.__class__.__name__)
        await callback.answer("Не удалось отменить продление.", show_alert=True)
        return

    user.subscription_canceled = True
    await callback.message.edit_text(
        "Продление подписки отменено.\n"
        f"Доступ сохранится до: {format_date(user.plan_ends_at)}."
    )
    await callback.answer()


@router.message(Command("debug_payments"))
async def debug_payments_handler(message: Message, session: AsyncSession) -> None:
    if message.from_user.id not in settings.admin_id_set:
        await message.answer("Недоступно.")
        return

    intents_result = await session.execute(
        select(PaymentIntent).order_by(desc(PaymentIntent.created_at)).limit(5)
    )
    payments_result = await session.execute(
        select(Payment).order_by(desc(Payment.created_at)).limit(5)
    )
    intents = intents_result.scalars().all()
    payments = payments_result.scalars().all()
    tariffs = await list_tariffs(session)

    intent_lines = [
        (
            f"payload={intent.payload}\n"
            f"telegram_id={intent.telegram_id}\n"
            f"tariff={intent.tariff}\n"
            f"amount_stars={intent.amount_stars}\n"
            f"status={intent.status}\n"
            f"created_at={intent.created_at}"
        )
        for intent in intents
    ]
    payment_lines = [
        (
            f"telegram_id={payment.telegram_id}\n"
            f"tariff={payment.tariff}\n"
            f"amount_stars={payment.amount_stars}\n"
            f"currency={payment.currency}\n"
            f"created_at={payment.created_at}"
        )
        for payment in payments
    ]

    await message.answer(
        f"aiogram version: {aiogram.__version__}\n\n"
        "tariffs:\n"
        + "\n".join(
            f"{tariff.code}: {tariff.price_stars} Stars, "
            f"{tariff.monthly_limit} распознаваний"
            for tariff in tariffs
        )
        + "\n\n"
        "Последние PaymentIntent:\n"
        f"{chr(10).join(intent_lines) if intent_lines else 'нет'}\n\n"
        "Последние Payment:\n"
        f"{chr(10).join(payment_lines) if payment_lines else 'нет'}"
    )
