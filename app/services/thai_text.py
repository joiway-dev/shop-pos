"""Amount in Thai words for printed documents: 125050 -> 'หนึ่งพันสองร้อยห้าสิบบาทห้าสิบสตางค์'."""

_DIGITS = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_PLACES = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _below_million(n: int) -> str:
    out = []
    digits = str(n)
    length = len(digits)
    for i, ch in enumerate(digits):
        d = int(ch)
        place = length - i - 1
        if d == 0:
            continue
        if place == 1 and d == 1:
            out.append("สิบ")
        elif place == 1 and d == 2:
            out.append("ยี่สิบ")
        elif place == 0 and d == 1 and length > 1:
            out.append("เอ็ด")
        else:
            out.append(_DIGITS[d] + _PLACES[place])
    return "".join(out)


def _number_text(n: int) -> str:
    if n == 0:
        return "ศูนย์"
    parts = []
    millions, rest = divmod(n, 1_000_000)
    if millions:
        parts.append(_number_text(millions) + "ล้าน")
    if rest:
        parts.append(_below_million(rest))
    return "".join(parts)


def baht_text(satang: int) -> str:
    if satang < 0:
        return "ลบ" + baht_text(-satang)
    baht, st = divmod(satang, 100)
    if st == 0:
        return _number_text(baht) + "บาทถ้วน"
    head = _number_text(baht) + "บาท" if baht else ""
    return head + _number_text(st) + "สตางค์"
