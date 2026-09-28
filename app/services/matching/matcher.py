"""Product matching (SPEC 4.2).

Search order and scores:
  1. barcode of a product or unit           -> 100 (returned alone)
  2. exact alias (normalized)               -> 100
  3. exact product name (normalized)        -> 98
  4. query is part of a name/alias          -> 85-95 (longer share = higher)
  5. fuzzy: mean of token_set_ratio and partial_ratio, capped at FUZZY_CAP
     so a fuzzy guess can never be auto-accepted (owner decision).

Ties are broken by alias hit_count. The catalog index is cached in memory and
rebuilt after any commit that changed products, units or aliases
(services call `mark_catalog_changed`).
"""

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Product, ProductAlias, ProductUnit
from app.services.matching.normalize import normalize
from app.services.settings import get_shop_settings

FUZZY_CAP = 90
MAX_CHOICES = 5

STATUS_AUTO = "auto"
STATUS_NEEDS_CHOICE = "needs_choice"
STATUS_NOT_FOUND = "not_found"

REASON_LABELS = {
    "barcode": "บาร์โค้ด",
    "alias": "ชื่อเรียก",
    "name": "ชื่อสินค้า",
    "contains": "มีคำนี้ในชื่อ",
    "fuzzy": "ใกล้เคียง",
}


@dataclass(frozen=True)
class MatchConfig:
    auto_accept: int = 92
    suggest_min: int = 60
    min_gap: int = 8


@dataclass
class MatchCandidate:
    product_id: int
    name: str
    score: int
    reason: str
    unit_id: int | None = None
    matched_text: str = ""
    hit_count: int = 0

    @property
    def reason_label(self) -> str:
        return REASON_LABELS.get(self.reason, self.reason)


@dataclass
class MatchResult:
    query: str
    normalized: str
    status: str
    candidates: list[MatchCandidate] = field(default_factory=list)

    @property
    def best(self) -> MatchCandidate | None:
        return self.candidates[0] if self.candidates else None


@dataclass
class _Entry:
    product_id: int
    text: str
    kind: str  # "name" | "alias"
    hit_count: int


@dataclass
class CatalogIndex:
    entries: list[_Entry]
    names: dict[int, str]
    barcodes: dict[str, tuple[int, int | None]]

    @property
    def texts(self) -> list[str]:
        return [e.text for e in self.entries]


# --- cache ---------------------------------------------------------------------

_version = 0
_cache: dict[str, tuple[int, CatalogIndex]] = {}


def invalidate_index() -> None:
    """Force a rebuild on next use (after restoring a backup / go-live)."""
    global _version
    _version += 1


def mark_catalog_changed(db: Session) -> None:
    """Call after changing products/units/aliases; the index is rebuilt after commit."""
    db.info["catalog_changed"] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    global _version
    if session.info.pop("catalog_changed", False):
        _version += 1


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("catalog_changed", None)


def build_index(db: Session) -> CatalogIndex:
    entries: list[_Entry] = []
    names: dict[int, str] = {}
    barcodes: dict[str, tuple[int, int | None]] = {}

    for p in db.execute(
        select(Product.id, Product.name, Product.name_normalized, Product.barcode).where(
            Product.is_active.is_(True)
        )
    ):
        names[p.id] = p.name
        entries.append(_Entry(p.id, p.name_normalized, "name", 0))
        if p.barcode:
            barcodes[p.barcode] = (p.id, None)

    for u in db.execute(
        select(ProductUnit.id, ProductUnit.product_id, ProductUnit.barcode).where(
            ProductUnit.barcode.is_not(None)
        )
    ):
        if u.product_id in names:
            barcodes[u.barcode] = (u.product_id, u.id)

    for a in db.execute(
        select(ProductAlias.product_id, ProductAlias.alias_normalized, ProductAlias.hit_count)
    ):
        if a.product_id in names:
            entries.append(_Entry(a.product_id, a.alias_normalized, "alias", a.hit_count))

    return CatalogIndex(entries=entries, names=names, barcodes=barcodes)


def get_index(db: Session) -> CatalogIndex:
    key = str(db.get_bind().url)
    cached = _cache.get(key)
    if cached and cached[0] == _version:
        return cached[1]
    index = build_index(db)
    _cache[key] = (_version, index)
    return index


def get_config(db: Session) -> MatchConfig:
    s = get_shop_settings(db)
    return MatchConfig(s.match_auto_accept, s.match_suggest_min, s.match_min_gap)


# --- scoring -------------------------------------------------------------------


def _contains_score(query: str, target: str) -> int:
    return min(95, 85 + round(10 * len(query) / len(target)))


def rank(index: CatalogIndex, query: str, config: MatchConfig) -> list[MatchCandidate]:
    """All candidates scoring >= suggest_min, best first."""
    raw = query.strip()
    if raw in index.barcodes:
        product_id, unit_id = index.barcodes[raw]
        return [
            MatchCandidate(product_id, index.names[product_id], 100, "barcode", unit_id, raw)
        ]

    q = normalize(query)
    if not q:
        return []

    best: dict[int, MatchCandidate] = {}

    def offer(entry: _Entry, score: int, reason: str) -> None:
        current = best.get(entry.product_id)
        if current is None or (score, entry.hit_count) > (current.score, current.hit_count):
            best[entry.product_id] = MatchCandidate(
                entry.product_id, index.names[entry.product_id], score, reason,
                matched_text=entry.text, hit_count=entry.hit_count,
            )

    for entry in index.entries:
        if entry.text == q:
            offer(entry, 100 if entry.kind == "alias" else 98, entry.kind)
        elif len(q) >= 2 and q in entry.text:
            offer(entry, _contains_score(q, entry.text), "contains")

    # Fuzzy: a mean >= suggest_min needs each scorer >= 2*suggest_min - 100.
    cutoff = max(0, 2 * config.suggest_min - 100)
    texts = index.texts
    partial = {
        i: s for _, s, i in process.extract(q, texts, scorer=fuzz.partial_ratio, score_cutoff=cutoff, limit=None)
    }
    token_set = {
        i: s for _, s, i in process.extract(q, texts, scorer=fuzz.token_set_ratio, score_cutoff=cutoff, limit=None)
    }
    for i in partial.keys() & token_set.keys():
        score = min(FUZZY_CAP, round((partial[i] + token_set[i]) / 2))
        offer(index.entries[i], score, "fuzzy")

    ranked = [c for c in best.values() if c.score >= config.suggest_min]
    ranked.sort(key=lambda c: (-c.score, -c.hit_count, c.name))
    return ranked


def decide(query: str, candidates: list[MatchCandidate], config: MatchConfig) -> MatchResult:
    normalized = normalize(query)
    if not candidates:
        return MatchResult(query, normalized, STATUS_NOT_FOUND)
    top = candidates[0]
    second = candidates[1].score if len(candidates) > 1 else None
    auto = top.reason == "barcode" or (
        top.score >= config.auto_accept and (second is None or top.score - second >= config.min_gap)
    )
    status = STATUS_AUTO if auto else STATUS_NEEDS_CHOICE
    return MatchResult(query, normalized, status, candidates[:MAX_CHOICES])


def match(db: Session, query: str) -> MatchResult:
    config = get_config(db)
    return decide(query, rank(get_index(db), query, config), config)
