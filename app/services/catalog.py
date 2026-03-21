"""Product catalog service for loading and searching products."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from app.models import Product


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

    def search(self, query: str, limit: int = 5) -> list[Product]:
        """
        Search products by name, description, category or tags.
        Uses normalized keyword matching (accent-insensitive, case-insensitive).
        Returns up to *limit* results ordered by relevance score.
        """
        q_norm = _normalize(query)
        keywords = q_norm.split()

        scored: list[tuple[int, Product]] = []
        for product in self._products:
            score = self._score(product, keywords)
            if score > 0:
                scored.append((score, product))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

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
        """Format a product for display in chat."""
        lines = [
            f"📦 *{product.name}*",
            f"   {product.description}",
            f"   💵 Precio: ${product.price:,.2f} por {product.unit}",
        ]
        if product.stock is not None:
            stock_str = "✅ En stock" if product.stock > 0 else "❌ Sin stock"
            lines.append(f"   {stock_str} ({product.stock} disponibles)")
        if product.promotions:
            lines.append("   🏷️ Promociones:")
            for promo in product.promotions:
                lines.append(f"      • {promo.description}")
        return "\n".join(lines)


catalog_service = CatalogService()
