"""Catalog API endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models import Product
from app.services.catalog import catalog_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=list[Product])
async def list_products() -> list[Product]:
    """List all products in the catalog."""
    return catalog_service.get_all()


@router.get("/search", response_model=list[Product])
async def search_products(
    q: str = Query(..., description="Search query"),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[Product]:
    """Search products by query."""
    return catalog_service.search(q, limit=limit)


@router.get("/categories", response_model=list[str])
async def list_categories() -> list[str]:
    """List all product categories."""
    return catalog_service.list_categories()


@router.get("/{product_id}", response_model=Product)
async def get_product(product_id: str) -> Product:
    """Get a product by ID."""
    product = catalog_service.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=404, detail=f"Producto '{product_id}' no encontrado."
        )
    return product
