# Samara Realty Watch

Личный production-oriented MVP для автоматического сбора статистики по трёхкомнатным квартирам в Самаре. Система хранит историю наблюдений и цен, помогает находить новые объявления, снижение цены и вероятные дубли между площадками, а результаты показывает в локальном web UI.

Это личный аналитический инструмент. Он не является финансовой, инвестиционной или юридической консультацией. Ипотечные расчёты ориентировочные: реальные условия, ПСК, страховки, требования банка, объект кредита и право на льготную программу нужно проверять отдельно.

## Что внутри

- Python 3.12, Typer CLI, FastAPI web UI, Playwright persistent context.
- PostgreSQL 16, SQLAlchemy 2.x async, Alembic.
- YAML-конфигурация поисков и scoring.
- JSON-логи через structlog.
- Web UI с фильтрами по цене, площади, этажу, району, источнику и изменениям цены.
- Локальный HTML-отчёт как простой экспорт/резервный просмотр.
- Без обхода CAPTCHA, антибот-защит, fingerprint/rate-limit обхода, прокси-ротации.
- Metabase в отдельном compose profile `analytics`.

## Быстрый старт

```bash
make init
cp config/searches.example.yaml config/searches.yaml
cp config/scoring.example.yaml config/scoring.yaml
cp .env.example .env
make up
make migrate
make browser-init
make collect
make web
make scheduler
make report
```

В `.env` не храните реальные секреты в git. Telegram-бот не нужен для основного сценария и не используется отчётом.

Рабочий каталог проекта:

```bash
cd /d/dev/samara_realty_watch
```

## Конфигурация поисков

Создайте сохранённый поиск на сайте вручную, авторизуйтесь в обычном Chromium и вставьте URL:

```yaml
searches:
  - name: "yandex_samara_3rooms_main"
    source: "yandex_realty"
    enabled: true
    city: "Самара"
    rooms: 3
    url: "https://realty.yandex.ru/samara/kupit/kvartira/tryohkomnatnaya/..."
    interval_hours: 4
    max_pages: 10
```

Для Домклика, Циан и Авито можно оставить `enabled: false`, пока не сохранены реальные HTML fixtures для точной настройки селекторов.

## Авторизация в браузере

```bash
make browser-init
```

Команда запускает Chromium в headful-режиме с persistent profile из `BROWSER_PROFILE_DIR`. Войдите на нужные сайты, дождитесь сохранения cookies/local storage и нажмите `Ctrl+C`. Профиль не удаляется и используется последующими `make collect`.

На Linux-хосте для видимого Chromium из контейнера может потребоваться проброс `DISPLAY`/Wayland или запуск команды вне контейнера в той же структуре проекта.

## Ручной сбор

```bash
make collect
HEADLESS=false make collect
docker compose run --rm collector python -m app collect --source yandex_realty
docker compose run --rm collector python -m app collect --search yandex_samara_3rooms_main
```

Если сайт показывает CAPTCHA, просит логин или изменилась разметка, сборщик не пытается обходить защиту. Он сохраняет HTML и screenshot в `data/debug`, пишет ошибку в `collector_runs` и продолжает другие источники.

## Автоматический сбор

```bash
make scheduler
```

Compose-сервис `scheduler` запускает `python -m app collect` каждые 2 часа. Интервал можно изменить через `.env`:

```env
COLLECT_INTERVAL_SECONDS=7200
```

Логи:

```bash
docker compose logs -f scheduler
```

## Web UI

```bash
make web
```

После запуска откройте `http://localhost:8000`. В web UI доступны:

- список объявлений из PostgreSQL;
- фильтры по цене, цене за м², площади, этажу, этажности дома, району и источнику;
- фильтр по объявлениям с изменениями цены за выбранный период;
- статистика по каждому объявлению: наблюдения, мин/макс цена, изменение с первого наблюдения;
- карточка объявления с историей наблюдений и изменениями цены.

## HTML-отчёт

```bash
make report
```

Команда создаёт локальный файл `data/reports/index.html`. Это статический снимок базы, который можно открыть в браузере без запущенного backend. В нём есть:

- объявления, увиденные за последние 7 дней;
- цену, цену за м², площадь, этаж, адрес и ссылку на источник;
- дату первого и последнего наблюдения;
- изменения цены, если объявление уже встречалось раньше с другой ценой.

Для другого периода:

```bash
docker compose run --rm collector python -m app report --days 30
```

## CLI

```bash
python -m app init-db
python -m app browser-init
python -m app collect
python -m app stats
python -m app report
python -m app stats --district "октябрьский"
python -m app listing show <uuid>
python -m app listing duplicates
python -m app debug export-html --source domclick
```

## Systemd timer

Примеры unit-файлов лежат в `scripts/`.

```bash
sudo cp scripts/realty-collector.service /etc/systemd/system/realty-collector.service
sudo cp scripts/realty-collector.timer /etc/systemd/system/realty-collector.timer
sudo systemctl daemon-reload
sudo systemctl enable --now realty-collector.timer
systemctl list-timers realty-collector.timer
```

Таймер запускает сбор раз в 4 часа с `RandomizedDelaySec=20min`.

## Metabase

```bash
docker compose --profile analytics up -d metabase
```

После запуска откройте `http://localhost:3000` и подключите PostgreSQL `postgres:5432`, БД `realty`.

## Backup PostgreSQL

```bash
docker compose exec postgres pg_dump -U realty -d realty > realty_backup.sql
```

Восстановление:

```bash
docker compose exec -T postgres psql -U realty -d realty < realty_backup.sql
```

## Как добавить источник

1. Добавьте URL в `config/searches.yaml`.
2. Если JSON-LD/data-атрибутов недостаточно, сохраните HTML выдачи в `tests/fixtures/<source>.html`.
3. Настройте адаптер в `collectors/<source>.py`.
4. Добавьте unit-тест на fixture.

Для Домклика, Циан и Авито сейчас добавлены безопасные заготовки. Нужны реальные HTML-фрагменты карточек выдачи после вашей авторизации, чтобы не выдумывать неподтверждённые селекторы.

## Очистка debug-файлов

Debug-файлы пишутся в `data/debug/screenshots` и `data/debug/html`. Для ротации можно добавить cron:

```bash
find /opt/samara_realty_watch/data/debug -type f -mtime +30 -delete
```

## Проверки

```bash
make lint
make test
```

Тесты используют fixtures из `tests/fixtures` и не ходят на живые сайты.
