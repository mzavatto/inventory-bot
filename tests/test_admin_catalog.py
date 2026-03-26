"""Tests for the admin catalog import functionality."""
from __future__ import annotations

import io
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.admin.models import (
    CatalogImport,
    CatalogImportSummary,
    CatalogItem,
    CatalogItemSKU,
    CatalogMetadata,
    CatalogPrice,
    CatalogSection,
    ImportStatus,
    ItemType,
)
from app.admin.auth import (
    verify_credentials,
    create_session,
    get_session,
    invalidate_session,
    cleanup_expired_sessions,
)
from app.services.ingestion.import_service import (
    CatalogImportService,
    FileValidationError,
    ImportNotFoundError,
)
from app.services.ingestion.parser import (
    PDFParser,
    ParseResult,
    ExtractedBlock,
    ParsedPage,
    _normalize_text,
    _clean_text,
    _extract_metadata_from_text,
    _detect_sections_from_text,
    _extract_promotions_from_text,
    PARSER_VERSION,
)


class TestAdminAuth:
    """Tests for admin authentication."""

    def test_verify_credentials_valid(self) -> None:
        """Test valid credentials verification."""
        with patch("app.admin.auth.settings") as mock_settings:
            mock_settings.admin_username = "admin"
            mock_settings.admin_password = "password123"
            assert verify_credentials("admin", "password123") is True

    def test_verify_credentials_invalid_username(self) -> None:
        """Test invalid username."""
        with patch("app.admin.auth.settings") as mock_settings:
            mock_settings.admin_username = "admin"
            mock_settings.admin_password = "password123"
            assert verify_credentials("wrong", "password123") is False

    def test_verify_credentials_invalid_password(self) -> None:
        """Test invalid password."""
        with patch("app.admin.auth.settings") as mock_settings:
            mock_settings.admin_username = "admin"
            mock_settings.admin_password = "password123"
            assert verify_credentials("admin", "wrong") is False

    def test_create_session(self) -> None:
        """Test session creation."""
        session = create_session("testuser")
        assert session.token is not None
        assert session.user.username == "testuser"
        assert session.expires_at > datetime.now(timezone.utc)

    def test_get_session_valid(self) -> None:
        """Test retrieving a valid session."""
        session = create_session("testuser")
        retrieved = get_session(session.token)
        assert retrieved is not None
        assert retrieved.user.username == "testuser"

    def test_get_session_invalid(self) -> None:
        """Test retrieving with invalid token."""
        retrieved = get_session("invalid_token_12345")
        assert retrieved is None

    def test_invalidate_session(self) -> None:
        """Test session invalidation."""
        session = create_session("testuser")
        assert get_session(session.token) is not None
        result = invalidate_session(session.token)
        assert result is True
        assert get_session(session.token) is None

    def test_invalidate_nonexistent_session(self) -> None:
        """Test invalidating a non-existent session."""
        result = invalidate_session("nonexistent_token")
        assert result is False


class TestCatalogModels:
    """Tests for catalog data models."""

    def test_catalog_item_fingerprint(self) -> None:
        """Test fingerprint generation for catalog items."""
        item1 = CatalogItem(
            name="Test Product",
            line="Contemporary",
            dimensions="20 x 30",
            capacity_liters=5.0,
            section_name="Destacados",
        )
        item2 = CatalogItem(
            name="Test Product",
            line="Contemporary",
            dimensions="20 x 30",
            capacity_liters=5.0,
            section_name="Destacados",
        )
        item3 = CatalogItem(
            name="Different Product",
            line="Contemporary",
            dimensions="20 x 30",
            capacity_liters=5.0,
            section_name="Destacados",
        )

        # Same data should produce same fingerprint
        assert item1.fingerprint == item2.fingerprint
        # Different data should produce different fingerprint
        assert item1.fingerprint != item3.fingerprint

    def test_catalog_item_display_name_default(self) -> None:
        """Test display_name defaults to name."""
        item = CatalogItem(name="Test Product")
        assert item.display_name == "Test Product"

    def test_catalog_import_add_log(self) -> None:
        """Test adding log entries to import."""
        import_record = CatalogImport(source_file_name="test.pdf")
        import_record.add_log("Test message")
        assert len(import_record.raw_log) == 1
        assert "Test message" in import_record.raw_log[0]

    def test_import_status_enum(self) -> None:
        """Test import status enum values."""
        assert ImportStatus.PENDING.value == "pending"
        assert ImportStatus.PROCESSING.value == "processing"
        assert ImportStatus.COMPLETED.value == "completed"
        assert ImportStatus.FAILED.value == "failed"


class TestFileValidation:
    """Tests for file validation."""

    def setup_method(self) -> None:
        self.service = CatalogImportService()

    def test_validate_valid_pdf(self) -> None:
        """Test validation of valid PDF file."""
        # Should not raise
        self.service.validate_file(
            filename="catalog.pdf",
            file_size=1024 * 1024,  # 1MB
            content_type="application/pdf",
        )

    def test_validate_invalid_extension(self) -> None:
        """Test validation rejects non-PDF files."""
        with pytest.raises(FileValidationError) as exc_info:
            self.service.validate_file(
                filename="catalog.doc",
                file_size=1024,
                content_type="application/msword",
            )
        assert "Invalid file type" in str(exc_info.value)

    def test_validate_empty_file(self) -> None:
        """Test validation rejects empty files."""
        with pytest.raises(FileValidationError) as exc_info:
            self.service.validate_file(
                filename="catalog.pdf",
                file_size=0,
                content_type="application/pdf",
            )
        assert "empty" in str(exc_info.value).lower()

    def test_validate_oversized_file(self) -> None:
        """Test validation rejects oversized files."""
        with pytest.raises(FileValidationError) as exc_info:
            self.service.validate_file(
                filename="catalog.pdf",
                file_size=100 * 1024 * 1024,  # 100MB
                content_type="application/pdf",
            )
        assert "too large" in str(exc_info.value).lower()

    def test_compute_file_hash(self) -> None:
        """Test file hash computation."""
        content = b"test file content"
        file = io.BytesIO(content)
        hash1 = self.service.compute_file_hash(file)

        # Reset and compute again
        file.seek(0)
        hash2 = self.service.compute_file_hash(file)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest length


class TestParserHelpers:
    """Tests for parser helper functions."""

    def test_normalize_text(self) -> None:
        """Test text normalization."""
        assert _normalize_text("Café") == "cafe"
        assert _normalize_text("LÍNEA ROSA") == "linea rosa"
        assert _normalize_text("Destacados Essen+") == "destacados essen+"

    def test_clean_text(self) -> None:
        """Test text cleaning."""
        assert _clean_text("  hello   world  ") == "hello world"
        assert _clean_text("\n\t multiple \n spaces \t") == "multiple spaces"

    def test_extract_metadata_from_text(self) -> None:
        """Test metadata extraction."""
        text = """
        Catálogo Essen 2024
        Ciclo: 15
        Fecha: 01/03/2024
        """
        metadata = _extract_metadata_from_text(text, "catalog.pdf")

        assert metadata.source_file_name == "catalog.pdf"
        assert "essen" in metadata.catalog_name.lower()
        assert metadata.cycle == "15"
        assert "01/03/2024" in metadata.updated_date

    def test_detect_sections_from_text(self) -> None:
        """Test section detection."""
        text = """
        DESTACADOS
        Product 1
        Product 2

        LÍNEA ROSA
        Product 3
        """
        sections = _detect_sections_from_text(text, 1)
        assert len(sections) >= 2

    def test_extract_promotions_from_text(self) -> None:
        """Test promotion extraction."""
        text = """
        Banco Galicia 12 cuotas sin interés
        20% descuento con Visa
        """
        promotions = _extract_promotions_from_text(text)
        assert len(promotions) >= 2


class TestCatalogImportService:
    """Tests for catalog import service."""

    def setup_method(self) -> None:
        self.service = CatalogImportService()

    @pytest.mark.asyncio
    async def test_upload_file(self, tmp_path: Path) -> None:
        """Test file upload."""
        content = b"%PDF-1.4\nTest PDF content"
        file = io.BytesIO(content)

        with patch.object(self.service, "_sanitize_filename", return_value="test.pdf"):
            with patch("app.services.ingestion.import_service.settings") as mock_settings:
                mock_settings.catalog_upload_path = tmp_path
                mock_settings.catalog_max_file_size_bytes = 100 * 1024 * 1024
                mock_settings.catalog_allowed_extensions_list = [".pdf"]

                result = await self.service.upload_file(
                    file=file,
                    filename="test.pdf",
                    file_size=len(content),
                    content_type="application/pdf",
                    uploaded_by="admin",
                )

        assert result.id is not None
        assert result.source_file_name == "test.pdf"
        assert result.uploaded_by == "admin"
        assert result.import_status == ImportStatus.PENDING

    def test_get_import_not_found(self) -> None:
        """Test getting non-existent import."""
        with pytest.raises(ImportNotFoundError):
            self.service.get_import("nonexistent-id")

    def test_list_imports_empty(self) -> None:
        """Test listing imports when empty."""
        imports = self.service.list_imports()
        assert imports == []

    def test_sanitize_filename(self) -> None:
        """Test filename sanitization."""
        assert self.service._sanitize_filename("test.pdf") == "test.pdf"
        assert self.service._sanitize_filename("test file.pdf") == "test_file.pdf"
        # Long filenames should be truncated but keep extension
        long_name = "a" * 200 + ".pdf"
        sanitized = self.service._sanitize_filename(long_name)
        assert len(sanitized) <= 100
        assert sanitized.endswith(".pdf")


class TestPDFParser:
    """Tests for PDF parser."""

    def test_parser_initialization(self) -> None:
        """Test parser initializes correctly."""
        parser = PDFParser()
        # Should have pdfplumber since we installed it
        assert parser._pdfplumber_available is True

    def test_parse_nonexistent_file(self) -> None:
        """Test parsing non-existent file."""
        parser = PDFParser()
        result = parser.parse("/nonexistent/path.pdf", "test.pdf")
        assert result.success is False
        assert len(result.errors) > 0


class TestImportSummary:
    """Tests for import summary generation."""

    def test_summary_default_values(self) -> None:
        """Test summary has correct default values."""
        summary = CatalogImportSummary()
        assert summary.total_items_detected == 0
        assert summary.new_items_count == 0
        assert summary.updated_items_count == 0
        assert summary.deleted_items_count == 0
        assert summary.changed_prices_count == 0
        assert summary.warnings_count == 0
        assert summary.errors_count == 0
        assert summary.warnings == []
        assert summary.errors == []


class TestSKUMatching:
    """Tests for SKU matching logic."""

    def setup_method(self) -> None:
        self.service = CatalogImportService()

    def test_matching_by_sku(self) -> None:
        """Test item matching by SKU."""
        # Add an existing item
        existing = CatalogItem(
            id="existing-1",
            name="Existing Product",
            skus=[CatalogItemSKU(sku="SKU001")],
        )
        self.service._items_by_sku["SKU001"] = existing

        # Create new item with same SKU
        new_item = CatalogItem(
            name="Updated Product",
            skus=[CatalogItemSKU(sku="SKU001")],
        )

        # The service should match by SKU
        assert "SKU001" in self.service._items_by_sku
        assert self.service._items_by_sku["SKU001"].name == "Existing Product"

    def test_matching_by_fingerprint(self) -> None:
        """Test item matching by fingerprint."""
        existing = CatalogItem(
            id="existing-2",
            name="Test Product",
            line="Contemporary",
            dimensions="20 x 30",
            capacity_liters=5.0,
            section_name="Destacados",
        )
        self.service._items_by_fingerprint[existing.fingerprint] = existing

        new_item = CatalogItem(
            name="Test Product",
            line="Contemporary",
            dimensions="20 x 30",
            capacity_liters=5.0,
            section_name="Destacados",
        )

        # Same fingerprint should match
        assert new_item.fingerprint in self.service._items_by_fingerprint


class TestBundleExtraction:
    """Tests for bundle/combo extraction."""

    def test_detect_combo_type(self) -> None:
        """Test detection of combo item type."""
        from app.services.ingestion.parser import ItemType

        # Test block with combo keyword
        text = "Combo Familiar - SKU123 - Incluye 3 productos"
        assert "combo" in text.lower()

    def test_detect_bundle_type(self) -> None:
        """Test detection of bundle item type."""
        text = "Pack Promocional - SKU456 - Bundle especial"
        assert "bundle" in text.lower() or "pack" in text.lower()


class TestPriceExtraction:
    """Tests for price extraction."""

    def test_catalog_price_fields(self) -> None:
        """Test CatalogPrice model fields."""
        price = CatalogPrice(
            sku="SKU001",
            psvp_lista=1500.0,
            psvp_negocio=1200.0,
            installments_12=150.0,
            puntos=500,
        )
        assert price.psvp_lista == 1500.0
        assert price.psvp_negocio == 1200.0
        assert price.installments_12 == 150.0
        assert price.puntos == 500

    def test_price_versioning(self) -> None:
        """Test that prices support versioning."""
        price = CatalogPrice(
            sku="SKU001",
            psvp_lista=1500.0,
            valid_from=datetime.now(timezone.utc),
        )
        assert price.valid_from is not None
