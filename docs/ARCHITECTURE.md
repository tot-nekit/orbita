# Архитектура Орбиты

## Границы проекта

Проект разделён по ответственности: `app/` содержит продукт, `scripts/` — операционные команды и отдельные runtime, `tests/` — проверки, `docs/` — документацию, `resources/` — импортируемые данные. Корень содержит только launcher и конфигурацию сборки.

```text
app/static ── HTTP/Mini App ──► app.server ──► app.service ──► app.store ──► SQLCipher/WAL
Telegram ──► app.telegram_bot ───────────────────────┘             │
Webhook ──► scripts.scheduled ──► app.webhooks/notifications       └─ data/backups
```

`run.py` — единый launcher для `web`, `bot`, `all`, `backup` и `doctor`. Стартовые файлы только выбирают Python и вызывают его. Команды из `scripts/` запускаются модулями (`python -m scripts.configure`), поэтому не зависят от случайной текущей директории.

## Каталоги и модули

| Путь | Ответственность |
| --- | --- |
| `app/server.py` | HTTP маршруты, cookie/Bearer, CSP, лимиты и статусы |
| `app/auth.py`, `app/security.py` | аккаунты, scrypt, сессии, Telegram HMAC и одноразовые коды |
| `app/service.py` | валидация действий, черновики, подтверждения и конфликты |
| `app/store.py`, `app/database.py` | owner scope, транзакции, миграции, SQLCipher и WAL |
| `app/telegram_bot.py`, `app/notifications.py`, `app/webhooks.py` | Telegram transport, outbox, leases и повторная доставка |
| `app/dates.py`, `app/focus.py`, `app/context.py` | часовые пояса, фокус дня и текущий owner |
| `app/ai.py` | OpenAI-compatible LLM/STT adapter без доступа модели к БД |
| `app/static/` | vanilla JavaScript/CSS UI без сборки |
| `scripts/configure.py` | интерактивная локальная настройка `.env` |
| `scripts/manage.py` | операции с ключами, шифрованием и legacy-данными |
| `scripts/backup.py` | online backup с integrity check и ротацией |
| `scripts/hosted.py` | Waitress entry point для managed hosting |
| `scripts/scheduled.py` | ограниченный cron/webhook цикл |
| `scripts/setup_webhook.py` | настройка Telegram webhook на HTTPS origin |
| `scripts/prepare_hosted.py` | подготовка новой managed-hosting установки |
| `scripts/release.py` | allowlist исходного релиза без секретов и runtime-данных |

Зависимости направлены внутрь: HTTP и Telegram вызывают service/store, а доменная логика не знает о конкретном transport. `scripts/` может вызывать `app/`, но бизнес-правила не дублирует. `.env`, база, WAL, логи и резервные копии находятся в runtime-слое и исключены из Git и Docker build context.

## Каналы и изоляция

Сайт использует отзывной HttpOnly cookie, Mini App — временный Bearer после проверки подписанного `initData`, Telegram — проверенный update. Все записи, диалоги, уведомления и receipts привязаны к текущему owner; owner не берётся из тела запроса. Production требует HTTPS origin, Secure cookie, регистрацию, SQLCipher и постоянный ключ.

SQLCipher шифрует базу, WAL и резервные копии при хранении. `store.atomic()` использует `BEGIN IMMEDIATE`, а идемпотентный receipt коммитится вместе с изменением. Повтор с тем же ключом возвращает исходный результат, другой payload получает конфликт. Редактирование проверяет revision, удаление мягкое и восстанавливается десять минут.

Напоминания проходят через durable outbox. Worker берёт lease, повторяет отправку с backoff и перед доставкой повторно проверяет owner, срок, статус и согласие на уведомления. Доставка имеет семантику at-least-once: сетевой сбой может привести к редкому повтору.

## Запуск и проверки

```sh
python -m scripts.configure
python run.py doctor
python run.py all
python -m scripts.test
npm run test:ui
```

Для managed hosting используются `python -m scripts.hosted`, `python -m scripts.scheduled` и `python -m scripts.setup_webhook`. Полные инструкции находятся в `docs/archive/ALWAYSDATA.md`, `docs/archive/HOSTING.md`, `docs/DEPLOYMENT.md` и `docs/PRIVACY.md`.

## Ограничения

Одна база на постоянном диске. Не реализованы горизонтальное масштабирование, Calendar sync, recurrence, browser push, совместные пространства и одновременный polling и webhook для одного токена.
