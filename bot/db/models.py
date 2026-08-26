"""Схема БД. SQLAlchemy 2.0, async-совместимая."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bot.db.types import Money


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Общий базовый класс моделей."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    country: Mapped[str] = mapped_column(String(2), default="KZ")
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Канонические ключи магазинов через запятую («steam,gog»).
    # Пустая строка означает «все магазины» — так по умолчанию.
    preferred_shops: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    watches: Mapped[list[Watch]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg_id={self.tg_id}>"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ID в IsThereAnyDeal (UUID-строка). Может отсутствовать, если игра
    # найдена только через Steam/CheapShark.
    itad_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    steam_appid: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True, default=None
    )
    cheapshark_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, default=None
    )
    title: Mapped[str] = mapped_column(String(300), index=True)
    slug: Mapped[str | None] = mapped_column(String(300), index=True, default=None)
    image_url: Mapped[str | None] = mapped_column(Text, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    snapshots: Mapped[list[PriceSnapshot]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Game id={self.id} title={self.title!r}>"


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Источник ID: itad | cheapshark | steam | epic — один магазин может
    # прийти из разных API с разными идентификаторами.
    source: Mapped[str] = mapped_column(String(16), default="itad")
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_shops_source_external"),
    )

    def __repr__(self) -> str:
        return f"<Shop {self.source}:{self.external_id} {self.name!r}>"


class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )
    target_price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    # true — уведомлять о любом падении ниже последней замеченной цены,
    # даже если цель ещё не достигнута
    notify_any_drop: Mapped[bool] = mapped_column(Boolean, default=False)
    # цена, о которой уже уведомили: повторное письмо шлём только если стало
    # дешевле неё — иначе бот будет спамить каждый час
    last_notified_price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="watches")
    game: Mapped[Game] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("user_id", "game_id", name="uq_watches_user_game"),
    )

    def __repr__(self) -> str:
        return f"<Watch user={self.user_id} game={self.game_id}>"


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )
    shop_id: Mapped[int] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Money)
    regular_price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    cut: Mapped[int] = mapped_column(Integer, default=0)
    # ISO-4217. В одной таблице соседствуют ₸ (ITAD/Steam/Epic) и $
    # (CheapShark), поэтому цена без валюты бессмысленна.
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    url: Mapped[str | None] = mapped_column(Text, default=None)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    game: Mapped[Game] = relationship(back_populates="snapshots")
    shop: Mapped[Shop] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_snapshots_game_checked", "game_id", "checked_at"),
    )

    def __repr__(self) -> str:
        return f"<PriceSnapshot game={self.game_id} {self.price} {self.currency}>"
