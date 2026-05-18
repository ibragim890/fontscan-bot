# Font Bot

Telegram-бот на Python 3.11+ для определения шрифта по фото через WhatFontIs API. Запуск выполняется через long polling, без webhook.

## Возможности

- Пробный доступ не начинается после `/start`.
- Пробный доступ начинается при первой отправке фото на распознавание.
- Trial: 2 дня и 3 распознавания всего.
- `/start` показывает одно главное меню с кнопками `🔎 Узнать шрифт`, `💳 Подписка` и `👤 Профиль`.
- Главное меню работает через inline keyboard, без старого reply keyboard.
- Кнопка `🔎 Узнать шрифт` просит отправить фото.
- Кнопка `💳 Подписка` показывает статус, остаток распознаваний и оплату.
- Кнопка `👤 Профиль` показывает текущий доступ, тариф, дату окончания и остаток распознаваний.
- Подписки Telegram Stars:
  - Designer: 99 Stars / месяц, 20 распознаваний.
  - Studio: 199 Stars / месяц, 50 распознаваний.
- Цены, лимиты подписок и trial-настройки можно менять из Telegram без Railway redeploy.
- Оплата запускается сразу при нажатии `Оплатить Designer` или `Оплатить Studio`.
- После результата можно сразу нажать `🔎 Узнать другой шрифт` или открыть `💳 Подписка`.
- Одинаковые картинки не вызывают WhatFontIs API повторно и не списывают лимит.
- Фото пользователей не сохраняются на диск. В базе хранится только SHA-256 hash изображения и JSON-ответ сервиса.

Для цифровых услуг внутри Telegram используется Telegram Stars.

## Создание бота

1. Откройте Telegram и найдите `@BotFather`.
2. Выполните команду `/newbot`.
3. Задайте имя и username бота.
4. BotFather выдаст `BOT_TOKEN`. Сохраните его в `.env`.

## WhatFontIs API

1. Зарегистрируйтесь на WhatFontIs.
2. Получите API-ключ в личном кабинете или у поддержки WhatFontIs.
3. Укажите ключ в переменной `WHATFONTIS_API_KEYS`.

Для коммерческого использования WhatFontIs API нужно согласовать commercial use с WhatFontIs.

## Настройка

Скопируйте пример окружения:

```bash
cp .env.example .env
```

Заполните `.env`:

```env
BOT_TOKEN=123456:telegram-token
WHATFONTIS_API_KEYS=whatfontis-key
DATABASE_URL=sqlite+aiosqlite:///bot.db
ADMIN_IDS=123456789,987654321
ADMIN_SECRET_CODE=
ADMIN_SECRET_ENABLED=true

TRIAL_DAYS=2
TRIAL_REQUESTS_LIMIT=3

DESIGNER_PRICE_STARS=99
DESIGNER_MONTHLY_LIMIT=20

STUDIO_PRICE_STARS=199
STUDIO_MONTHLY_LIMIT=50

SUBSCRIPTION_PERIOD=2592000

DAILY_API_SAFETY_LIMIT=90

SUPPORT_USERNAME=@your_support
TERMS_URL=https://example.com/terms
```

`ADMIN_IDS` можно оставить пустым или указать Telegram ID администраторов через запятую.
`ADMIN_SECRET_CODE` задаётся только через переменные окружения и не должен храниться в коде.

Значения `TRIAL_*`, `DESIGNER_*` и `STUDIO_*` используются как стартовые дефолты при первом создании записей в базе. После этого актуальные цены и лимиты берутся из таблиц базы данных и могут меняться Telegram-командами.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Запуск

```bash
python -m app.main
```

При первом запуске приложение создаст таблицы SQLite автоматически.

## Railway Deploy

1. Создайте GitHub repo.
2. Залейте проект в репозиторий.
3. В Railway выберите `New Project` → `Deploy from GitHub Repo`.
4. В Railway → `Variables` добавьте:

```env
BOT_TOKEN=
WHATFONTIS_API_KEYS=
DATABASE_URL=
ADMIN_IDS=
ADMIN_SECRET_CODE=
ADMIN_SECRET_ENABLED=true
SUPPORT_USERNAME=
DAILY_API_SAFETY_LIMIT=90
TRIAL_DAYS=2
TRIAL_REQUESTS_LIMIT=3
DESIGNER_PRICE_STARS=99
DESIGNER_MONTHLY_LIMIT=20
STUDIO_PRICE_STARS=199
STUDIO_MONTHLY_LIMIT=50
```

Railway запускает long polling worker через `Procfile`:

```text
worker: python -m app.main
```

Если используется SQLite на Railway без volume, база может потеряться при redeploy. Для теста можно SQLite, для запуска лучше PostgreSQL.

## Telegram Stars Payments

Цифровые услуги внутри Telegram оплачиваются через Telegram Stars:

- Валюта invoice: `XTR`.
- `provider_token` пустой: `""`.
- Для месячной подписки используется `subscription_period=2592000`.
- В invoice передаётся один `LabeledPrice`.
- Payload уникален и имеет формат `sub:<tariff>:<telegram_id>:<uuid>`.
- После оплаты Telegram присылает `successful_payment`.
- `telegram_payment_charge_id` сохраняется в базе и используется для отмены продления Stars-подписки.

Тарифы:

- Trial: 2 дня и 3 распознавания, бесплатно. Начинается только после первой отправки фото.
- Designer: 99 Stars / месяц, 20 распознаваний.
- Studio: 199 Stars / месяц, 50 распознаваний.

## Dynamic Tariffs

Тарифы хранятся в базе данных. Invoice, экран подписки и `/tariffs` используют актуальные значения из базы, поэтому менять цены и лимиты можно прямо с телефона без Railway redeploy.

Команды доступны администраторам из `ADMIN_IDS` и пользователям с секретным доступом через `/admin_login <code>`:

```text
/set_price designer 149
/set_limit designer 30
/set_trial_days 2
/set_trial_limit 3
/tariffs
```

Ограничения:

- Цена: целое число от 1 до 10000 Stars.
- Лимит распознаваний: целое число от 1 до 100000.
- Если тариф не найден, бот отвечает `Тариф не найден.`

## Редактирование Текстов

Тексты основных экранов хранятся в базе данных и редактируются прямо из Telegram без redeploy. Доступно только администраторам из `ADMIN_IDS` или пользователям после `/admin_login <code>`.

Открыть меню редактора:

```text
/text_menu
```

Быстрые команды:

```text
/texts
/list_texts
/get_text main_menu
/set_text main_menu
/reset_text main_menu
/cancel_text
```

Редактируются тексты:

- главное меню;
- узнать шрифт;
- подписка;
- профиль;
- no access;
- результат найден;
- результат не найден;
- после оплаты;
- support;
- terms.

В текстах можно использовать переменные:

```text
{trial_days}
{trial_limit}
{status}
{days_left}
{hours_left}
{date}
{remaining}
{limit}
{tariff}
{price_designer}
{limit_designer}
{price_studio}
{limit_studio}
{font_name}
{support_username}
```

Если переменная не передана или написана с ошибкой, бот не падает и оставляет плейсхолдер как есть.

## Кэш Изображений

Бот считает `sha256` от скачанного изображения и сохраняет его как `image_hash`.

Если пользователь повторно отправляет ту же картинку:

- WhatFontIs API не вызывается.
- Лимит trial или подписки не списывается.
- Повторные картинки не тратят лимит распознаваний.
- Пользователь получает тот же результат.
- В базе сохраняется новая запись `FontRequest` с `provider="cache"` и `is_cached_response=True`.

## WhatFontIs API Usage

Бот ведёт дневной учёт запросов к WhatFontIs по каждому API key.

- `WHATFONTIS_API_KEYS` может содержать один ключ или несколько ключей через запятую.
- Сами API keys не показываются в интерфейсе.
- Каждый реальный запрос к WhatFontIs увеличивает счётчик текущего ключа за сегодня.
- Если WhatFontIs возвращает `429`, ключ помечается как `rate limited`.
- `/admin_stats` показывает блок `WhatFontIs usage today` с количеством запросов по ключам.
- `DAILY_API_SAFETY_LIMIT` ограничивает суммарное количество реальных WhatFontIs API calls за день по всем ключам.
- Если `DAILY_API_SAFETY_LIMIT=0` или пустой, safety limit отключён.
- Кэш проверяется до safety limit, поэтому повторные картинки продолжают отвечать без API и без списания лимита.

## Secret Admin Analytics

Секретная аналитика доступна администраторам из `ADMIN_IDS` и пользователям, которые ввели правильный `ADMIN_SECRET_CODE`.

1. Установите `ADMIN_SECRET_CODE` в Railway Variables.
2. Убедитесь, что `ADMIN_SECRET_ENABLED=true`.
3. Перезапустите Railway service.
4. В Telegram выполните:

```text
/admin_login <code>
```

5. Смотреть аналитику:

```text
/secret_stats
```

6. Закрыть доступ:

```text
/admin_logout
```

`/secret_stats` показывает только агрегаты: пользователей, trial, подписки, распознавания, cache hits, оплаты, WhatFontIs usage и safety limit. Секретный код, `BOT_TOKEN`, API keys и персональные данные пользователей не выводятся.

## Если счёт не создаётся

Проверьте:

1. `BOT_TOKEN` правильный.
2. Бот запущен в Telegram.
3. `aiogram` обновлён: `pip install -U aiogram`.
4. `provider_token=""`.
5. `currency="XTR"`.
6. `prices=[один LabeledPrice]`.
7. `subscription_period=2592000`.
8. Реальную ошибку в консоли после `logger.exception`.

Для диагностики администратор из `ADMIN_IDS` может вызвать `/debug_payments`.

## Проверка

1. Отправьте `/start`: появится главное меню с кнопками `🔎 Узнать шрифт`, `💳 Подписка`, `👤 Профиль`; trial ещё не должен начаться.
2. Нажмите `🔎 Узнать шрифт`: бот попросит отправить фото.
3. Отправьте фото или скрин с крупным фрагментом текста.
4. Отправьте то же фото повторно: API не должен вызываться, лимит не должен списываться.
5. Проверьте `/status`: должен появиться статус Trial.
6. Используйте 3 уникальных распознавания и отправьте ещё одно фото: бот должен предложить подписку без вызова WhatFontIs.
7. Отправьте `/subscribe` или нажмите `💳 Подписка`: бот покажет статус и тарифы.
8. Нажмите `Оплатить Designer` или `Оплатить Studio`: invoice создаётся сразу.
9. Проверьте `/status`: должен отобразиться тариф, дата окончания и остаток лимита.
10. Проверьте `/cancel`: продление отменяется, доступ остаётся до даты окончания.
11. Проверьте `/paysupport`, `/support`, `/terms`.

## Команды

- `/start` — приветствие.
- `/status` — профиль пользователя: текущий доступ, тариф, дата окончания и остаток распознаваний.
- `/subscribe` — выбор подписки.
- `/cancel` — отмена продления активной Stars-подписки.
- `/paysupport` — поддержка по оплате.
- `/support` — общая поддержка.
- `/terms` — условия.
- `/admin_stats` — статистика для администраторов из `ADMIN_IDS`.
- `/admin_login <code>` — открыть секретный доступ к агрегированной аналитике.
- `/secret_stats` — агрегированная аналитика FontScan для `ADMIN_IDS` или пользователей с секретным доступом.
- `/admin_logout` — закрыть секретный доступ к аналитике.
- `/set_price <tariff> <price>` — изменить цену тарифа без redeploy.
- `/set_limit <tariff> <limit>` — изменить месячный лимит тарифа без redeploy.
- `/set_trial_days <days>` — изменить длительность trial без redeploy.
- `/set_trial_limit <limit>` — изменить лимит trial без redeploy.
- `/tariffs` — показать актуальные цены и лимиты тарифов.
- `/backup_db` — отправить SQLite backup базы данных администратору.
- `/text_menu` или `/texts` — открыть меню редактора текстов.
- `/list_texts` — показать список ключей текстов.
- `/get_text <key>` — посмотреть текущий текст.
- `/set_text <key>` — перейти в режим изменения текста.
- `/reset_text <key>` — сбросить текст к стандартному.
- `/cancel_text` — отменить редактирование текста.
- `/debug_payments` — диагностика платежей для администраторов из `ADMIN_IDS`.
- `/reset_limits` — сбросить `trial_requests_used` и `monthly_requests_used` всем пользователям, только для `ADMIN_IDS`.
- `/reset_trials` — сбросить trial всем пользователям без удаления paid-подписок, только для `ADMIN_IDS`.

## Данные

SQLite используется как MVP-хранилище. Бот не сохраняет исходные изображения пользователей. Для истории распознаваний сохраняются:

- Telegram ID пользователя.
- SHA-256 hash изображения.
- Найденный шрифт.
- JSON-ответ WhatFontIs.
- Статус и флаг списания запроса.
