# Font Bot

Telegram-бот на Python 3.11+ для определения шрифта по фото через WhatFontIs API. Запуск выполняется через long polling, без webhook.

## Возможности

- Trial больше не зависит от дней: новому пользователю доступно 1 бесплатное распознавание.
- `/start` показывает одно главное меню с кнопками `🔎 Узнать шрифт`, `💳 Доступ` и `👤 Профиль`.
- Главное меню работает через inline keyboard, без старого reply keyboard.
- Кнопка `🔎 Узнать шрифт` просит отправить фото.
- Кнопка `💳 Доступ` показывает бесплатный остаток, платный баланс распознаваний и оплату.
- Кнопка `👤 Профиль` показывает текущий доступ, тариф, дату окончания и остаток распознаваний.
- Пакеты распознаваний оплачиваются картой через Робокассу:
  - Founder offer: 99 ₽ за 50 распознаваний, доступен 24 часа после первого бесплатного найденного шрифта.
  - Founder regular: 199 ₽ за 50 распознаваний после окончания оффера.
- Цены, лимиты подписок и trial-настройки можно менять из Telegram без Railway redeploy.
- Оплата запускается при нажатии `Купить за 99 ₽` или `Купить за 199 ₽`.
- После результата можно сразу нажать `🔎 Узнать другой шрифт` или открыть `💳 Доступ`.
- Deep links `/start threads`, `/start tiktok`, `/start telegram`, `/start instagram` и `/start ref_xxx` сохраняют источник пользователя для маркетинговой аналитики.
- Админ-команды дают воронку, источники, API usage, top fonts, inactive users, gift subscriptions, CSV export и рассылки по аудиториям.
- Одинаковые картинки не вызывают WhatFontIs API повторно. Cache hit проверяет доступ до кэша и списывает распознавание только если найден полезный шрифт.
- Если текст на изображении не читается, текст не найден, provider timeout/rate limit/internal error или ответ невалидный, попытка не списывается.
- Фото пользователей не сохраняются на диск. В базе хранится только SHA-256 hash изображения и JSON-ответ сервиса.

Код Telegram Stars оставлен как legacy/fallback, но скрыт из обычного пользовательского интерфейса.

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

TRIAL_REQUESTS_LIMIT=1

SUBSCRIPTION_PERIOD=2592000
SUBSCRIPTION_PRODUCT_ID=

DAILY_API_SAFETY_LIMIT=90

SUPPORT_USERNAME=@your_support
TERMS_URL=https://example.com/terms

ROBOKASSA_ENABLED=false
ROBOKASSA_MERCHANT_LOGIN=
ROBOKASSA_PASSWORD1=
ROBOKASSA_PASSWORD2=
ROBOKASSA_TEST_MODE=true
ROBOKASSA_BASE_URL=https://auth.robokassa.ru/Merchant/Index.aspx
PUBLIC_BASE_URL=
```

`ADMIN_IDS` можно оставить пустым или указать Telegram ID администраторов через запятую.
`ADMIN_SECRET_CODE` задаётся только через переменные окружения и не должен храниться в коде.

`TRIAL_REQUESTS_LIMIT` используется как стартовый дефолт `trial_limit=1` при первом создании записей в базе. Trial-дни больше не участвуют в проверке доступа. После этого актуальные цены и лимиты берутся из таблиц базы данных и могут меняться Telegram-командами.

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
TRIAL_REQUESTS_LIMIT=1
SUBSCRIPTION_PERIOD=2592000
SUBSCRIPTION_PRODUCT_ID=
ROBOKASSA_ENABLED=true
ROBOKASSA_MERCHANT_LOGIN=
ROBOKASSA_PASSWORD1=
ROBOKASSA_PASSWORD2=
ROBOKASSA_TEST_MODE=true
ROBOKASSA_BASE_URL=https://auth.robokassa.ru/Merchant/Index.aspx
PUBLIC_BASE_URL=https://your-railway-url
```

Railway запускает один процесс через `Procfile`. Внутри процесса одновременно работают long polling и FastAPI web server на `PORT` из Railway:

```text
worker: python -m app.main
```

В кабинете Робокассы укажите:

```text
Result URL: https://your-railway-url/robokassa/result
Success URL: https://your-railway-url/robokassa/success
Fail URL: https://your-railway-url/robokassa/fail
Метод Result URL: POST или GET
```

`PUBLIC_BASE_URL` — публичный URL Railway-сервиса, например `https://fontscan-bot-production.up.railway.app`.
`ROBOKASSA_TEST_MODE` можно поставить `true` для тестов или `false` для боевых платежей.

Если используется SQLite на Railway без volume, база может потеряться при redeploy. Для теста можно SQLite, для запуска лучше PostgreSQL.

## Robokassa Card Payments

Основной пользовательский способ оплаты — карта через Робокассу.

- Если `ROBOKASSA_ENABLED=false`, кнопки оплаты картой не показываются.
- Если `ROBOKASSA_ENABLED=true`, в меню доступа появляются кнопки покупки пакета.
- Если Робокасса включена, но не заданы `ROBOKASSA_MERCHANT_LOGIN`, `ROBOKASSA_PASSWORD1`, `ROBOKASSA_PASSWORD2` или `PUBLIC_BASE_URL`, бот отвечает: `Оплата картой временно недоступна.`
- Пока рублёвая цена хранится в старом техническом поле: `price_rub = price_stars`.
- Для создания ссылки используется подпись `md5(MerchantLogin:OutSum:InvId:Password1)`.
- Result URL проверяет подпись `md5(OutSum:InvId:Password2)` и использует `OutSum` ровно в том виде, в котором он пришёл от Робокассы.
- При успешном Result URL бот начисляет 50 распознаваний, закрывает активный оффер после покупки и возвращает Робокассе `OK<InvId>`.

## Launch Offer

После первого успешного бесплатного распознавания запускается 24-часовой launch-offer:

- 50 распознаваний за 99 ₽ вместо 199 ₽.
- Оффер стартует только если найден полезный шрифт.
- Если текст плохо читается, текст не найден, сервис недоступен, произошёл timeout/rate limit или нет полезного названия шрифта, бесплатное распознавание не списывается и оффер не запускается.
- Напоминания отправляются через 6, 12, 18 часов и за 1 час до конца.
- Каждое напоминание содержит кнопку покупки.
- После покупки или истечения оффера напоминания прекращаются.
- Если оффер закончился, кнопка `pay_card:founder_offer` не создаёт платёж и предлагает regular-пакет за 199 ₽.

## Telegram Stars Legacy

Код Telegram Stars сохранён для совместимости, но скрыт из обычного пользовательского интерфейса:

- Stars-модели, `PaymentIntent`, `Payment`, `pre_checkout_query`, `successful_payment` и `send_subscription_invoice` остаются в коде.
- Кнопки Stars не показываются в меню подписки.
- Обычный пользовательский сценарий не создаёт Stars invoice.

Тарифы:

- Trial: 1 бесплатное распознавание без ограничения по дням.
- Founder offer: 99 ₽, 50 распознаваний, только при активном 24-часовом оффере.
- Founder regular: 199 ₽, 50 распознаваний.

## Dynamic Tariffs

Пакеты хранятся в базе данных. Ссылка Робокассы, экран доступа, `/tariffs` и `/packages` используют актуальные значения из базы, поэтому менять цены и лимиты можно прямо с телефона без Railway redeploy.

Команды доступны администраторам из `ADMIN_IDS` и пользователям с секретным доступом через `/admin_login <code>`:

```text
/set_price founder_regular 249
/set_limit founder_regular 50
/set_trial_limit 1
/tariffs
/packages
```

Ограничения:

- Цена: целое число от 1 до 10000 ₽.
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

- Проверка доступа всегда выполняется до cache.
- Если доступа нет, бот не скачивает фото, не считает hash, не проверяет cache и не вызывает WhatFontIs API.
- WhatFontIs API не вызывается при cache hit.
- Cache hit списывает 1 распознавание только если cached result был полезным и содержит найденный шрифт.
- Cache hit с `unreadable_text`, `no_text_detected`, `invalid_image` или ошибкой provider не списывает usage.
- Пользователь получает тот же результат.
- В базе сохраняется новая запись `FontRequest` с `provider="cache"` и `is_cached_response=True`.

## Result Types и Списание

Font provider возвращает `success`, `counted_as_usage` и `result_type`.

- `font_found`: `success=true`, `counted_as_usage=true`.
- `no_font_match`: API нормально обработал изображение, но полезное название шрифта не найдено; `success=false`, `counted_as_usage=false`.
- `unreadable_text` / `no_text_detected` / `invalid_image`: текст плохо читается, не найден или изображение невалидно; `success=false`, `counted_as_usage=false`.
- `timeout`, `rate_limited`, `provider_error`, `internal_api_error`, `invalid_response`: `success=false`, `counted_as_usage=false`.

`increment_usage` вызывается только при `counted_as_usage=true`. После первого бесплатного `font_found` бот запускает launch-offer на 24 часа.

## WhatFontIs API Usage

Бот ведёт дневной учёт запросов к WhatFontIs по каждому API key.

- `WHATFONTIS_API_KEYS` может содержать один ключ или несколько ключей через запятую.
- Сами API keys не показываются в интерфейсе.
- Каждый реальный запрос к WhatFontIs увеличивает счётчик текущего ключа за сегодня.
- Если WhatFontIs возвращает `429`, ключ помечается как `rate limited`.
- `/admin_stats` показывает блок `WhatFontIs usage today` с количеством запросов по ключам.
- `DAILY_API_SAFETY_LIMIT` ограничивает суммарное количество реальных WhatFontIs API calls за день по всем ключам.
- Если `DAILY_API_SAFETY_LIMIT=0` или пустой, safety limit отключён.
- Кэш проверяется до safety limit, но только после проверки активного доступа.

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

## Growth / Marketing

Бот сохраняет первый source из deep link и не перезаписывает его повторными `/start`. Для referral-ссылок payload вида `ref_xxx` дополнительно сохраняет `referred_by=xxx`.

Примеры deep links:

```text
/start threads
/start tiktok
/start telegram
/start instagram
/start ref_123456789
```

Админ-команды доступны `ADMIN_IDS` и пользователям с секретным доступом через `/admin_login <code>`:

```text
/top_sources
/funnels
/user_stats
/api_usage
/top_fonts
/inactive_users
/health_full
/gift_sub 123456789 designer 30
/export_users
```

`/broadcast` запускает FSM-рассылку текстом: бот просит текст, показывает preview, предлагает аудиторию `all`, `free` или `paid`, затем отправляет только после подтверждения. Между отправками есть небольшая задержка; ошибки Telegram API по отдельным пользователям логируются и не останавливают рассылку.

`/broadcast_photo` работает так же, но принимает фото и optional caption.

`/export_users` отправляет CSV с колонками:

```text
telegram_id,username,source,created_at,plan,plan_ends_at,trial_requests_used,monthly_requests_used
```

Экспорт не содержит `BOT_TOKEN`, WhatFontIs keys, Robokassa passwords, admin secret или другие секреты.

## Если ссылка оплаты не создаётся

Проверьте:

1. `BOT_TOKEN` правильный.
2. Бот запущен в Telegram.
3. `ROBOKASSA_ENABLED=true`.
4. Заданы `ROBOKASSA_MERCHANT_LOGIN`, `ROBOKASSA_PASSWORD1`, `ROBOKASSA_PASSWORD2`.
5. Задан `PUBLIC_BASE_URL` с публичным URL Railway-сервиса.
6. В кабинете Робокассы настроены Result, Success и Fail URL.
7. Реальную ошибку в консоли после `logger.exception`.

Для диагностики администратор из `ADMIN_IDS` может вызвать `/debug_payments`.

## Если Robokassa не активирует подписку

1. Проверьте Railway logs: Result URL теперь пишет method, query, body, `OutSum`, `InvId`, полученную и рассчитанную подпись.
2. Откройте `https://your-railway-url/health`: должен быть ответ `OK`.
3. Откройте `https://your-railway-url/debug/robokassa`: проверьте, что включена Робокасса, задан `PUBLIC_BASE_URL`, есть merchant login, password1 и password2.
4. В кабинете Робокассы проверьте Result URL: `https://your-railway-url/robokassa/result`.
5. Проверьте метод Result URL: поддерживаются `POST` и `GET`.
6. Если в логах `bad signature`, проверьте `ROBOKASSA_PASSWORD2`: для Result URL используется именно password2.
7. Команда администратора `/debug_robokassa` показывает те же настройки без паролей.

## Проверка

1. Отправьте `/start`: появится главное меню с кнопками `🔎 Узнать шрифт`, `💳 Доступ`, `👤 Профиль`; trial ещё не должен начаться.
2. Нажмите `🔎 Узнать шрифт`: бот попросит отправить фото.
3. Отправьте изображение или скрин с крупным фрагментом текста.
4. После найденного шрифта бот должен списать бесплатное распознавание и отправить отдельный offer-message `50 распознаваний за 99 ₽`.
5. Проверьте `/status`: должен быть экран доступа с остатком бесплатных распознаваний `0 / 1`, платным балансом и активным оффером.
6. Отправьте второе фото без оплаты: бот должен показать paywall без скачивания фото, проверки cache и вызова WhatFontIs.
7. Если первое фото было cache hit с найденным шрифтом, trial всё равно должен списаться.
8. Отправьте нечитаемое изображение: бот должен ответить `Текст на изображении плохо читается.` и не списать trial/paid usage.
9. Отправьте `/subscribe` или нажмите `💳 Доступ`: бот покажет статус и пакет.
10. Нажмите `Купить за 99 ₽` при активном оффере или `Купить за 199 ₽` после его окончания: бот должен отправить URL-кнопку Робокассы.
11. Пользователь должен видеть кнопку `Оплатить картой`.
12. После оплаты `/status` должен показывать платный баланс +50 распознаваний.
13. Проверьте `/paysupport`, `/support`, `/terms`.

## Команды

- `/start` — приветствие.
- `/status` — текущий доступ: бесплатный остаток, платный баланс и активный пакет.
- `/subscribe` — экран доступа и покупка пакета.
- `/cancel` — отмена продления активной legacy Stars-подписки, если такая подписка была создана раньше.
- `/paysupport` — поддержка по оплате.
- `/support` — общая поддержка.
- `/terms` — условия.
- `/admin_stats` — статистика для администраторов из `ADMIN_IDS`.
- `/admin_login <code>` — открыть секретный доступ к агрегированной аналитике.
- `/secret_stats` — агрегированная аналитика FontScan для `ADMIN_IDS` или пользователей с секретным доступом.
- `/admin_logout` — закрыть секретный доступ к аналитике.
- `/top_sources` — топ источников из `/start <payload>`.
- `/funnels` — воронка Start → Photo → Paywall → Payment → Paid.
- `/user_stats` — пользователи всего, сегодня, 7d, 30d, paid, trial available/exhausted.
- `/api_usage` — дневной usage WhatFontIs по ключам и safety limit без вывода самих ключей.
- `/broadcast` — текстовая рассылка по `all`, `free` или `paid` после preview и confirm.
- `/broadcast_photo` — рассылка фото с caption по `all`, `free` или `paid`.
- `/gift_sub <telegram_id> <tariff> <days>` — выдать или продлить подписку пользователю.
- `/export_users` — CSV export пользователей без секретных данных.
- `/top_fonts` — топ найденных шрифтов по `FontRequest.top_font`.
- `/inactive_users` — пользователи без активности 7+ дней.
- `/health_full` — расширенный health: DB, uptime, API keys, safety limit, polling, Robokassa, cache.
- `/set_price <tariff> <price>` — изменить цену тарифа без redeploy.
- `/set_limit <tariff> <limit>` — изменить месячный лимит тарифа без redeploy.
- `/set_trial_days <days>` — legacy-команда; дни trial больше не используются.
- `/set_trial_limit <limit>` — изменить лимит trial без redeploy.
- `/tariffs` — показать актуальные цены и лимиты пакетов/legacy-тарифов.
- `/packages` — алиас `/tariffs`.
- `/backup_db` — отправить SQLite backup базы данных администратору.
- `/text_menu` или `/texts` — открыть меню редактора текстов.
- `/list_texts` — показать список ключей текстов.
- `/get_text <key>` — посмотреть текущий текст.
- `/set_text <key>` — перейти в режим изменения текста.
- `/reset_text <key>` — сбросить текст к стандартному.
- `/cancel_text` — отменить редактирование текста.
- `/debug_payments` — диагностика платежей для администраторов из `ADMIN_IDS`.
- `/debug_access` — диагностика доступа текущего пользователя для администраторов и secret access.
- `/debug_offer` — диагностика launch-offer текущего администратора.
- `/debug_user_access <telegram_id>` — диагностика доступа конкретного пользователя.
- `/reset_my_offer` — сбросить launch-offer текущему администратору.
- `/start_my_offer` — запустить launch-offer текущему админу на 24 часа.
- `/selftest_access` — внутренний self-test access/usage логики без внешнего API.
- `/reset_limits` — сбросить `trial_requests_used` и `monthly_requests_used` всем пользователям, только для `ADMIN_IDS`.
- `/reset_trials` — сбросить trial всем пользователям без удаления paid-подписок, только для `ADMIN_IDS`.
- `/reset_user_trial <telegram_id>` — сбросить trial одного пользователя без изменения paid-подписки и месячного лимита.
- `/reset_user_limits <telegram_id>` — сбросить trial и monthly usage одного пользователя без изменения подписки и платежей.

## Данные

SQLite используется как MVP-хранилище. Бот не сохраняет исходные изображения пользователей. Для истории распознаваний сохраняются:

- Telegram ID пользователя.
- Source и referral payload, если пользователь пришёл через deep link.
- SHA-256 hash изображения.
- Найденный шрифт.
- JSON-ответ WhatFontIs.
- Статус, `result_type`, `provider_success` и флаг списания запроса.
- Платный баланс распознаваний и состояние launch-offer.
