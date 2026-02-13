# molt-mail-digest

Telegram-бот, который по расписанию собирает новые письма из IMAP-папки, прогоняет их через LLM и отправляет краткий дайджест.

Проект ориентирован на рабочий сценарий: раз в несколько часов собирать только новые письма (по UID), делать короткие выжимки и показывать сводку в Telegram.

## Что делает сервис

- Подключается к IMAP-ящику и читает новые письма из нужной папки.
- Учитывает только письма с `UID > last_uid` (состояние хранится в SQLite).
- Чистит тело письма (обрезает шум, подписи, quoted-блоки).
- Для каждого письма получает однострочное summary через LLM (OpenAI-compatible API, по умолчанию Groq endpoint).
- Собирает итоговый дайджест:
  - **СВОДКА** (числа считаются кодом, не моделью);
  - **ЗАЯВКИ** (письма с ID заявки в теме вида `12345` / `12345-МСК` и т.п.);
  - **ПРОЧЕЕ** (группировка остальных писем по темам);
  - **НЕ ОБРАБОТАНО** (если были ошибки обработки).
- Отправляет дайджест в Telegram по расписанию (cron-like расписание через APScheduler) и по команде вручную.

## Управление через Telegram

Доступ ограничен одним `TELEGRAM_CHAT_ID` (owner chat).

Доступные команды:

- `/status` — текущее состояние (`paused`, `last_uid`, папка, часы расписания, модель).
- `/pause` — поставить автодайджесты на паузу.
- `/resume` — снять паузу.
- `/digest_now` — собрать и отправить дайджест немедленно.
- `/jobs_spb_now` — прочитать новые посты из Telegram-каналов и отправить только вакансии СПб.

## Конфигурация

Сервис читает переменные окружения из `.env` (см. `docker-compose.yml`).

### Обязательные переменные

```env
IMAP_HOST=imap.example.com
IMAP_USER=me@example.com
IMAP_PASSWORD=your_password

TELEGRAM_BOT_TOKEN=123456:ABCDEF...
TELEGRAM_CHAT_ID=123456789

LLM_API_KEY=...
```

### Опциональные переменные (с дефолтами)

```env
IMAP_PORT=993
IMAP_FOLDER=INBOX/ONLINE

# OpenAI-compatible endpoint/model
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Также поддерживаются fallback-переменные:
# GROQ_API_KEY / OPENAI_API_KEY
# OPENAI_BASE_URL / OPENAI_MODEL

TZ=Europe/Moscow
SCHEDULE_HOURS=10,12,14,16,18

MAX_EMAILS_PER_RUN=80
MAX_CHARS_PER_EMAIL=20000
SUMMARY_MAX_OUTPUT_TOKENS=220
DIGEST_MAX_OUTPUT_TOKENS=900

LOG_LEVEL=INFO
```


### Переменные для Telegram user-source (вакансии из каналов)

```env
# Включить источник вакансий из каналов
TELEGRAM_USER_ENABLED=1

# Данные приложения Telegram (my.telegram.org)
TELEGRAM_USER_API_ID=123456
TELEGRAM_USER_API_HASH=abcdef1234567890abcdef1234567890

# Авторизованная StringSession Telethon
TELEGRAM_USER_SESSION=PASTE_TELETHON_STRING_SESSION_HERE

# Каналы-источники через запятую: @username или ID
TELEGRAM_SOURCE_CHANNELS=@jobs_channel_1,@jobs_channel_2

# Сколько новых постов максимум читать за один запуск
TELEGRAM_SOURCE_FETCH_LIMIT=80

# Стоп-слова в названии вакансии (в любом регистре/части слова)
TELEGRAM_VACANCY_BANNED_WORDS=врач,водитель,агент,терапевт,диспетчер
```

Куда приходит результат:
- в тот же owner-чат, что и остальные сообщения бота (`TELEGRAM_CHAT_ID`).

## Как запустить

### Вариант 1 (рекомендуется): Docker Compose

1. Создайте `.env` в корне проекта.
2. Запустите:

```bash
docker compose up -d --build
```

3. Проверьте логи:

```bash
docker compose logs -f app
```

Состояние хранится в Docker volume `appdata` (`/data/state.db` внутри контейнера).

### Вариант 2: локально без Docker

```bash
cd app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m main
```

> В локальном режиме SQLite тоже пишет в `/data/state.db`, поэтому убедитесь, что путь доступен для записи.

## Как работает расписание

Используется `APScheduler` с `CronTrigger` (будни `mon-fri`, минуты `:00`).

Пример:

- `SCHEDULE_HOURS=10,12,14,16,18`
- `TZ=Europe/Moscow`

означает, что автодайджест будет запускаться в 10:00, 12:00, 14:00, 16:00 и 18:00 по Москве в рабочие дни.

Если нужен шаг «каждые 2 часа», можно задать:

```env
SCHEDULE_HOURS=8,10,12,14,16,18,20
```

## Важные детали

- При изменении `UIDVALIDITY` в IMAP-папке сервис сбрасывает `last_uid` в 0 и начинает отслеживание заново.
- Даже если для отдельных писем LLM вернул ошибку, `last_uid` продвигается дальше (чтобы не зациклиться на проблемных письмах).
- Длинные сообщения автоматически режутся на части под лимит Telegram.
- В логах редактируется токен Telegram-бота (маскируется).

## Типичный сценарий эксплуатации

1. Развернуть через Docker Compose.
2. Убедиться, что бот отвечает на `/status`.
3. Проверить ручной прогон `/digest_now`.
4. Дождаться автоматического запуска по расписанию.
5. При необходимости использовать `/pause` и `/resume`.


## Фильтрация вакансий из Telegram-постов по городу (СПб)

В проект добавлен детерминированный парсер `app/city_extract.py` для сообщений формата:

- `Москва`
- список вакансий + `Ссылка: https://hh.ru/vacancy/...`
- `Санкт-Петербург`
- список вакансий + ссылки
- следующий город

Быстрый пример:

```python
from city_extract import extract_spb_vacancies

items = extract_spb_vacancies(message_text)
for item in items:
    print(item.title, item.link)
```

Функция вернёт только вакансии из блока `Санкт-Петербург`, добавит к каждой вакансии `company` из строки `Компания: ...` и проигнорирует остальные города.

## Возможные проблемы

- **`Missing required env var`** — не хватает обязательной переменной в `.env`.
- **Не приходит дайджест по расписанию** — проверьте `TZ`, `SCHEDULE_HOURS`, а также не включена ли пауза (`/status`).
- **Ошибки IMAP select/fetch** — проверьте `IMAP_FOLDER`, права пользователя и корректность IMAP-логина.
- **Ошибки LLM API** — проверьте `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` и сетевую доступность endpoint.

---

Если хотите, можно расширить README отдельным разделом с готовыми пресетами расписания (например, «каждые 2 часа в рабочее время», «только утром», «24/7»).


## Как подключить Telegram user (кратко)

1. Создайте `api_id`/`api_hash` на `my.telegram.org`.
2. Локально получите `StringSession` Telethon (один раз), вставьте в `TELEGRAM_USER_SESSION`.
3. Укажите каналы в `TELEGRAM_SOURCE_CHANNELS`.
4. Перезапустите сервис и вызовите `/jobs_spb_now`.

Мини-скрипт для получения `StringSession`:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 123456
api_hash = "your_api_hash"

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
```

> Скрипт попросит номер телефона/код/2FA при первом запуске. Полученную строку храните как секрет.


Стоп-слова по умолчанию: `врач, водитель, агент, терапевт, диспетчер`. Если одно из слов встречается в названии вакансии (в любом виде), такая вакансия не отправляется.
