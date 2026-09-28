"""Learning new aliases from staff choices (SPEC 4.3)."""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Product, ProductAlias
from app.models.catalog import ALIAS_SOURCE_LEARNED
from app.services import catalog
from app.services.matching.normalize import normalize

LEARN_SAVED = "saved"
LEARN_ALREADY = "already"  # same product already knows this text
LEARN_CONFLICT = "conflict"  # another product owns this alias -> owner decides
LEARN_SKIPPED = "skipped"  # nothing worth learning


@dataclass
class LearnResult:
    status: str
    message: str


def learn_alias(db: Session, actor_id: int, text: str, product: Product) -> LearnResult:
    """Remember `text` as a learned alias of `product` after a staff choice.

    Never creates an alias that points at a different product than an existing
    one; in that case the owner has to sort it out on the aliases page.
    """
    alias_normalized = normalize(text)
    if not alias_normalized or alias_normalized == product.name_normalized:
        return LearnResult(LEARN_SKIPPED, "")
    if len(alias_normalized) < 2:
        return LearnResult(LEARN_SKIPPED, "คำสั้นเกินไป ไม่ได้จำไว้")

    others = catalog.alias_owners(db, alias_normalized, exclude_product_id=product.id)
    if others:
        names = ", ".join(p.name for p in others)
        return LearnResult(
            LEARN_CONFLICT,
            f"ไม่ได้จำคำนี้ เพราะ \"{text.strip()}\" เป็นชื่อเรียกของ {names} อยู่แล้ว "
            "— ให้เจ้าของร้านแก้ในหน้าชื่อเรียก",
        )
    if db.scalar(
        select(ProductAlias.id).where(
            ProductAlias.product_id == product.id, ProductAlias.alias_normalized == alias_normalized
        )
    ):
        record_alias_hit(db, product.id, alias_normalized)
        return LearnResult(LEARN_ALREADY, "")

    catalog.add_alias(db, actor_id, product, text, source=ALIAS_SOURCE_LEARNED)
    return LearnResult(LEARN_SAVED, f"จำไว้แล้ว: \"{text.strip()}\" = {product.name}")


def record_alias_hit(db: Session, product_id: int, alias_normalized: str) -> None:
    """Count a use of an alias (tie-breaker in matching). Does not rebuild the index."""
    db.execute(
        update(ProductAlias)
        .where(ProductAlias.product_id == product_id, ProductAlias.alias_normalized == alias_normalized)
        .values(hit_count=ProductAlias.hit_count + 1)
    )
