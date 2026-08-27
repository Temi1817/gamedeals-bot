FROM python:3.12-slim

# Логи должны идти в stdout сразу, иначе в docker logs пусто до перезапуска
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не будет тянуть переустановку
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY migrations ./migrations
COPY alembic.ini ./
# Служебные скрипты: users.py, probe_apis.py — запускаются через
# docker compose exec, поэтому должны быть внутри образа
COPY scripts ./scripts

# База лежит в томе, каталог должен существовать до первого запуска
RUN mkdir -p /app/data && useradd --create-home --uid 1000 bot \
    && chown -R bot:bot /app
USER bot

CMD ["python", "-m", "bot.main"]
