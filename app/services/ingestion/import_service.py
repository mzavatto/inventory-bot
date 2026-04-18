"""Catalog import service for managing catalog file uploads and processing."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.admin.models import (
    CatalogImport,
    CatalogItem,
    ImportHistoryItem,
    ImportStatus,
)
from app.config import settings
from app.services.ingestion.parser import PARSER_VERSION, ParseResult, get_parser

logger = logging.getLogger(__name__)

# Path to the catalog JSON file
_CATALOG_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "catalog.json"


def _catalog_item_to_product_dict(item: CatalogItem, idx: int) -> dict:
    """Convert a CatalogItem to a product dictionary for catalog.json.
    
    Args:
        item: The CatalogItem to convert.
        idx: Index for generating a unique ID.
    
    Returns:
        A dictionary in the format used by catalog.json.
    """
    # Get the primary SKU or generate an ID
    if item.skus:
        product_id = item.skus[0].sku
    else:
        product_id = f"P{idx:03d}"
    
    # Get price from the item's prices. The PSVP LISTA column is the
    # canonical retail price; the 12-cuotas value is also stored separately
    # so the chatbot can mention installment options.
    price = 0.0
    price_installments_12: float | None = None
    if item.prices:
        price_info = item.prices[0]
        if price_info.psvp_lista:
            price = price_info.psvp_lista
        elif price_info.precio_preferencial:
            price = price_info.precio_preferencial
        elif price_info.psvp_negocio:
            price = price_info.psvp_negocio
        if price_info.installments_12:
            price_installments_12 = price_info.installments_12
    
    # Build description
    description_parts = []
    if item.section_name:
        description_parts.append(f"Sección: {item.section_name}")
    if item.size_cm:
        description_parts.append(f"Tamaño: {item.size_cm}")
    if item.dimensions:
        description_parts.append(f"Dimensiones: {item.dimensions}")
    if item.capacity_liters:
        description_parts.append(f"Capacidad: {item.capacity_liters}L")
    
    description = ". ".join(description_parts) if description_parts else item.name
    
    # Build tags from name and section
    tags = []
    name_words = item.name.lower().split()
    for word in name_words:
        if len(word) > 2 and word not in tags:
            tags.append(word)
    if item.section_name:
        section_words = item.section_name.lower().split()
        for word in section_words:
            if len(word) > 2 and word not in tags:
                tags.append(word)
    
    # Build promotions list
    promotions = []
    if item.prices:
        price_info = item.prices[0]
        # Add installment info as promotion
        installments = []
        if price_info.installments_12:
            installments.append(f"12 cuotas de ${price_info.installments_12:,.2f}")
        if price_info.installments_15:
            installments.append(f"15 cuotas de ${price_info.installments_15:,.2f}")
        if price_info.installments_10:
            installments.append(f"10 cuotas de ${price_info.installments_10:,.2f}")
        
        if installments:
            promotions.append({
                "description": "Cuotas sin interés disponibles: " + ", ".join(installments),
                "conditions": "Consultar cuotas disponibles"
            })
        
        # Add points info if available
        if price_info.puntos or price_info.puntos_essen_plus:
            points_desc = []
            if price_info.puntos_essen_plus:
                points_desc.append(f"Puntos Essen+: {price_info.puntos_essen_plus}")
            if price_info.puntos:
                points_desc.append(f"Puntos: {price_info.puntos}")
            promotions.append({
                "description": " | ".join(points_desc),
                "conditions": "Programa de puntos"
            })
    
    return {
        "id": product_id,
        "name": item.name,
        "description": description,
        "price": price,
        "price_installments_12": price_installments_12,
        "unit": "combo" if item.item_type.value == "combo" else "unidad",
        "category": item.section_name or "General",
        "stock": None,
        "promotions": promotions,
        "tags": tags
    }


def _save_items_to_catalog_json(items: list[CatalogItem]) -> None:
    """Save catalog items to the catalog.json file, replacing existing content.
    
    Uses atomic file replacement to prevent data loss: writes to a temporary
    file first, then renames it to the target path.
    
    Args:
        items: List of CatalogItem objects to save.
    """
    import tempfile
    import os
    
    products = []
    for idx, item in enumerate(items, start=1):
        product_dict = _catalog_item_to_product_dict(item, idx)
        products.append(product_dict)
    
    # Create a backup of the existing file if it exists
    backup_path = None
    if _CATALOG_JSON_PATH.exists():
        backup_path = _CATALOG_JSON_PATH.with_suffix(".json.bak")
        try:
            shutil.copy2(_CATALOG_JSON_PATH, backup_path)
            logger.debug("Created backup at %s", backup_path)
        except Exception as backup_exc:
            logger.warning("Failed to create backup: %s", backup_exc)
            backup_path = None
    
    # Write to a temporary file first, then atomically rename
    temp_fd = None
    temp_path = None
    try:
        # Create temp file in the same directory for atomic rename
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".json.tmp",
            dir=_CATALOG_JSON_PATH.parent
        )
        
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        temp_fd = None  # Closed by os.fdopen
        
        # Atomic rename
        os.replace(temp_path, _CATALOG_JSON_PATH)
        temp_path = None  # Successfully renamed
        
        logger.info("Saved %d products to catalog.json", len(products))
        
        # Remove backup on success
        if backup_path and backup_path.exists():
            try:
                backup_path.unlink()
            except Exception:
                pass  # Not critical
                
    except Exception as exc:
        logger.error("Failed to save catalog.json: %s", exc)
        
        # Restore from backup if available
        if backup_path and backup_path.exists():
            try:
                shutil.copy2(backup_path, _CATALOG_JSON_PATH)
                logger.info("Restored catalog.json from backup")
            except Exception as restore_exc:
                logger.error("Failed to restore from backup: %s", restore_exc)
        
        raise
    finally:
        # Clean up temp file if it still exists
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except Exception:
                pass
        if temp_path and Path(temp_path).exists():
            try:
                Path(temp_path).unlink()
            except Exception:
                pass


class FileValidationError(Exception):
    """Raised when file validation fails."""

    pass


class ImportNotFoundError(Exception):
    """Raised when an import record is not found."""

    pass


class CatalogImportService:
    """Service for managing catalog imports."""

    def __init__(self) -> None:
        self._imports: dict[str, CatalogImport] = {}
        self._items_by_sku: dict[str, CatalogItem] = {}
        self._items_by_fingerprint: dict[str, CatalogItem] = {}
        self._processing_tasks: dict[str, asyncio.Task[None]] = {}

    def validate_file(
        self,
        filename: str,
        file_size: int,
        content_type: str | None,
    ) -> None:
        """Validate an uploaded file.

        Args:
            filename: Original filename.
            file_size: File size in bytes.
            content_type: MIME content type.

        Raises:
            FileValidationError: If validation fails.
        """
        # Check extension
        file_ext = Path(filename).suffix.lower()
        allowed_extensions = settings.catalog_allowed_extensions_list

        if file_ext not in allowed_extensions:
            raise FileValidationError(
                f"Invalid file type: '{file_ext}'. "
                f"Allowed types: {', '.join(allowed_extensions)}"
            )

        # Check file size
        if file_size <= 0:
            raise FileValidationError("File is empty")

        max_size = settings.catalog_max_file_size_bytes
        if file_size > max_size:
            max_mb = settings.catalog_max_file_size_mb
            raise FileValidationError(
                f"File too large: {file_size / 1024 / 1024:.1f}MB. "
                f"Maximum allowed: {max_mb}MB"
            )

        # Check MIME type for PDF
        if file_ext == ".pdf" and content_type:
            valid_pdf_types = {"application/pdf", "application/x-pdf"}
            if content_type not in valid_pdf_types:
                logger.warning(
                    "File has .pdf extension but content-type is '%s'", content_type
                )
                # Don't fail, just warn - the extension check is more reliable

    def compute_file_hash(self, file: BinaryIO) -> str:
        """Compute SHA-256 hash of a file.

        Args:
            file: File object to hash.

        Returns:
            Hex digest of the file hash.
        """
        sha256 = hashlib.sha256()
        while True:
            data = file.read(65536)  # Read in 64KB chunks
            if not data:
                break
            sha256.update(data)
        file.seek(0)  # Reset file position
        return sha256.hexdigest()

    def check_duplicate(self, file_hash: str) -> CatalogImport | None:
        """Check if a file with the same hash has already been imported.

        Args:
            file_hash: SHA-256 hash of the file.

        Returns:
            Previous import record if found, None otherwise.
        """
        for import_record in self._imports.values():
            if import_record.source_file_hash == file_hash:
                return import_record
        return None

    async def upload_file(
        self,
        file: BinaryIO,
        filename: str,
        file_size: int,
        content_type: str | None,
        uploaded_by: str,
    ) -> CatalogImport:
        """Upload and store a catalog file.

        Args:
            file: File object to upload.
            filename: Original filename.
            file_size: File size in bytes.
            content_type: MIME content type.
            uploaded_by: Username of uploader.

        Returns:
            CatalogImport record for the upload.

        Raises:
            FileValidationError: If validation fails.
        """
        # Validate file
        self.validate_file(filename, file_size, content_type)

        # Compute hash
        file_hash = self.compute_file_hash(file)

        # Check for duplicate
        existing = self.check_duplicate(file_hash)
        if existing:
            logger.info("Duplicate file detected: %s", filename)
            # Still create a new import record pointing to existing file
            # This allows re-processing if needed

        # Generate unique ID and storage path
        import_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_filename = self._sanitize_filename(filename)
        storage_filename = f"{timestamp}_{import_id[:8]}_{safe_filename}"
        storage_path = settings.catalog_upload_path / storage_filename

        # Save file
        with open(storage_path, "wb") as dest:
            shutil.copyfileobj(file, dest)

        # Create import record
        catalog_import = CatalogImport(
            id=import_id,
            source_file_name=filename,
            source_file_path=str(storage_path),
            source_file_hash=file_hash,
            file_size_bytes=file_size,
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(timezone.utc),
            import_status=ImportStatus.PENDING,
            parser_version=PARSER_VERSION,
        )

        self._imports[import_id] = catalog_import
        catalog_import.add_log(f"File uploaded by {uploaded_by}")

        return catalog_import

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename for safe storage."""
        # Keep only alphanumeric, dash, underscore, and dot
        import re

        name = re.sub(r"[^\w\-.]", "_", filename)
        # Limit length
        if len(name) > 100:
            ext = Path(name).suffix
            name = name[: 100 - len(ext)] + ext
        return name

    def get_import(self, import_id: str) -> CatalogImport:
        """Get an import record by ID.

        Args:
            import_id: Import ID.

        Returns:
            CatalogImport record.

        Raises:
            ImportNotFoundError: If import not found.
        """
        if import_id not in self._imports:
            raise ImportNotFoundError(f"Import not found: {import_id}")
        return self._imports[import_id]

    def list_imports(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImportHistoryItem]:
        """List import history.

        Args:
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            List of import history items.
        """
        # Sort by upload time, newest first
        sorted_imports = sorted(
            self._imports.values(),
            key=lambda x: x.uploaded_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Apply pagination
        paginated = sorted_imports[offset : offset + limit]

        # Convert to history items
        return [
            ImportHistoryItem(
                id=imp.id,
                source_file_name=imp.source_file_name,
                uploaded_by=imp.uploaded_by,
                uploaded_at=imp.uploaded_at,
                import_status=imp.import_status,
                total_items_detected=imp.summary.total_items_detected,
                new_items_count=imp.summary.new_items_count,
                updated_items_count=imp.summary.updated_items_count,
                errors_count=imp.summary.errors_count,
            )
            for imp in paginated
        ]

    async def start_processing(
        self,
        import_id: str,
        dry_run: bool = False,
    ) -> CatalogImport:
        """Start processing an uploaded catalog file.

        Args:
            import_id: Import ID to process.
            dry_run: If True, parse but don't apply changes.

        Returns:
            Updated CatalogImport record.

        Raises:
            ImportNotFoundError: If import not found.
        """
        catalog_import = self.get_import(import_id)

        if catalog_import.import_status not in (
            ImportStatus.PENDING,
            ImportStatus.FAILED,
        ):
            catalog_import.add_log(
                f"Cannot process: status is {catalog_import.import_status}"
            )
            return catalog_import

        # Start async processing
        task = asyncio.create_task(
            self._process_import(import_id, dry_run),
            name=f"process_import_{import_id}",
        )
        self._processing_tasks[import_id] = task

        return catalog_import

    async def _process_import(
        self,
        import_id: str,
        dry_run: bool,
    ) -> None:
        """Process a catalog import in the background.

        Args:
            import_id: Import ID to process.
            dry_run: If True, parse but don't apply changes.
        """
        catalog_import = self._imports[import_id]
        catalog_import.import_status = ImportStatus.PROCESSING
        catalog_import.started_at = datetime.now(timezone.utc)
        catalog_import.add_log("Processing started")

        try:
            # Parse the PDF
            catalog_import.add_log("Parsing PDF...")
            parser = get_parser()
            result = parser.parse(
                catalog_import.source_file_path,
                catalog_import.source_file_name,
            )

            if not result.success:
                catalog_import.import_status = ImportStatus.FAILED
                catalog_import.add_log(f"Parsing failed: {result.error_message}")
                catalog_import.summary.errors.extend(result.errors)
                catalog_import.summary.errors_count = len(result.errors)
                return

            # Store extracted data
            catalog_import.metadata = result.metadata
            catalog_import.sections = result.sections
            catalog_import.items = result.items
            catalog_import.promotions = result.promotions

            # Build summary
            catalog_import.summary.total_items_detected = len(result.items)
            catalog_import.summary.sections_detected = len(result.sections)
            catalog_import.summary.promotions_detected = len(result.promotions)
            catalog_import.summary.warnings = result.warnings
            catalog_import.summary.warnings_count = len(result.warnings)
            catalog_import.summary.errors = result.errors
            catalog_import.summary.errors_count = len(result.errors)

            catalog_import.add_log(
                f"Parsed {len(result.items)} items, "
                f"{len(result.sections)} sections, "
                f"{len(result.promotions)} promotions"
            )

            if not dry_run:
                # Apply changes to catalog
                self._apply_changes(catalog_import, result)
            else:
                catalog_import.add_log("Dry run: changes not applied")

            catalog_import.import_status = ImportStatus.COMPLETED
            catalog_import.add_log("Processing completed successfully")

        except Exception as exc:
            logger.exception("Error processing import %s: %s", import_id, exc)
            catalog_import.import_status = ImportStatus.FAILED
            catalog_import.add_log(f"Processing failed: {exc}")
            catalog_import.summary.errors.append(str(exc))
            catalog_import.summary.errors_count += 1

        finally:
            catalog_import.finished_at = datetime.now(timezone.utc)
            # Clean up task reference
            self._processing_tasks.pop(import_id, None)

    def _apply_changes(
        self,
        catalog_import: CatalogImport,
        parse_result: ParseResult,
    ) -> None:
        """Apply parsed changes to the catalog database.

        Args:
            catalog_import: Import record to update.
            parse_result: Parsed catalog data.
        """
        new_count = 0
        updated_count = 0
        price_changes = 0

        for item in parse_result.items:
            # Try to match by SKU first
            matched = False
            for sku_info in item.skus:
                if sku_info.sku in self._items_by_sku:
                    existing = self._items_by_sku[sku_info.sku]
                    self._update_item(existing, item)
                    updated_count += 1
                    matched = True
                    break

            if not matched:
                # Try to match by fingerprint
                if item.fingerprint in self._items_by_fingerprint:
                    existing = self._items_by_fingerprint[item.fingerprint]
                    self._update_item(existing, item)
                    updated_count += 1
                    matched = True

            if not matched:
                # New item
                item.id = str(uuid.uuid4())
                for sku_info in item.skus:
                    self._items_by_sku[sku_info.sku] = item
                self._items_by_fingerprint[item.fingerprint] = item
                new_count += 1

            # Count price changes
            if item.prices:
                price_changes += len(item.prices)

        catalog_import.summary.new_items_count = new_count
        catalog_import.summary.updated_items_count = updated_count
        catalog_import.summary.changed_prices_count = price_changes

        # Save items to catalog.json and reload the in-memory catalog service
        if parse_result.items:
            try:
                _save_items_to_catalog_json(parse_result.items)
                catalog_import.add_log(
                    f"Saved {len(parse_result.items)} products to catalog.json"
                )
                # Reload the catalog service so the chat bot uses the new data
                from app.services.catalog import catalog_service
                catalog_service.reload()
                catalog_import.add_log("Catalog service reloaded with new products")
            except Exception as save_exc:
                logger.exception("Failed to save to catalog.json: %s", save_exc)
                catalog_import.add_log(f"Warning: Failed to save to catalog.json: {save_exc}")
                catalog_import.summary.warnings.append(f"Failed to save to catalog.json: {save_exc}")
                catalog_import.summary.warnings_count += 1

        catalog_import.add_log(
            f"Applied changes: {new_count} new, {updated_count} updated, "
            f"{price_changes} price entries"
        )

    def _update_item(self, existing: CatalogItem, new: CatalogItem) -> None:
        """Update an existing item with new data.

        Args:
            existing: Existing catalog item.
            new: New data to merge.
        """
        # Update basic fields
        existing.name = new.name
        existing.display_name = new.display_name
        existing.description = new.description or existing.description
        existing.section_name = new.section_name
        existing.page_number = new.page_number
        existing.raw_extracted_text = new.raw_extracted_text
        existing.extraction_confidence = new.extraction_confidence

        # Merge SKUs
        existing_skus = {s.sku for s in existing.skus}
        for sku in new.skus:
            if sku.sku not in existing_skus:
                existing.skus.append(sku)
                self._items_by_sku[sku.sku] = existing

        # Add new prices (version instead of overwrite)
        for price in new.prices:
            price.valid_from = datetime.now(timezone.utc)
            existing.prices.append(price)

    async def cancel_processing(self, import_id: str) -> bool:
        """Cancel a running import process.

        Args:
            import_id: Import ID to cancel.

        Returns:
            True if cancelled, False if not running.
        """
        task = self._processing_tasks.get(import_id)
        if task and not task.done():
            task.cancel()
            catalog_import = self._imports.get(import_id)
            if catalog_import:
                catalog_import.import_status = ImportStatus.CANCELLED
                catalog_import.add_log("Processing cancelled by user")
            return True
        return False

    def delete_import(self, import_id: str) -> bool:
        """Delete an import record and its associated file.

        Args:
            import_id: Import ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        if import_id not in self._imports:
            return False

        catalog_import = self._imports[import_id]

        # Don't delete if processing
        if catalog_import.import_status == ImportStatus.PROCESSING:
            return False

        # Delete file
        try:
            file_path = Path(catalog_import.source_file_path)
            if file_path.exists():
                file_path.unlink()
        except Exception as exc:
            logger.warning("Failed to delete file %s: %s", catalog_import.source_file_path, exc)

        # Remove from store
        del self._imports[import_id]
        return True


# Singleton service instance
_import_service: CatalogImportService | None = None


def get_import_service() -> CatalogImportService:
    """Get the catalog import service singleton."""
    global _import_service
    if _import_service is None:
        _import_service = CatalogImportService()
    return _import_service
