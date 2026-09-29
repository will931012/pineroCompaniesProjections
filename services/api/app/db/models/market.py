from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PRICE = Numeric(20, 6)


class DailyPrice(Base):
    """End-of-day bar exactly as reported by one provider.

    Unadjusted OHLCV are the provider's original values; adjusted columns are the
    provider's own split/dividend adjustment. Nothing here is derived locally.
    """

    __tablename__ = "daily_prices"

    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[int] = mapped_column(BigInteger)
    adj_open: Mapped[Decimal | None] = mapped_column(PRICE)
    adj_high: Mapped[Decimal | None] = mapped_column(PRICE)
    adj_low: Mapped[Decimal | None] = mapped_column(PRICE)
    adj_close: Mapped[Decimal | None] = mapped_column(PRICE)
    adj_volume: Mapped[int | None] = mapped_column(BigInteger)
    dividend_cash: Mapped[Decimal | None] = mapped_column(PRICE)
    split_factor: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
