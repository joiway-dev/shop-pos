"""Daily cash close (ปิดยอดประจำวัน).

expected cash = opening float + cash sales + cash payments from debtors
Anyone may close (staff count the drawer); the latest close of a day counts.
Drawer expenses are not tracked yet: write them in the note (owner decision).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyClose, User
from app.services import reports
from app.services.audit import log_action
from app.services.money import AmountError, format_money, parse_money


class CloseError(ValueError):
    pass


def preview(db: Session, day: date, opening_cash: int = 0) -> dict:
    cash = reports.day_cash(db, day)
    expected = opening_cash + cash["cash_sales"] + cash["cash_receipts"]
    return {**cash, "opening_cash": opening_cash, "expected": expected}


def close_day(db: Session, actor: User, day: date, opening_text: str, counted_text: str, note: str = "") -> DailyClose:
    try:
        opening = parse_money(opening_text or "0", "เงินทอนตั้งต้น")
        counted = parse_money(counted_text, "เงินที่นับได้")
    except AmountError as e:
        raise CloseError(str(e)) from None
    p = preview(db, day, opening)
    row = DailyClose(
        business_date=day, opening_cash=opening, cash_sales=p["cash_sales"], cash_receipts=p["cash_receipts"],
        expected_cash=p["expected"], counted_cash=counted, difference=counted - p["expected"],
        transfer_total=p["transfer"], credit_total=p["credit"], bill_count=p["bills"],
        note=(note or "").strip()[:500], user_id=actor.id,
    )
    db.add(row)
    db.flush()
    log_action(db, actor.id, "daily_close", "daily_close", row.id, {
        "date": day.isoformat(), "expected": format_money(row.expected_cash),
        "counted": format_money(counted), "difference": format_money(row.difference),
    })
    return row


def closes_of(db: Session, day: date) -> list[DailyClose]:
    return list(db.scalars(
        select(DailyClose).where(DailyClose.business_date == day).order_by(DailyClose.id.desc())
    ))


def recent_closes(db: Session, limit: int = 30) -> list[DailyClose]:
    return list(db.scalars(select(DailyClose).order_by(DailyClose.id.desc()).limit(limit)))
