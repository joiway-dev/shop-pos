import pytest

from app.services.matching.normalize import canonical_unit, normalize

# The three spellings SPEC 4.1 requires to be identical.
TIGER = ["ปูนเสือ 50กก.", "ปูนเสือ 50 kg", "ปูน เสือ ๕๐ กก"]


@pytest.mark.parametrize("text", TIGER)
def test_spec_examples_are_identical(text):
    assert normalize(text) == "ปูนเสือ 50 กก"


@pytest.mark.parametrize(
    "text,expected",
    [
        # whitespace and case
        ("  ปูน   ตรา   เสือ  ", "ปูนตราเสือ"),
        ("TOA Shield", "toa shield"),
        ("PVC", "pvc"),
        # Thai digits
        ("๑๒๓", "123"),
        ("เหล็ก ๑๒ มม.", "เหล็ก 12 มม"),
        # kg spellings
        ("ปูนเสือ 50 กก", "ปูนเสือ 50 กก"),
        ("ปูนเสือ 50 กิโล", "ปูนเสือ 50 กก"),
        ("ปูนเสือ 50กิโลกรัม", "ปูนเสือ 50 กก"),
        ("ปูนเสือ 50KG", "ปูนเสือ 50 กก"),
        ("ปูนเสือ 50 kgs.", "ปูนเสือ 50 กก"),
        # mm spellings (มิล = มม, owner decision)
        ("เหล็ก 12 มิล", "เหล็ก 12 มม"),
        ("เหล็ก 12mm", "เหล็ก 12 มม"),
        ("เหล็ก 12 มิลลิเมตร", "เหล็ก 12 มม"),
        ("เหล็ก 12มม.", "เหล็ก 12 มม"),
        # metre spellings
        ("สายไฟ 2.5 เมตร", "สายไฟ 2.5 ม"),
        ("สายไฟ 2.5 m", "สายไฟ 2.5 ม"),
        ("สายไฟ 2.5ม.", "สายไฟ 2.5 ม"),
        # cm / inch / หุน
        ("ไม้ 10 ซม.", "ไม้ 10 ซม"),
        ("ไม้ 10cm", "ไม้ 10 ซม"),
        ('ท่อ 4"', "ท่อ 4 นิ้ว"),
        ("ท่อ 4 inch", "ท่อ 4 นิ้ว"),
        ("ท่อ 4 หุน", "ท่อ 4 หุน"),
        # decimals are kept, separators dropped
        ("สายไฟ 2x1.5", "สายไฟ 2 1.5"),
        ("ไม้ 2 × 4", "ไม้ 2 4"),
        ("ไม้ 2*4", "ไม้ 2 4"),
        ("ปูนเสือ x10", "ปูนเสือ 10"),
        ("1,000 แผ่น", "1000 แผ่น"),
        ("ปูน-เสือ", "ปูนเสือ"),
        ("ปูน, เสือ.", "ปูนเสือ"),
        ("ปูนเสือ(ถุงเล็ก)", "ปูนเสือถุงเล็ก"),
        # unit words are only unified after a number
        ("5 มัด", "5 มัด"),
        ("เมตรวัด", "เมตรวัด"),
        ("max box", "max box"),
        # invisible characters from LINE / Word
        ("ปูน​เสือ", "ปูนเสือ"),
        ("ทํา", "ทำ"),
        ("", ""),
        ("  ", ""),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected


def test_normalize_is_idempotent():
    for text in TIGER + ["เหล็ก 12 มิล 20 เส้น", "สายไฟ VAF 2x1.5"]:
        once = normalize(text)
        assert normalize(once) == once


@pytest.mark.parametrize(
    "word,expected",
    [("เมตร", "ม"), ("ม.", "ม"), ("M", "ม"), ("กิโล", "กก"), ("ถุง", "ถุง"), (" พาเลท ", "พาเลท")],
)
def test_canonical_unit(word, expected):
    assert canonical_unit(word) == expected
