from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
                    text="💳 Подписка",
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
                    text="💳 Подписка",
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
                    text="💳 Подписка",
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
) -> InlineKeyboardMarkup:
    if user_has_active_paid_plan:
        return back_to_menu_keyboard()

    rows = [
        [
            InlineKeyboardButton(
                text="Оплатить Designer картой",
                callback_data="pay_card:designer",
            )
        ],
        [
            InlineKeyboardButton(
                text="Оплатить Studio картой",
                callback_data="pay_card:studio",
            )
        ],
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="menu:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def no_access_subscription_keyboard() -> InlineKeyboardMarkup:
    return subscription_menu_keyboard(user_has_active_paid_plan=False)


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
