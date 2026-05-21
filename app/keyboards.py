import logging
from inspect import signature

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


logger = logging.getLogger(__name__)
INLINE_BUTTON_SUPPORTS_STYLE = "style" in signature(InlineKeyboardButton).parameters


def purchase_button(
    *,
    text: str,
    callback_data: str,
    style: str | None = None,
    fallback_text: str | None = None,
) -> InlineKeyboardButton:
    if style and INLINE_BUTTON_SUPPORTS_STYLE:
        try:
            return InlineKeyboardButton(
                text=text,
                callback_data=callback_data,
                style=style,
            )
        except TypeError:
            logger.warning("InlineKeyboardButton style is not supported at runtime")

    return InlineKeyboardButton(
        text=fallback_text or text,
        callback_data=callback_data,
    )


def ensure_no_legacy_payment_ui(keyboard: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    forbidden = "St" + "ars"
    if forbidden in str(keyboard):
        logger.error("Legacy payment label leaked into keyboard: %s", keyboard)
    assert forbidden not in str(keyboard)
    return keyboard


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Узнать шрифт",
                    callback_data="menu:find_font",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Доступ",
                    callback_data="menu:subscription",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="menu:profile",
                )
            ],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="menu:main",
                )
            ]
        ]
    )


def result_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Узнать другой шрифт",
                    callback_data="result:find_again",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Доступ",
                    callback_data="menu:subscription",
                )
            ],
        ]
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Доступ",
                    callback_data="menu:subscription",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="menu:main",
                )
            ],
        ]
    )


def subscription_menu_keyboard(
    user_has_active_paid_plan: bool,
    launch_offer_active: bool = False,
) -> InlineKeyboardMarkup:
    purchase_keyboard = (
        offer_purchase_keyboard()
        if launch_offer_active
        else regular_purchase_keyboard()
    )
    rows = list(purchase_keyboard.inline_keyboard)
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data="menu:main",
            )
        ]
    )
    return ensure_no_legacy_payment_ui(InlineKeyboardMarkup(inline_keyboard=rows))


def cancel_subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отменить продление",
                    callback_data="cancel_subscription_confirm",
                )
            ],
        ]
    )


def no_access_subscription_keyboard(
    launch_offer_active: bool = False,
) -> InlineKeyboardMarkup:
    return (
        offer_purchase_keyboard()
        if launch_offer_active
        else regular_purchase_keyboard()
    )


def offer_purchase_keyboard() -> InlineKeyboardMarkup:
    return ensure_no_legacy_payment_ui(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    purchase_button(
                        text="Купить за 99 ₽",
                        fallback_text="🟢 Купить за 99 ₽",
                        callback_data="pay_card:founder_offer",
                        style="primary",
                    )
                ]
            ]
        )
    )


def regular_purchase_keyboard() -> InlineKeyboardMarkup:
    return ensure_no_legacy_payment_ui(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Купить за 199 ₽",
                        callback_data="pay_card:founder_regular",
                    )
                ]
            ]
        )
    )


def invoice_link_keyboard(tariff_title: str, invoice_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить {tariff_title}",
                    url=invoice_link,
                )
            ]
        ]
    )


def card_payment_keyboard(invoice_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить картой",
                    url=invoice_url,
                )
            ]
        ]
    )


def offer_broadcast_payment_keyboard(payment_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Купить за 99 ₽",
                    url=payment_url,
                )
            ]
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Статистика",
                    callback_data="admin:stats",
                )
            ]
        ]
    )


def text_menu_keyboard(
    categories: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=title,
                callback_data=f"textcat:{code}",
            )
        ]
        for code, title in categories
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_category_keyboard(
    items: list[tuple[str, str]],
    category: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=title,
                callback_data=f"textedit:{key}",
            )
        ]
        for key, title in items
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="textmenu:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_edit_keyboard(key: str, category: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Изменить",
                    callback_data=f"text_change:{key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сбросить",
                    callback_data=f"text_reset:{key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"text_back:{category}",
                )
            ],
        ]
    )


def text_after_save_keyboard(key: str, category: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Вернуться к блоку",
                    callback_data=f"textedit:{key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="В меню текстов",
                    callback_data="textmenu:main",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"text_back:{category}",
                )
            ],
        ]
    )


def broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="All",
                    callback_data="broadcast:audience:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Free",
                    callback_data="broadcast:audience:free",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Paid",
                    callback_data="broadcast:audience:paid",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast:cancel",
                )
            ],
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data="broadcast:send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast:cancel",
                )
            ],
        ]
    )


def broadcast_offer_audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Всем",
                    callback_data="broadcast_offer:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Только free",
                    callback_data="broadcast_offer:free",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Только без баланса",
                    callback_data="broadcast_offer:no_balance",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_offer:cancel",
                )
            ],
        ]
    )


def broadcast_offer_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data="broadcast_offer:send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_offer:cancel",
                )
            ],
        ]
    )


def broadcast_builder_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать рассылку",
                    callback_data="broadcast_builder:create",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_builder:cancel",
                )
            ],
        ]
    )


def broadcast_builder_button_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без кнопки",
                    callback_data="broadcast_btn:none",
                )
            ],
            [
                InlineKeyboardButton(
                    text="URL-кнопка",
                    callback_data="broadcast_btn:url",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Robokassa-кнопка",
                    callback_data="broadcast_btn:robokassa",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Готово",
                    callback_data="broadcast_btn:done",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_btn:cancel",
                )
            ],
        ]
    )


def broadcast_builder_packages_keyboard(
    packages: list[tuple[str, str, int, int]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{title} — {price_rub} ₽ / {recognitions_count} распознаваний",
                callback_data=f"broadcast_pkg:{code}",
            )
        ]
        for code, title, price_rub, recognitions_count in packages
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data="broadcast_btn:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_builder_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить тест себе",
                    callback_data="broadcast_preview:test",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выбрать аудиторию",
                    callback_data="broadcast_preview:audience",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить заново",
                    callback_data="broadcast_preview:restart",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_preview:cancel",
                )
            ],
        ]
    )


def broadcast_builder_audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Всем",
                    callback_data="broadcast_audience:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Без баланса",
                    callback_data="broadcast_audience:no_balance",
                )
            ],
            [
                InlineKeyboardButton(
                    text="С балансом",
                    callback_data="broadcast_audience:has_balance",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Не покупали",
                    callback_data="broadcast_audience:never_paid",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_audience:cancel",
                )
            ],
        ]
    )


def broadcast_builder_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data="broadcast_confirm:send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data="broadcast_confirm:back",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="broadcast_confirm:cancel",
                )
            ],
        ]
    )
