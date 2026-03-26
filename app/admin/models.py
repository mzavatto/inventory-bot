"""Data models for catalog ingestion."""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ImportStatus(str, Enum):
    """Status of a catalog import."""

    PENDING = "pending"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemType(str, Enum):
    """Type of catalog item."""

    PRODUCT = "product"
    COMBO = "combo"
    BUNDLE = "bundle"
    REPLACEMENT_PART = "replacement_part"
    PROMO_BUNDLE = "promo_bundle"


class CatalogMetadata(BaseModel):
    """Metadata extracted from a catalog document."""

    catalog_name: str = ""
    cycle: str = ""
    edition: str = ""
    updated_date: str = ""
    source_file_name: str = ""
    source_file_hash: str = ""
    parser_version: str = "1.0.0"
    ingestion_timestamp: datetime | None = None


class CatalogSection(BaseModel):
    """A section/category within the catalog."""

    id: str = ""
    name: str
    display_name: str = ""
    page_start: int | None = None
    page_end: int | None = None
    parent_section_id: str | None = None


class CatalogItemSKU(BaseModel):
    """SKU information for a catalog item."""

    sku: str
    variant_name: str = ""
    color: str = ""
    line: str = ""
    finish: str = ""
    material: str = ""
    is_composite: bool = False
    composite_skus: list[str] = Field(default_factory=list)


class CatalogItemComponent(BaseModel):
    """Component of a bundle/combo item."""

    component_sku: str = ""
    component_name: str
    quantity: int = 1
    order: int = 0


class CatalogPrice(BaseModel):
    """Price information for a catalog item."""

    sku: str | None = None
    item_fingerprint: str | None = None
    installments_18: float | None = None
    installments_15: float | None = None
    installments_12: float | None = None
    installments_10: float | None = None
    psvp_lista: float | None = None
    psvp_negocio: float | None = None
    precio_preferencial: float | None = None
    puntos_essen_plus: int | None = None
    puntos: int | None = None
    puntos_xl: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    currency: str = "ARS"


class CatalogPromotion(BaseModel):
    """Promotion information from the catalog."""

    promotion_id: str = ""
    description: str
    bank_name: str = ""
    installment_conditions: str = ""
    discount_percent: float | None = None
    discount_amount: float | None = None
    validity_start: datetime | None = None
    validity_end: datetime | None = None
    global_discount_notes: str = ""
    exchange_plan_value: float | None = None
    applicable_skus: list[str] = Field(default_factory=list)


class CatalogItem(BaseModel):
    """A product, bundle, or replacement part in the catalog."""

    id: str = ""
    item_type: ItemType = ItemType.PRODUCT
    name: str
    display_name: str = ""
    description: str = ""
    section_id: str = ""
    section_name: str = ""
    line: str = ""
    material: str = ""
    color: str = ""
    dimensions: str = ""
    size_cm: str = ""
    capacity_liters: float | None = None
    shape: str = ""
    page_number: int | None = None
    raw_extracted_text: str = ""
    extraction_confidence: float | None = None

    skus: list[CatalogItemSKU] = Field(default_factory=list)
    components: list[CatalogItemComponent] = Field(default_factory=list)
    prices: list[CatalogPrice] = Field(default_factory=list)

    fingerprint: str = ""

    def compute_fingerprint(self) -> str:
        """Compute a deterministic fingerprint for matching items without SKU."""
        normalized_name = self.name.lower().strip()
        normalized_line = self.line.lower().strip()
        normalized_dims = self.dimensions.lower().strip()
        capacity_str = str(self.capacity_liters or "")
        section_str = self.section_name.lower().strip()

        data = f"{normalized_name}|{normalized_line}|{normalized_dims}|{capacity_str}|{section_str}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def model_post_init(self, __context: Any) -> None:
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()
        if not self.display_name:
            self.display_name = self.name


class CatalogImportSummary(BaseModel):
    """Summary of a catalog import operation."""

    total_items_detected: int = 0
    new_items_count: int = 0
    updated_items_count: int = 0
    deleted_items_count: int = 0
    changed_prices_count: int = 0
    warnings_count: int = 0
    errors_count: int = 0
    sections_detected: int = 0
    promotions_detected: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CatalogImport(BaseModel):
    """Record of a catalog import operation."""

    id: str = ""
    catalog_id: str | None = None
    source_file_name: str
    source_file_path: str = ""
    source_file_hash: str = ""
    file_size_bytes: int = 0
    uploaded_by: str = ""
    uploaded_at: datetime | None = None
    import_status: ImportStatus = ImportStatus.PENDING
    parser_version: str = "1.0.0"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: CatalogImportSummary = Field(default_factory=CatalogImportSummary)
    raw_log: list[str] = Field(default_factory=list)

    # Extracted data
    metadata: CatalogMetadata | None = None
    sections: list[CatalogSection] = Field(default_factory=list)
    items: list[CatalogItem] = Field(default_factory=list)
    promotions: list[CatalogPromotion] = Field(default_factory=list)

    def add_log(self, message: str) -> None:
        """Add a log message with timestamp."""
        timestamp = datetime.now().isoformat()
        self.raw_log.append(f"[{timestamp}] {message}")


class CatalogImportRequest(BaseModel):
    """Request to start a catalog import."""

    import_id: str
    dry_run: bool = False


class CatalogImportResponse(BaseModel):
    """Response from a catalog import operation."""

    import_id: str
    status: ImportStatus
    message: str
    summary: CatalogImportSummary | None = None


class ImportHistoryItem(BaseModel):
    """Summary item for import history list."""

    id: str
    source_file_name: str
    uploaded_by: str
    uploaded_at: datetime | None
    import_status: ImportStatus
    total_items_detected: int
    new_items_count: int
    updated_items_count: int
    errors_count: int
