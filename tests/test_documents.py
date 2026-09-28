import threading
from datetime import datetime

import pytest

from app.db import begin_immediate, create_db_engine, create_session_factory
from app.models import ShopSetting
from app.services import documents as docs
from app.services.thai_text import baht_text

OCT = datetime(2026, 10, 5)
NOV = datetime(2026, 11, 1)


def test_doc_type_rules():
    s = ShopSetting(vat_mode="none", allow_abbreviated_invoice=False)
    assert docs.sale_doc_type(s, has_buyer=False) == docs.DOC_RC
    assert docs.sale_doc_type(s, has_buyer=True) == docs.DOC_RC
    s.vat_mode = "vat"
    assert docs.sale_doc_type(s, has_buyer=True) == docs.DOC_TAX
    with pytest.raises(docs.DocumentError):
        docs.sale_doc_type(s, has_buyer=False)
    s.allow_abbreviated_invoice = True
    assert docs.sale_doc_type(s, has_buyer=False) == docs.DOC_ABB


def test_numbers_are_sequential_per_type_and_month(db):
    assert docs.next_doc_no(db, "RC", OCT) == "RC2569-10-0001"
    assert docs.next_doc_no(db, "RC", OCT) == "RC2569-10-0002"
    assert docs.next_doc_no(db, "TAX", OCT) == "INV2569-10-0001"
    assert docs.next_doc_no(db, "RC", NOV) == "RC2569-11-0001"
    assert docs.next_doc_no(db, "RC", OCT) == "RC2569-10-0003"


def test_yearly_reset(db):
    assert docs.next_doc_no(db, "ABB", OCT, "yearly") == "ABB2569-0001"
    assert docs.next_doc_no(db, "ABB", NOV, "yearly") == "ABB2569-0002"


def test_rolled_back_number_is_reused(db):
    docs.next_doc_no(db, "RC", OCT)
    db.rollback()
    assert docs.next_doc_no(db, "RC", OCT) == "RC2569-10-0001"


def test_concurrent_numbering_has_no_duplicates_or_gaps(config, app):
    """Several tills issuing numbers at once must get unique consecutive numbers."""
    factory = create_session_factory(create_db_engine(config.db_url))
    results, errors = [], []

    def worker():
        try:
            for _ in range(10):
                with factory() as s:
                    begin_immediate(s)
                    results.append(docs.next_doc_no(s, "RC", OCT))
                    s.commit()
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results) == [f"RC2569-10-{n:04d}" for n in range(1, 41)]


@pytest.mark.parametrize("satang,text", [
    (0, "ศูนย์บาทถ้วน"),
    (100, "หนึ่งบาทถ้วน"),
    (1100, "สิบเอ็ดบาทถ้วน"),
    (2100, "ยี่สิบเอ็ดบาทถ้วน"),
    (10100, "หนึ่งร้อยเอ็ดบาทถ้วน"),
    (125050, "หนึ่งพันสองร้อยห้าสิบบาทห้าสิบสตางค์"),
    (25, "ยี่สิบห้าสตางค์"),
    (123456789, "หนึ่งล้านสองแสนสามหมื่นสี่พันห้าร้อยหกสิบเจ็ดบาทแปดสิบเก้าสตางค์"),
    (2000000000, "ยี่สิบล้านบาทถ้วน"),
    (10000000100, "หนึ่งร้อยล้านหนึ่งบาทถ้วน"),
])
def test_baht_text(satang, text):
    assert baht_text(satang) == text
