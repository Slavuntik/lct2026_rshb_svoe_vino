"""ORM-модели — зеркало contracts/schema.sql (ЗАМОРОЖЕН, правки — через
оркестратора). Источник истины по структуре — тот файл, не этот.

Расхождения SQLite vs Postgres (дев-среда без Postgres, см. ORCHESTRATION.md
и B-api.md шаг 1; задокументировано также в reports/b-report.md):

  * `uuid`      -> String(36); значение генерирует приложение (str(uuid4())),
                   а не БД (в SQLite нет gen_random_uuid()).
  * `bigserial` -> Integer autoincrement PK (в SQLite это ROWID-alias; для
                   объёма MVP-демо разницы в диапазоне значений не имеет
                   значения, в Postgres по-прежнему bigserial).
  * `jsonb`     -> generic JSON (в SQLite хранится как TEXT сериализованным
                   json; теряются jsonb-операторы и GIN-индексы, на объёмах
                   демо не требуются).
  * `timestamptz` -> naive DateTime, приложение всегда пишет/читает UTC без
                   tzinfo (SQLite не хранит offset надёжно). В Postgres
                   рекомендуется оставить timestamptz как есть.
  * `gen_random_uuid()` / `now()` как DEFAULT в БД -> те же значения
                   выставляются в Python (default=... на колонке), поведение
                   идентично с точки зрения API.
  * ON DELETE CASCADE / SET NULL воспроизведены через ForeignKey(ondelete=...)
                   и требуют `PRAGMA foreign_keys=ON` на каждое соединение
                   (включено в app/db.py) — в чистом sqlite3 это не дефолт.
  * CHECK-констрейнты (verdict IN (...), role IN (...)) воспроизведены как
                   CheckConstraint — SQLite их фактически поддерживает.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Boolean,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConsentLedger(Base):
    """v0.2 (ревью 01, блокер 1): НАМЕРЕННО без FK на users. Журнал согласий
    обязан пережить удаление аккаунта — доказуемость отзыва нужна ровно после
    того, как пользователь удалился. user_id — просто text/uuid-строка, может
    указывать на гостевой id, у которого никогда не было строки в users.
    """

    __tablename__ = "consent_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    consent_version: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    ip_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_consent_ledger_user_scope_at", "user_id", "scope", "at"),
    )


class Swipe(Base):
    __tablename__ = "swipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    wine_id: Mapped[str] = mapped_column(String, nullable=False)
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        CheckConstraint("verdict IN ('like','dislike','skip')", name="ck_swipes_verdict"),
        Index("ix_swipes_user_at", "user_id", "at"),
    )


class TasteProfile(Base):
    __tablename__ = "taste_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    vector: Mapped[dict] = mapped_column(JSON, nullable=False)
    top_styles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    swipes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    matched: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_chat_messages_role"),
        Index("ix_chat_messages_user_at", "user_id", "at"),
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    props: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_events_name_at", "name", "at"),
    )


class Waitlist(Base):
    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    consent_version: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        CheckConstraint("verdict IN ('up','down')", name="ck_feedback_verdict"),
    )
