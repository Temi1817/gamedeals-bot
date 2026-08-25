"""Слой доступа к данным. Хендлеры не пишут SQL сами."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Game, PriceSnapshot, Shop, User, Watch


# --------------------------------------------------------------------------- #
# пользователи
# --------------------------------------------------------------------------- #
class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        user: User | None = await self.session.scalar(
            select(User).where(User.tg_id == tg_id)
        )
        return user

    async def get_or_create(
        self, tg_id: int, username: str | None, country: str = "KZ"
    ) -> User:
        user = await self.get_by_tg_id(tg_id)
        if user is None:
            user = User(tg_id=tg_id, username=username, country=country)
            self.session.add(user)
            await self.session.flush()
            return user
        if username is not None and user.username != username:
            user.username = username
        return user

    async def set_country(self, user: User, country: str) -> None:
        user.country = country.upper()

    async def set_notify(self, user: User, enabled: bool) -> None:
        user.notify_enabled = enabled

    async def all_with_notifications(self) -> list[User]:
        result = await self.session.scalars(
            select(User).where(User.notify_enabled.is_(True))
        )
        return list(result)


# --------------------------------------------------------------------------- #
# игры
# --------------------------------------------------------------------------- #
class GameRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, game_id: int) -> Game | None:
        return await self.session.get(Game, game_id)

    async def find(
        self,
        *,
        itad_id: str | None = None,
        steam_appid: int | None = None,
        cheapshark_id: str | None = None,
    ) -> Game | None:
        """Ищет игру по любому из внешних идентификаторов."""
        for column, value in (
            (Game.itad_id, itad_id),
            (Game.steam_appid, steam_appid),
            (Game.cheapshark_id, cheapshark_id),
        ):
            if value is None:
                continue
            game = await self.session.scalar(select(Game).where(column == value))
            if game is not None:
                return game
        return None

    async def upsert(
        self,
        *,
        title: str,
        itad_id: str | None = None,
        steam_appid: int | None = None,
        cheapshark_id: str | None = None,
        slug: str | None = None,
        image_url: str | None = None,
    ) -> Game:
        """Находит игру по внешним ID или создаёт, дополняя недостающие поля."""
        game = await self.find(
            itad_id=itad_id, steam_appid=steam_appid, cheapshark_id=cheapshark_id
        )
        if game is None:
            game = Game(
                title=title,
                itad_id=itad_id,
                steam_appid=steam_appid,
                cheapshark_id=cheapshark_id,
                slug=slug,
                image_url=image_url,
            )
            self.session.add(game)
            await self.session.flush()
            return game

        # доклеиваем идентификаторы, полученные из другого источника
        game.title = title or game.title
        game.itad_id = game.itad_id or itad_id
        game.steam_appid = game.steam_appid or steam_appid
        game.cheapshark_id = game.cheapshark_id or cheapshark_id
        game.slug = game.slug or slug
        game.image_url = image_url or game.image_url
        await self.session.flush()
        return game


# --------------------------------------------------------------------------- #
# магазины
# --------------------------------------------------------------------------- #
class ShopRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, source: str, external_id: str, name: str) -> Shop:
        shop = await self.session.scalar(
            select(Shop).where(
                Shop.source == source, Shop.external_id == str(external_id)
            )
        )
        if shop is None:
            shop = Shop(source=source, external_id=str(external_id), name=name)
            self.session.add(shop)
            await self.session.flush()
        elif shop.name != name:
            shop.name = name
        return shop


# --------------------------------------------------------------------------- #
# вотчлист
# --------------------------------------------------------------------------- #
class WatchRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, watch_id: int) -> Watch | None:
        return await self.session.get(Watch, watch_id)

    async def for_user(self, user_id: int) -> list[Watch]:
        result = await self.session.scalars(
            select(Watch)
            .where(Watch.user_id == user_id)
            .options(selectinload(Watch.game))
            .order_by(Watch.created_at.desc())
        )
        return list(result)

    async def all_active(self) -> list[Watch]:
        """Все отслеживания вместе с игрой и пользователем — для джобы."""
        result = await self.session.scalars(
            select(Watch).options(
                selectinload(Watch.game), selectinload(Watch.user)
            )
        )
        return list(result)

    async def add(
        self,
        *,
        user_id: int,
        game_id: int,
        target_price: Decimal | None,
        currency: str = "KZT",
        notify_any_drop: bool = False,
    ) -> tuple[Watch, bool]:
        """Возвращает (запись, создана_ли_новая). Повтор обновляет цель."""
        watch = await self.session.scalar(
            select(Watch).where(Watch.user_id == user_id, Watch.game_id == game_id)
        )
        if watch is not None:
            watch.target_price = target_price
            watch.currency = currency
            watch.notify_any_drop = notify_any_drop
            watch.last_notified_price = None  # цель сменилась — счётчик сбрасываем
            await self.session.flush()
            return watch, False

        watch = Watch(
            user_id=user_id,
            game_id=game_id,
            target_price=target_price,
            currency=currency,
            notify_any_drop=notify_any_drop,
        )
        self.session.add(watch)
        await self.session.flush()
        return watch, True

    async def remove(self, watch_id: int, user_id: int) -> bool:
        """Удаляет отслеживание, если оно принадлежит этому пользователю."""
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                delete(Watch).where(Watch.id == watch_id, Watch.user_id == user_id)
            ),
        )
        return bool(result.rowcount)

    async def mark_notified(self, watch: Watch, price: Decimal) -> None:
        watch.last_notified_price = price

    async def count_for_user(self, user_id: int) -> int:
        return (
            await self.session.scalar(
                select(func.count()).select_from(Watch).where(Watch.user_id == user_id)
            )
        ) or 0


# --------------------------------------------------------------------------- #
# история цен
# --------------------------------------------------------------------------- #
class SnapshotRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        game_id: int,
        shop_id: int,
        price: Decimal,
        regular_price: Decimal | None,
        cut: int,
        currency: str,
        url: str | None,
    ) -> PriceSnapshot:
        snapshot = PriceSnapshot(
            game_id=game_id,
            shop_id=shop_id,
            price=price,
            regular_price=regular_price,
            cut=cut,
            currency=currency,
            url=url,
        )
        self.session.add(snapshot)
        return snapshot

    async def history(
        self, game_id: int, currency: str, days: int = 180, limit: int = 500
    ) -> list[PriceSnapshot]:
        since = datetime.now(UTC) - timedelta(days=days)
        result = await self.session.scalars(
            select(PriceSnapshot)
            .where(
                PriceSnapshot.game_id == game_id,
                PriceSnapshot.currency == currency,
                PriceSnapshot.checked_at >= since,
            )
            .order_by(PriceSnapshot.checked_at)
            .limit(limit)
        )
        return list(result)

    async def local_low(
        self, game_id: int, currency: str
    ) -> tuple[Decimal, datetime] | None:
        """Минимум по нашим собственным замерам — запасной вариант, если
        исторический минимум от ITAD недоступен."""
        row = (
            await self.session.execute(
                select(PriceSnapshot.price, PriceSnapshot.checked_at)
                .where(
                    PriceSnapshot.game_id == game_id,
                    PriceSnapshot.currency == currency,
                )
                .order_by(PriceSnapshot.price, desc(PriceSnapshot.checked_at))
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        return row[0], row[1]

    async def prune(self, older_than_days: int = 365) -> int:
        """Чистка старых замеров, чтобы SQLite не пух бесконечно."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                delete(PriceSnapshot).where(PriceSnapshot.checked_at < cutoff)
            ),
        )
        return result.rowcount or 0
