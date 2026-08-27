# GameDeals Bot

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![aiogram 3](https://img.shields.io/badge/aiogram-3.x-2CA5E0)](https://docs.aiogram.dev/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Telegram-бот, который ищет, где PC-игра стоит дешевле всего, и пишет,
когда она подешевела. Регион по умолчанию — **Казахстан, ₸**.

Бот: [@steamitik_bot](https://t.me/steamitik_bot)

```
🎮 Cyberpunk 2077
━━━━━━━━━━━━━━━
💰 Лучшая цена
   4 113 ₸
   GOG · −70% · было 13 722 ₸
   спишут $8.99
━━━━━━━━━━━━━━━
🏬 Другие магазины

🥈 Steam
   17 999 ₸
🥉 Humble Store
   ≈27 448 ₸

   и ещё 10 магазинов — от ≈12 153 ₸
━━━━━━━━━━━━━━━
📉 Минимум за всё время
   8 231 ₸ · GOG · 17.06.2026

🔥 Это исторический минимум или ниже. Лучше не будет.
```

---

## Что умеет

| Что | Как вызвать |
|---|---|
| Поиск игры и цены по всем магазинам | написать название или `/find` |
| Скидки дешевле суммы | написать число: `5000` |
| Топ продаж Steam / Epic / общий рейтинг | `🏆 Популярное`, `/top` |
| Скидки с фильтром по проценту | `🔥 Скидки`, `/deals` |
| Бесплатные раздачи Epic | `🎁 Раздачи`, `/free` |
| Отслеживание цены с уведомлением | кнопка 🔔 на карточке, `/watch` |
| Список отслеживаемого | `🔔 Отслеживаю`, `/list` |
| Регион и выбор магазинов | `⚙️ Настройки`, `/settings` |

Все действия доступны кнопками — команды набирать не нужно.

**Фоновые задачи:** проверка цен раз в 60 минут с уведомлением о снижении,
автопост раздач в 20:00 по местному времени.

---

## Быстрый старт

```bash
git clone <repo> && cd telega_bot

python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # вписать BOT_TOKEN и ITAD_API_KEY
python -m bot.main
```

Миграции применяются сами при запуске — отдельная команда не нужна.

### Что положить в `.env`

| Переменная | Обязательно | Где взять |
|---|---|---|
| `BOT_TOKEN` | да | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ITAD_API_KEY` | нет, но желательно | [isthereanydeal.com/apps/my](https://isthereanydeal.com/apps/my/) → регистрация приложения → поле **API Key** |

⚠️ ITAD выдаёт три значения: **API Key**, Client ID и Client Secret.
Нужен именно первый — Client ID и Secret нужны для OAuth, то есть доступа
к вишлисту конкретного пользователя, а нам это ни к чему.

Без ключа ITAD бот работает, но теряет GOG, Humble, Fanatical,
GreenManGaming и остальных: останутся только Steam, Epic и CheapShark.

Остальные настройки — регион, TTL кэшей, интервалы задач, вебхук — с
разумными значениями по умолчанию, см. [.env.example](.env.example).

---

## Разработка

```bash
pip install -r requirements-dev.txt

pytest                  # 316 тестов
ruff check .            # линтер
mypy                    # строгая типизация
```

```bash
python scripts/probe_apis.py            # проверить все источники
python scripts/probe_apis.py itad steam # только выбранные
```

`probe_apis.py` дёргает каждый источник живым запросом и печатает сырой
ответ. Первое, что стоит запустить, если бот вдруг начал врать: скорее
всего у кого-то поменялась схема.

---

## Как устроено

```
bot/
├── main.py            точка входа: миграции, роутеры, планировщик
├── config.py          pydantic-settings, всё из .env
├── handlers/          start, search, deals, free, top, watchlist, menu, errors
├── keyboards/         инлайн-кнопки и постоянное меню
├── services/
│   ├── itad.py        IsThereAnyDeal — охват магазинов и история цен
│   ├── steam.py       Steam — цены в тенге, топ продаж
│   ├── gog.py         GOG — региональные цены
│   ├── epic.py        Epic — раздачи, витрина, топ продаж
│   ├── cheapshark.py  CheapShark — резерв, если ITAD лёг
│   ├── rates.py       курсы валют
│   ├── aggregator.py  сводит источники в единый список
│   ├── shops.py       канонические имена магазинов
│   ├── cache.py       TTL-кэш
│   ├── http.py        таймауты, retry, Retry-After
│   └── models.py      Game, Offer, Shop, Deal, FreeGame
├── db/                модели, репозитории, сессии
├── jobs/              проверка цен, автопост раздач, планировщик
└── utils/             форматирование цен, рендер карточек, логи
```

Клиенты возвращают доменные модели, а не сырой JSON — хендлеры не знают,
из какого API пришли данные.

### Почему нужен агрегатор

У ITAD широкий охват магазинов, но **нет региональных цен для Казахстана**:
для `country=KZ` он отдаёт международный прайс. Магазины при этом продают
казахстанцам заметно дешевле. Поэтому цены Steam, GOG и Epic берутся у
самих витрин и подменяют данные ITAD:

| Cyberpunk 2077 | ITAD для KZ | Реальная цена |
|---|---|---|
| GOG | $17.99 | **$8.99** |
| HITMAN WoA в Epic | ≈12 807 ₸ | **5 184 ₸** |

Остальные магазины остаются с международным прайсом и помечаются знаком
`≈`. Точные цены помечены `✅` в сноске карточки.

Подробности и остальные грабли — в [docs/api-notes.md](docs/api-notes.md).

---

## Деплой

### Docker

```bash
docker compose up -d
```

### VPS вручную

```bash
pip install -r requirements.txt
python -m bot.main
```

Для systemd — юнит с `Restart=always` и `EnvironmentFile=/path/.env`.

### Вебхук вместо long polling

```env
USE_WEBHOOK=true
WEBHOOK_BASE_URL=https://your.domain
WEBHOOK_SECRET=любая-длинная-строка
WEBAPP_PORT=8080
```

База по умолчанию — SQLite. Переезд на Postgres — смена `DATABASE_URL`,
код к этому готов: деньги хранятся целым числом минорных единиц, а не
`NUMERIC`, миграции идут через Alembic в batch-режиме.

---

## Стек

Python 3.11+ · aiogram 3 · httpx · SQLAlchemy 2 (async) + aiosqlite ·
Alembic · APScheduler · pydantic-settings · structlog

Тесты: pytest + respx на замоканных ответах, снятых с живых API.

---

## Ограничения

- **Цены не всех магазинов точные.** Steam, GOG и Epic — точные. Humble,
  Fanatical, GreenManGaming, Microsoft Store и прочие — международный
  прайс, публичных API у них нет.
- **Витрина Epic покрывает не весь каталог** — около трёхсот игр из
  подборок и распродаж. Для остальных остаётся цена ITAD.
- **История скидок международная.** ITAD знает её за годы, но в долларах —
  на графике такие точки помечены `≈`. Наши собственные замеры по Steam,
  GOG и Epic подмешиваются как точные и со временем вытесняют пересчёт.
- **Курс валют приблизительный.** Пересчёт в тенге — ориентир, банк
  спишет по своему курсу. Поэтому у неточных цен стоит `≈`.
- **Календаря распродаж нет.** Публичного API для дат сезонных распродаж
  не существует ни у Steam, ни у ITAD — подробности в
  [docs/api-notes.md](docs/api-notes.md).

---

## Участие и лицензия

Как поднять проект, что проверить перед пул-реквестом и на какие грабли
не стоит наступать — в [CONTRIBUTING.md](CONTRIBUTING.md).

Лицензия — [MIT](LICENSE).

Проект не аффилирован со Steam, Epic Games, GOG, IsThereAnyDeal и другими
упомянутыми сервисами и использует только их публичные API.
