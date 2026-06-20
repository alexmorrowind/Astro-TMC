# Astro TMS

FastAPI TMS/CRM для логистической компании: сайт менеджера, Google Drive-ссылки и Telegram collector для приема документов из групп/чатов служебного Telegram-аккаунта.

В проекте больше нет Telegram-бота. Документы забирает только `collector`, который работает как отдельный Telegram-аккаунт через Telethon.

## Что реализовано

- Авторизация сайта: `superadmin` и `manager`.
- Менеджер видит только водителей своей компании.
- Таблица водителей, таблица траков, статусы документов, карточки driver/truck.
- Раздел `/telegram/groups`: личные чаты, группы и каналы collector-аккаунта.
- Раздел `/telegram/request-docs`: массовая отправка текста и фото в выбранные личные чаты.
- Раздел `/telegram/incoming`: неразобранные документы из Telegram.
- Привязка чата к водителю: ответы с документами автоматически уходят в карточку водителя.
- Просмотр и скачивание документов прямо с сайта через `/documents/{id}/view` и `/download`.
- Настраиваемый список обязательных документов в админке отдельно для `driver` и `truck`.
- Сканирование существующего Google Drive: компании, drivers, trucks и уже загруженные файлы попадают в локальную таблицу.
- Google Drive upload service: `Company -> Driver -> Driver_DocumentType.ext`.
- SQLite локально по умолчанию; можно заменить через `DATABASE_URL`.

## Локальный запуск сайта

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.init_db
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Откройте `http://127.0.0.1:8001`.

Первый вход:

```text
SUPERADMIN_USERNAME=admin
SUPERADMIN_PASSWORD=admin123
```

Демо-данные:

```bash
.venv/bin/python scripts/seed_demo.py
```

## Что написать в `.env`

Минимум для сайта:

```text
APP_SECRET_KEY=replace-with-long-random-string
DATABASE_URL=sqlite:///./tmc.db
SUPERADMIN_USERNAME=admin
SUPERADMIN_PASSWORD=admin123
PUBLIC_BASE_URL=http://127.0.0.1:8001
```

Для Telegram collector:

```text
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_COLLECTOR_PHONE=+15550100000
TELEGRAM_COLLECTOR_SESSION=telegram_collector
TELEGRAM_COLLECTOR_BACKEND_URL=http://127.0.0.1:8001
TELEGRAM_COLLECTOR_REQUIRE_CONNECTED_GROUPS=false
TELEGRAM_INGEST_TOKEN=long-random-secret
```

`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` берутся на `https://my.telegram.org`.

## Как запустить collector

1. Создайте отдельный служебный Telegram-аккаунт. Не используйте основной личный аккаунт.
2. Добавьте этот аккаунт в нужные группы. Личные чаты аккаунта тоже будут видны collector.
3. Запустите collector:

```bash
.venv/bin/python -m app.collector.main
```

Первый запуск попросит код Telegram и, если включен, 2FA-пароль. После входа появится файл `telegram_collector.session`. Его нельзя публиковать или отправлять кому-либо.

4. При старте collector сам добавит все личные чаты, группы и каналы аккаунта в `/telegram/groups`.
5. Collector раз в минуту повторно синхронизирует диалоги, поэтому новые личные чаты появятся на сайте без перезапуска.
6. В `/telegram/groups` выберите компанию и водителя для нужного личного чата.

Если чат привязан к водителю, входящие PDF/фото/документы сразу загрузятся в Google Drive и появятся в карточке водителя. Если чат не привязан, файл попадет в `/telegram/incoming`, где его можно прикрепить вручную.

## Как запросить документы в личных чатах

1. Запустите сайт и collector.
2. Откройте `/telegram/request-docs`.
3. Выберите личные чаты водителей.
4. Напишите текст шаблона и при необходимости прикрепите фото.
5. Нажмите `Поставить в очередь`.

Collector отправляет сообщение из подключенного Telegram-аккаунта. История отправки видна ниже на этой же странице: `pending`, `sent` или `failed`.

## Список документов

В `/admin` можно добавлять обязательные документы отдельно для `driver` и `truck`.

Driver checklist по умолчанию:

```text
CDL
Medical Examination Certificate (Medical Card)
Social Security number (SSN)
Work Authorization / Green Card / US Passport
Email & Phone# - Emergency contact
DrugTest
CCF form
Clearinghouse
Contract
```

Truck checklist по умолчанию:

```text
Truck Registration
Lease Agreement
Annual Inspection
OR Permit
NY Permit
NM Permit
KYU Permit
```

Таблица водителей и карточка водителя сразу используют driver checklist. Таблица `/trucks` использует truck checklist.

Дополнительно можно вывести список чатов в терминал:

```bash
.venv/bin/python -m app.collector.list_chats
```

Локальный тест без Telegram:

```bash
.venv/bin/python scripts/test_ingest.py /path/to/file.pdf
```

## Google Drive

1. Создайте проект в Google Cloud Console.
2. Включите Google Drive API.
3. Создайте Service Account.
4. Скачайте JSON-ключ как `credentials.json`.
5. Создайте корневую папку на Google Drive.
6. Дайте service account доступ к этой папке.
7. Укажите:

```text
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_CREDENTIALS_JSON=
GOOGLE_DRIVE_ROOT_FOLDER_ID=drive-folder-id
```

Если Google Drive не настроен, приложение работает в mock-режиме для локального теста.

Новые документы collector хранит локально в `uploads/` и параллельно загружает в Google Drive. Поэтому сайт может открывать и скачивать документы сам, без перехода в Google Drive.

## Сканирование существующего Google Drive

Кнопка `Сканировать Google Drive` на главной таблице читает уже существующие папки из корневой папки Drive.

Поддерживаемая структура:

```text
Root -> Company -> Drivers -> Driver Name -> files
Root -> Company -> Trucks -> Unit Number -> files
```

Также работает упрощенный вариант:

```text
Root -> Company -> Driver Name -> files
Root -> Company -> Unit 123 -> files
```

Сканер распознает документы по имени файла: CDL, medical, SSN, drug test, clearinghouse, registration, annual inspection, OR/NY/NM/KYU permits и т.д. Если название файла непонятное, запись не теряется в Drive, но в таблице не отмечается как конкретный документ, пока его не переименовать или не прикрепить вручную.

## Публичный тест через GitHub + Render

GitHub Pages не подойдет, потому что это backend-приложение с FastAPI и БД. Схема такая:

```text
GitHub repo -> Render Web Service -> публичный URL сайта
локальный collector -> отправляет файлы на Render URL
```

На Render:

- Подключите GitHub repo через Render Blueprint (`render.yaml`).
- Render создаст Web Service и Postgres database.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables:
  - `APP_SECRET_KEY`
  - `DATABASE_URL` автоматически из Render Postgres
  - `SUPERADMIN_USERNAME`
  - `SUPERADMIN_PASSWORD`
  - `TELEGRAM_INGEST_TOKEN`
  - `PUBLIC_BASE_URL=https://your-render-app.onrender.com`
  - `GOOGLE_CREDENTIALS_JSON` — содержимое `credentials.json` одной строкой
  - `GOOGLE_DRIVE_ROOT_FOLDER_ID`
  - `GOOGLE_DRIVE_CREATE_PUBLIC_LINKS=false`

Локальные файлы `.env`, `credentials.json`, `telegram_collector.session`, `tmc.db` и `uploads/` в GitHub не публикуются.

Для collector локально поменяйте:

```text
TELEGRAM_COLLECTOR_BACKEND_URL=https://your-render-app.onrender.com
TELEGRAM_INGEST_TOKEN=тот_же_секрет_что_на_Render
```

Важно: не коммитьте `.env`, `credentials.json`, `*.session`, `tmc.db`, папку `uploads/`.

## Структура

```text
app/
  collector/        Telethon collector and chat listing
  services/         Access, Drive, groups, incoming documents
  web/              Jinja templates and CSS
  config.py         Environment settings
  database.py       SQLAlchemy engine/session
  init_db.py        DB bootstrap
  main.py           FastAPI web app
  models.py         SQLAlchemy models
scripts/
  create_manager.py
  seed_demo.py
  test_ingest.py
```
