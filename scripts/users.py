"""Кто пользовался ботом: список пользователей и их отслеживания.

Запуск на сервере:
    docker compose exec -T bot python scripts/users.py
Локально:
    python scripts/users.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _db_path() -> Path:
    """Путь к SQLite: из DATABASE_URL, иначе дефолт проекта."""
    url = os.getenv("DATABASE_URL", "")
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return path
    return Path(__file__).resolve().parent.parent / "data" / "bot.db"


def _fmt(value: str | None) -> str:
    """ISO-дата в человеческий вид, всё остальное — как есть."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def main() -> int:
    path = _db_path()
    if not path.exists():
        print(f"База не найдена: {path}", file=sys.stderr)
        return 1

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    users = db.execute(
        """
        SELECT u.id, u.tg_id, u.username, u.country, u.notify_enabled,
               u.preferred_shops, u.created_at,
               (SELECT COUNT(*) FROM watches w WHERE w.user_id = u.id) AS watches
        FROM users u
        ORDER BY u.created_at
        """
    ).fetchall()

    if not users:
        print("Пользователей пока нет.")
        return 0

    print(f"Пользователей: {len(users)}\n")
    for u in users:
        name = f"@{u['username']}" if u["username"] else "без username"
        bell = "уведомления вкл" if u["notify_enabled"] else "уведомления выкл"
        shops = u["preferred_shops"] or "все магазины"
        print(f"#{u['id']}  {name}  tg_id={u['tg_id']}")
        print(f"    пришёл {_fmt(u['created_at'])} · {u['country']} · {bell}")
        print(f"    магазины: {shops} · отслеживает: {u['watches']}")

        rows = db.execute(
            """
            SELECT g.title, w.target_price, w.currency, w.created_at
            FROM watches w JOIN games g ON g.id = w.game_id
            WHERE w.user_id = ?
            ORDER BY w.created_at
            """,
            (u["id"],),
        ).fetchall()
        for w in rows:
            target = (
                f"до {w['target_price']} {w['currency']}"
                if w["target_price"] is not None
                else "любое снижение"
            )
            print(f"      • {w['title']} — {target}, с {_fmt(w['created_at'])}")
        print()

    total_games = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    total_snaps = db.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    last_snap = db.execute("SELECT MAX(checked_at) FROM price_snapshots").fetchone()[0]
    print(f"Игр в базе: {total_games} · замеров цен: {total_snaps}")
    print(f"Последний замер: {_fmt(last_snap)}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
