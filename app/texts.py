from collections.abc import Mapping
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BotText

logger = logging.getLogger(__name__)

PROCESSING_TEXT = "Принял изображение. Анализирую шрифт."
NOT_PHOTO_TEXT = "Отправь изображение с текстом — я попробую определить шрифт."
DOCUMENT_TEXT = """Сейчас я работаю с изображениями.

Отправь скриншот или картинку с текстом — так я смогу найти похожий шрифт."""
DOWNLOAD_ERROR_TEXT = "Не удалось скачать фото. Попробуйте ещё раз."
TEMP_OVERLOADED_TEXT = "Сервис временно перегружен. Попробуйте позже."
TEMP_UNAVAILABLE_TEXT = "Сервис временно недоступен. Попробуйте позже."
NO_ACTIVE_SUBSCRIPTION_TEXT = "У вас нет активной подписки."
UNREADABLE_TEXT = """Текст на изображении плохо читается.

Для точного поиска лучше отправить картинку, где буквы:
— крупные
— чёткие
— без размытия"""

PAYMENT_SUPPORT_TEXT = """По вопросам оплаты напишите в поддержку: {support}

Укажите:
1. Ваш Telegram ID
2. Тариф
3. Дату оплаты
4. Описание проблемы"""

DEFAULT_BOT_TEXTS: dict[str, tuple[str, str]] = {
    "main_menu": (
        "Главное меню",
        """Меню

Я помогу найти шрифт по фото.

Отправьте фото или скрин с текстом.
Лучше присылать крупный фрагмент одного слова.

Бесплатно доступно 1 распознавание.

Выберите действие:""",
    ),
    "find_font": (
        "Узнать шрифт",
        """Отправь изображение с текстом — я попробую определить шрифт.

Чем чётче изображение, тем точнее результат.""",
    ),
    "subscription_trial": (
        "Подписка: Trial",
        """Подписка

Статус: Бесплатный доступ
Распознаваний осталось: {remaining} / {limit}

После бесплатного распознавания нужна подписка.

Designer — {price_designer} ₽ / месяц, {limit_designer} распознаваний.
Studio — {price_studio} ₽ / месяц, {limit_studio} распознаваний.""",
    ),
    "subscription_no_access": (
        "Подписка: нет доступа",
        """Подписка

Статус: Нет активного доступа

Бесплатное распознавание уже использовано.

Designer — {price_designer} ₽ / месяц, {limit_designer} распознаваний.
Studio — {price_studio} ₽ / месяц, {limit_studio} распознаваний.""",
    ),
    "subscription_designer": (
        "Подписка: Designer",
        """Подписка

Статус: Designer ✅
Доступ до: {date}
Распознаваний осталось: {remaining} / {limit}""",
    ),
    "subscription_studio": (
        "Подписка: Studio",
        """Подписка

Статус: Studio ✅
Доступ до: {date}
Распознаваний осталось: {remaining} / {limit}""",
    ),
    "profile_trial": (
        "Профиль: Trial",
        """Профиль

Статус: Бесплатный доступ
Распознаваний осталось: {remaining} / {limit}""",
    ),
    "profile_no_access": (
        "Профиль: нет доступа",
        """Профиль

Статус: Нет активной подписки

Бесплатное распознавание уже использовано.

Оформите подписку, чтобы продолжить.""",
    ),
    "profile_designer": (
        "Профиль: Designer",
        """Профиль

Статус: Designer ✅
Подписка активна до: {date}
Осталось дней: {days_left}
Распознаваний осталось: {remaining} / {limit}""",
    ),
    "profile_studio": (
        "Профиль: Studio",
        """Профиль

Статус: Studio ✅
Подписка активна до: {date}
Осталось дней: {days_left}
Распознаваний осталось: {remaining} / {limit}""",
    ),
    "no_access": (
        "Нет доступа",
        """Бесплатное распознавание уже использовано.

Оформите подписку, чтобы продолжить.

Designer — {price_designer} ₽ / месяц, {limit_designer} распознаваний
Studio — {price_studio} ₽ / месяц, {limit_studio} распознаваний""",
    ),
    "font_result_found": (
        "Результат: найден",
        """Шрифт: {font_name}

Это наиболее похожий вариант. Бот может допускать неточное определение шрифта.""",
    ),
    "font_result_not_found": (
        "Результат: не найден",
        """Шрифт не найден.

Попробуйте отправить другой фрагмент текста или изображение с более характерными буквами.""",
    ),
    "payment_success_designer": (
        "Оплата: Designer",
        """Оплата прошла ✅

Тариф: Designer
Доступ активирован на 30 дней.
Распознаваний: {limit}

Теперь отправьте фото со шрифтом.""",
    ),
    "payment_success_studio": (
        "Оплата: Studio",
        """Оплата прошла ✅

Тариф: Studio
Доступ активирован на 30 дней.
Распознаваний: {limit}

Теперь отправьте фото со шрифтом.""",
    ),
    "support": (
        "Поддержка",
        "Поддержка: {support_username}",
    ),
    "terms": (
        "Условия",
        """1. Бот автоматически определяет шрифт по фото.
2. Результат может быть неточным.
3. После пробного доступа нужна подписка.
4. Оплата производится картой через Робокассу.""",
    ),
}

FORCED_TEXT_UPDATE_KEY = "bot_texts_payment_rub_v2"
ACCESS_TRIAL_TEXT_UPDATE_KEY = "access_trial_one_request_v1"
USAGE_RESULT_TEXT_UPDATE_KEY = "usage_result_types_v1"
FORCED_TEXT_UPDATE_KEYS = {
    "main_menu",
    "start_message",
    "find_font",
    "subscription_trial",
    "subscription_no_access",
    "profile_no_access",
    "no_access",
    "font_result_found",
    "font_result_not_found",
    "payment_success_designer",
    "payment_success_studio",
    "terms",
}
USAGE_RESULT_TEXT_UPDATE_KEYS = {
    "font_result_not_found",
}


class SafeFormatDict(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, values: Mapping[str, object]) -> str:
    try:
        return template.format_map(SafeFormatDict(values))
    except Exception:
        return template


def sanitize_user_text(text: str) -> str:
    return (
        text.replace("Telegram Stars", "Робокассу")
        .replace(" Stars", " ₽")
        .replace("Stars", "₽")
    )


def default_text_title(key: str) -> str:
    return DEFAULT_BOT_TEXTS.get(key, (key, ""))[0]


def default_text_value(key: str) -> str:
    return DEFAULT_BOT_TEXTS.get(key, (key, ""))[1]


async def get_bot_text_template(session: AsyncSession, key: str) -> str:
    result = await session.execute(select(BotText).where(BotText.key == key))
    bot_text = result.scalar_one_or_none()
    if bot_text is not None:
        logger.info(
            "Bot text source: key=%s source=db override_found=true default_used=false",
            key,
        )
        return bot_text.text
    logger.info(
        "Bot text source: key=%s source=default override_found=false default_used=true",
        key,
    )
    return default_text_value(key)


async def get_bot_text(session: AsyncSession, key: str, **kwargs: object) -> str:
    template = await get_bot_text_template(session, key)
    return sanitize_user_text(render_template(template, kwargs))


async def set_bot_text(session: AsyncSession, key: str, text: str) -> BotText:
    result = await session.execute(select(BotText).where(BotText.key == key))
    bot_text = result.scalar_one_or_none()
    if bot_text is None:
        bot_text = BotText(
            key=key,
            title=default_text_title(key),
            text=text,
        )
        session.add(bot_text)
        await session.flush()
        return bot_text

    bot_text.text = text
    return bot_text


async def list_bot_texts(session: AsyncSession) -> list[BotText]:
    result = await session.execute(select(BotText).order_by(BotText.id))
    texts_by_key = {bot_text.key: bot_text for bot_text in result.scalars().all()}
    texts = []
    for key, (title, text) in DEFAULT_BOT_TEXTS.items():
        texts.append(texts_by_key.get(key) or BotText(key=key, title=title, text=text))
    return texts


async def reset_bot_text(session: AsyncSession, key: str) -> BotText:
    return await set_bot_text(session, key, default_text_value(key))


async def font_result_text(session: AsyncSession, title: str | None) -> str:
    font_name = title.strip() if title and title.strip() else ""
    if font_name:
        return await get_bot_text(
            session,
            "font_result_found",
            font_name=font_name,
        )
    return await get_bot_text(session, "font_result_not_found")


async def support_text(session: AsyncSession) -> str:
    return await get_bot_text(
        session,
        "support",
        support_username=settings.support_contact,
    )


def paysupport_text() -> str:
    return PAYMENT_SUPPORT_TEXT.format(support=settings.support_contact)


async def terms_text(session: AsyncSession) -> str:
    return await get_bot_text(session, "terms")
