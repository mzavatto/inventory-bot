"""Tests for the product catalog service."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from app.services.catalog import CatalogService, _normalize
from app.models import Product, Promotion


@pytest.fixture
def sample_catalog(tmp_path: Path) -> Path:
    """Create a temporary catalog JSON file for testing."""
    catalog_data = [
        {
            "id": "T001",
            "name": "Yerba Mate Test 500g",
            "description": "Yerba mate de prueba, corte fino.",
            "price": 850.0,
            "unit": "bolsa",
            "category": "Yerba Mate",
            "stock": 100,
            "promotions": [
                {
                    "description": "10% descuento por 10 unidades",
                    "discount_percent": 10,
                    "min_quantity": 10,
                    "conditions": "Mínimo 10 unidades",
                }
            ],
            "tags": ["yerba", "mate", "500g"],
        },
        {
            "id": "T002",
            "name": "Café Molido Test 250g",
            "description": "Café de prueba molido.",
            "price": 1200.0,
            "unit": "paquete",
            "category": "Café",
            "stock": 50,
            "promotions": [],
            "tags": ["café", "molido"],
        },
        {
            "id": "T003",
            "name": "Azúcar Refinada Test 1kg",
            "description": "Azúcar blanca refinada.",
            "price": 450.0,
            "unit": "bolsa",
            "category": "Almacén",
            "stock": 0,
            "promotions": [],
            "tags": ["azúcar", "azucar"],
        },
    ]
    path = tmp_path / "test_catalog.json"
    path.write_text(json.dumps(catalog_data), encoding="utf-8")
    return path


@pytest.fixture
def catalog(sample_catalog: Path) -> CatalogService:
    return CatalogService(catalog_path=sample_catalog)


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("YERBA") == "yerba"

    def test_removes_accents(self) -> None:
        assert _normalize("café") == "cafe"
        assert _normalize("azúcar") == "azucar"

    def test_combined(self) -> None:
        assert _normalize("Café Molído") == "cafe molido"


class TestCatalogService:
    def test_get_all_returns_all_products(self, catalog: CatalogService) -> None:
        products = catalog.get_all()
        assert len(products) == 3

    def test_get_by_id_found(self, catalog: CatalogService) -> None:
        product = catalog.get_by_id("T001")
        assert product is not None
        assert product.name == "Yerba Mate Test 500g"

    def test_get_by_id_case_insensitive(self, catalog: CatalogService) -> None:
        assert catalog.get_by_id("t001") is not None
        assert catalog.get_by_id("T001") is not None

    def test_get_by_id_not_found(self, catalog: CatalogService) -> None:
        assert catalog.get_by_id("XXXX") is None

    def test_search_by_name(self, catalog: CatalogService) -> None:
        results = catalog.search("yerba")
        assert len(results) >= 1
        assert results[0].id == "T001"

    def test_search_by_tag_accent_insensitive(self, catalog: CatalogService) -> None:
        results = catalog.search("azucar")
        assert any(p.id == "T003" for p in results)

    def test_search_by_category(self, catalog: CatalogService) -> None:
        results = catalog.search("café")
        assert any(p.id == "T002" for p in results)

    def test_search_no_results(self, catalog: CatalogService) -> None:
        results = catalog.search("producto_inexistente_xyz")
        assert results == []

    def test_search_limit(self, catalog: CatalogService) -> None:
        results = catalog.search("test", limit=2)
        assert len(results) <= 2

    def test_list_categories(self, catalog: CatalogService) -> None:
        cats = catalog.list_categories()
        assert "Yerba Mate" in cats
        assert "Café" in cats
        assert "Almacén" in cats

    def test_format_product_includes_price(self, catalog: CatalogService) -> None:
        product = catalog.get_by_id("T001")
        assert product is not None
        formatted = catalog.format_product(product)
        assert "850" in formatted
        assert "Yerba Mate Test 500g" in formatted

    def test_format_product_shows_out_of_stock(self, catalog: CatalogService) -> None:
        product = catalog.get_by_id("T003")
        assert product is not None
        formatted = catalog.format_product(product)
        assert "Sin stock" in formatted

    def test_format_product_shows_promotions(self, catalog: CatalogService) -> None:
        product = catalog.get_by_id("T001")
        assert product is not None
        formatted = catalog.format_product(product)
        assert "Promociones" in formatted or "descuento" in formatted.lower()
