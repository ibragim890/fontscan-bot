from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    trial_requests_used: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped[str] = mapped_column(String(32), default="none")
    plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    plan_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    monthly_requests_used: Mapped[int] = mapped_column(Integer, default=0)
    monthly_requests_limit: Mapped[int] = mapped_column(Integer, default=0)

    subscription_payment_charge_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    subscription_payload: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_canceled: Mapped[bool] = mapped_column(Boolean, default=False)


class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payload: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tariff: Mapped[str] = mapped_column(String(32))
    amount_stars: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tariff: Mapped[str] = mapped_column(String(32))
    amount_stars: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(16))
    invoice_payload: Mapped[str] = mapped_column(String(255), index=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    provider_payment_charge_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    subscription_expiration_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_recurring: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_first_recurring: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FontRequest(Base):
    __tablename__ = "font_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    image_hash: Mapped[str] = mapped_column(String(64), index=True)
    top_font: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    counted_as_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cached_response: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApiKeyUsage(Base):
    __tablename__ = "api_key_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    key_index: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    requests_count: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
