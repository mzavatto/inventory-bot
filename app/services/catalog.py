"""Product catalog service for loading and searching products."""
from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path

from app.models import Product

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"


def _normalize(text: str) -> str:
    """Lowercase and remove accents for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class CatalogService:
    """Manages the product catalog loaded from a JSON file."""

    def __init__(self, catalog_path: Path = _CATALOG_PATH) -> None:
        self._catalog_path = catalog_path
        self._products: list[Product] = []
        self._load()

    def _load(self) -> None:
        with open(self._catalog_path, encoding="utf-8") as f:
            data = json.load(f)
        self._products = [Product(**item) for item in data]

    def reload(self) -> None:
        """Reload the catalog from disk (called after a catalog import)."""
        self._load()
        logger.info("Catalog reloaded: %d products loaded", len(self._products))

    def get_all(self) -> list[Product]:
        """Return all products in the catalog."""
        return list(self._products)

    def get_by_id(self, product_id: str) -> Product | None:
        """Return a product by its ID, case-insensitive."""
        pid = product_id.upper()
        for p in self._products:
            if p.id.upper() == pid:
                return p
        return None

    def search(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[Product]:
        """Search products by name, description, category, tags or ID.

        Uses normalized keyword matching (accent-insensitive, case-insensitive).
        If ``query`` matches a product ID/SKU exactly, that product is
        returned first. Optional ``category`` filter restricts results to a
        single section (matched accent/case-insensitively).
        Returns up to *limit* results ordered by relevance score.
        """
        results: list[Product] = []

        exact = self.get_by_id(query.strip())
        if exact and (category is None or _normalize(exact.category) == _normalize(category)):
            results.append(exact)

        q_norm = _normalize(query)
        keywords = [k for k in q_norm.split() if k]
        cat_norm = _normalize(category) if category else None

        scored: list[tuple[int, Product]] = []
        for product in self._products:
            if exact is not None and product.id == exact.id:
                continue
            if cat_norm and _normalize(product.category) != cat_norm:
                continue
            score = self._score(product, keywords) if keywords else 1
            if score > 0:
                scored.append((score, product))

        scored.sort(key=lambda x: x[0], reverse=True)
        results.extend(p for _, p in scored)
        return results[:limit]

    def list_by_category(self, category: str, limit: int = 50) -> list[Product]:
        """Return products belonging to a category (accent/case-insensitive)."""
        cat_norm = _normalize(category)
        return [p for p in self._products if _normalize(p.category) == cat_norm][:limit]

    def _score(self, product: Product, keywords: list[str]) -> int:
        """Score a product against a list of keywords."""
        score = 0
        name_norm = _normalize(product.name)
        desc_norm = _normalize(product.description)
        cat_norm = _normalize(product.category)
        tags_norm = [_normalize(t) for t in product.tags]

        for kw in keywords:
            if kw in name_norm:
                score += 3
            if kw in desc_norm:
                score += 1
            if kw in cat_norm:
                score += 2
            if any(kw in tag for tag in tags_norm):
                score += 2

        return score

    def list_categories(self) -> list[str]:
        """Return unique product categories."""
        seen: set[str] = set()
        result: list[str] = []
        for p in self._products:
            if p.category and p.category not in seen:
                seen.add(p.category)
                result.append(p.category)
        return result

    def format_product(self, product: Product) -> str:
        """Detailed product card for the seller (includes SKU + section)."""
        lines = [f"📦 *{product.name}*"]
        meta_parts = [f"SKU `{product.id}`"]
        if product.category:
            meta_parts.append(product.category)
        if product.unit and product.unit != "unidad":
            meta_parts.append(product.unit)
        lines.append("   " + " · ".join(meta_parts))
        if product.description:
            lines.append(f"   {product.description}")
        lines.append(f"   💵 PSVP Lista: ${product.price:,.2f}")
        if product.price_installments_12:
            lines.append(
                f"   💳 12 cuotas: ${product.price_installments_12:,.2f}"
            )
        if product.promotions:
            lines.append("   🏷️ Promociones:")
            for promo in product.promotions:
                lines.append(f"      • {promo.description}")
        return "\n".join(lines)

    def format_product_short(self, product: Product) -> str:
        """One-line product summary for inline lists (search results, sections)."""
        installments = (
            f" / 12x ${product.price_installments_12:,.2f}"
            if product.price_installments_12
            else ""
        )
        return f"`{product.id}` · {product.name} — ${product.price:,.2f}{installments}"


catalog_service = CatalogService()
