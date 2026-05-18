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


def subscription_menu_keyboard(
    user_has_active_paid_plan: bool,
) -> InlineKeyboardMarkup:
    if user_has_active_paid_plan:
        return back_to_menu_keyboard()

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить Designer",
                    callback_data="pay:designer",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оплатить Studio",
                    callback_data="pay:studio",
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
