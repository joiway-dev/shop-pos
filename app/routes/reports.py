"""Report pages. Each report builds one table spec used for both the web page
and the Excel export, so the two can never disagree."""

from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.routes.deps import DB, CostUser
from app.services import reports
from app.services.clock import now_local
from app.services.pricing import line_gross
from app.services.excel import MONEY, PCT, QTY, TEXT, Column, build_xlsx
from app.templating import templates

router = APIRouter(prefix="/reports")

TABS = [
    ("sales", "ยอดขายรายวัน/เดือน"),
    ("breakdown", "แยกสินค้า/หมวด/พนักงาน + กำไร"),
    ("top", "สินค้าขายดี"),
    ("idle", "สินค้าไม่เคลื่อนไหว"),
    ("tax", "สรุปภาษีขาย"),
]


def _dates(start: str, end: str) -> tuple[date, date]:
    today = now_local().date()
    try:
        s = date.fromisoformat(start) if start else today.replace(day=1)
        e = date.fromisoformat(end) if end else today
    except ValueError:
        s, e = today.replace(day=1), today
    if e < s:
        s, e = e, s
    return s, min(e, s + timedelta(days=3660))


def _thai(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


def _render(request: Request, user, kind: str, title: str, columns: list[Column], rows: list[list],
            total: list | None, start: date, end: date, fmt: str, extra: dict | None = None, note: str = ""):
    subtitle = f"{_thai(start)} – {_thai(end)}" if kind != "idle" else note
    if fmt == "xlsx":
        content = build_xlsx(title, subtitle, columns, rows, total)
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="report-{kind}-{start}.xlsx"'})
    return templates.TemplateResponse(request, "reports/report.html", {
        "user": user, "kind": kind, "tabs": TABS, "title": title, "subtitle": subtitle, "columns": columns,
        "rows": rows, "total": total, "start": start, "end": end, "extra": extra or {}, "note": note,
        "MONEY": MONEY, "QTY": QTY, "PCT": PCT,
    })


@router.get("")
def reports_home(_user: CostUser):
    return RedirectResponse("/reports/sales", status_code=303)


@router.get("/sales")
def report_sales(request: Request, db: DB, user: CostUser, start: str = "", end: str = "", group: str = "day",
                 format: str = ""):
    s, e = _dates(start, end)
    group = "month" if group == "month" else "day"
    data = reports.sales_by_period(db, s, e, group)
    cols = [Column("เดือน" if group == "month" else "วันที่", TEXT, 14), Column("จำนวนบิล", TEXT, 10),
            Column("ยอดขาย", MONEY), Column("ส่วนลด", MONEY), Column("VAT", MONEY), Column("ยอดก่อน VAT", MONEY),
            Column("เงินสด", MONEY), Column("โอน", MONEY), Column("เงินเชื่อ", MONEY), Column("บิลยกเลิก", TEXT, 10)]

    def row(r):
        return [r.label, r.bills, r.gross, r.discount, r.vat, r.net, r.cash, r.transfer, r.credit, r.voided]

    t = reports.total_row(data)
    title = "ยอดขายรายเดือน" if group == "month" else "ยอดขายรายวัน"
    return _render(request, user, "sales", title, cols, [row(r) for r in data], row(t) if data else None,
                   s, e, format, {"group": group})


@router.get("/breakdown")
def report_breakdown(request: Request, db: DB, user: CostUser, start: str = "", end: str = "", by: str = "product",
                     format: str = ""):
    s, e = _dates(start, end)
    by = by if by in ("product", "category", "cashier") else "product"
    data = reports.breakdown(db, s, e, by)
    first = {"product": "สินค้า", "category": "หมวดหมู่", "cashier": "พนักงาน"}[by]
    cols = [Column(first, TEXT, 36)]
    if by == "product":
        cols += [Column("จำนวน (หน่วยฐาน)", QTY), Column("หน่วย", TEXT, 8)]
    cols += [Column("จำนวนบิล", TEXT, 10), Column("ยอดขาย", MONEY), Column("ยอดก่อน VAT", MONEY),
             Column("ต้นทุน", MONEY), Column("กำไรขั้นต้น", MONEY), Column("% กำไร", PCT, 9)]

    def row(r):
        head = [r.label] + ([r.qty, r.unit] if by == "product" else [])
        return head + [r.bill_count, r.gross, r.net, r.cost, r.profit, r.margin_pct]

    t = reports.group_total(data)
    total = ([t.label] + (["", ""] if by == "product" else [])
             + [t.bill_count, t.gross, t.net, t.cost, t.profit, t.margin_pct]) if data else None
    return _render(request, user, "breakdown", f"ยอดขายและกำไรขั้นต้น แยกตาม{first}", cols,
                   [row(r) for r in data], total, s, e, format, {"by": by},
                   note="กำไรขั้นต้น = ยอดขายก่อน VAT − ต้นทุนเฉลี่ย ณ วันที่ขาย")


@router.get("/top")
def report_top(request: Request, db: DB, user: CostUser, start: str = "", end: str = "", by: str = "gross",
               limit: int = 20, format: str = ""):
    s, e = _dates(start, end)
    by = "qty" if by == "qty" else "gross"
    limit = max(5, min(limit, 200))
    data = reports.top_sellers(db, s, e, limit, by)
    cols = [Column("อันดับ", TEXT, 8), Column("สินค้า", TEXT, 36), Column("จำนวน (หน่วยฐาน)", QTY),
            Column("หน่วย", TEXT, 8), Column("ยอดขาย", MONEY), Column("กำไรขั้นต้น", MONEY)]
    rows = [[i, r.label, r.qty, r.unit, r.gross, r.profit] for i, r in enumerate(data, start=1)]
    return _render(request, user, "top", f"สินค้าขายดี {limit} อันดับ (เรียงตาม{'จำนวน' if by == 'qty' else 'ยอดขาย'})",
                   cols, rows, None, s, e, format, {"by": by, "limit": limit})


@router.get("/idle")
def report_idle(request: Request, db: DB, user: CostUser, days: int = 60, format: str = ""):
    days = max(7, min(days, 3650))
    today = now_local().date()
    data = reports.non_moving(db, days, today)
    cols = [Column("รหัส", TEXT, 10), Column("สินค้า", TEXT, 36), Column("คงเหลือ", QTY), Column("หน่วย", TEXT, 8),
            Column("มูลค่าคงเหลือ", MONEY), Column("ขายครั้งล่าสุด", TEXT, 14)]
    rows = [[r.product.sku, r.product.name, r.balance, r.product.base_unit,
             line_gross(max(r.balance, 0), r.product.avg_cost), _thai(r.last_sold) if r.last_sold else "ไม่เคยขาย"]
            for r in data]
    return _render(request, user, "idle", f"สินค้าไม่เคลื่อนไหว (ไม่มีการขาย {days} วัน)", cols, rows, None,
                   today, today, format, {"days": days}, note=f"ณ วันที่ {_thai(today)} · ไม่มีการขายภายใน {days} วัน")


@router.get("/tax")
def report_tax(request: Request, db: DB, user: CostUser, start: str = "", end: str = "", format: str = ""):
    s, e = _dates(start, end)
    data = reports.tax_report(db, s, e)
    cols = [Column("ลำดับ", TEXT, 7), Column("วันที่", TEXT, 12), Column("เลขที่ใบกำกับ", TEXT, 18),
            Column("ชื่อผู้ซื้อ", TEXT, 30), Column("เลขผู้เสียภาษีผู้ซื้อ", TEXT, 16), Column("สาขา", TEXT, 12),
            Column("มูลค่าสินค้า", MONEY), Column("ยกเว้นภาษี", MONEY), Column("ภาษีมูลค่าเพิ่ม", MONEY),
            Column("รวม", MONEY), Column("หมายเหตุ", TEXT, 10)]
    rows = [[i, _thai(r.issued), r.sale.doc_no, r.buyer_name, r.tax_id, r.branch, r.vatable, r.exempt, r.vat, r.total,
             "ยกเลิก" if r.voided else ""] for i, r in enumerate(data, start=1)]
    total = ["", "", "", "รวม", "", "", sum(r.vatable for r in data), sum(r.exempt for r in data),
             sum(r.vat for r in data), sum(r.total for r in data), ""] if data else None
    return _render(request, user, "tax", "รายงานภาษีขาย", cols, rows, total, s, e, format,
                   note="รูปแบบเบื้องต้นเพื่อส่งต่อให้นักบัญชี — ควรให้นักบัญชียืนยันก่อนใช้ยื่นจริง")
