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


class TestTableExtraction:
    """Tests for table-based PDF extraction."""

    def test_parse_price_value_with_dollar_sign(self) -> None:
        """Test parsing prices with dollar sign."""
        from app.services.ingestion.parser import _parse_price_value
        assert _parse_price_value("$ 9.455") == 9455.0
        assert _parse_price_value("$9.455") == 9455.0
        assert _parse_price_value("$11.346") == 11346.0

    def test_parse_price_value_without_dollar_sign(self) -> None:
        """Test parsing prices without dollar sign."""
        from app.services.ingestion.parser import _parse_price_value
        assert _parse_price_value("9.455") == 9455.0
        assert _parse_price_value("170.188") == 170188.0

    def test_parse_price_value_none_or_empty(self) -> None:
        """Test parsing None or empty price values."""
        from app.services.ingestion.parser import _parse_price_value
        assert _parse_price_value(None) is None
        assert _parse_price_value("") is None
        assert _parse_price_value("   ") is None

    def test_parse_points_value(self) -> None:
        """Test parsing points values."""
        from app.services.ingestion.parser import _parse_points_value
        assert _parse_points_value("35") == 35
        assert _parse_points_value("45") == 45
        assert _parse_points_value(None) is None
        assert _parse_points_value("") is None

    def test_extract_skus_from_cell(self) -> None:
        """Test extracting SKU codes from table cells."""
        from app.services.ingestion.parser import _extract_skus_from_cell
        skus = _extract_skus_from_cell("9455012 - 3754066 - 3755098")
        assert "9455012" in skus
        assert "3754066" in skus
        assert "3755098" in skus

    def test_extract_items_from_table(self) -> None:
        """Test extracting items from a catalog table."""
        from app.services.ingestion.parser import _extract_items_from_table

        table = [
            ["", "6 CUOTAS", "15 CUOTAS", "12 CUOTAS", "10 CUOTAS", "PSVP LISTA", "PSVP NEGOCIO", "PRECIO PREFERENCIAL", "PUNTOS ESSEN+", "PUNTOS"],
            ["Savarín 24cm TWISTER", "$ 9.455", "$ 11.346", "$ 14.182", "$ 17.019", "$ 170.188", "$ 136.150", "$ 122.535", "35", "35"],
            ["Savarín 18cm TIERRA", "$ 7.024", "$ 9.428", "$ 10.535", "$ 12.643", "$ 126.425", "$ 101.140", "$ 91.026", "26", "26"],
        ]

        items = _extract_items_from_table(table, 1, "COMPLEMENTOS")
        assert len(items) == 2
        assert items[0].name == "Savarín 24cm TWISTER"
        assert items[0].prices[0].psvp_lista == 170188.0
        assert items[0].prices[0].puntos == 35
        assert items[0].section_name == "COMPLEMENTOS"

    def test_extract_items_from_table_with_skus(self) -> None:
        """Test extracting items with SKU codes in table."""
        from app.services.ingestion.parser import _extract_items_from_table

        table = [
            ["Producto", "SKU", "PSVP LISTA", "PUNTOS"],
            ["WOK 30cm", "3754012 - 3754066 - 3755098", "$ 97.250", "20"],
        ]

        items = _extract_items_from_table(table, 1, "COMPLEMENTOS")
        assert len(items) == 1
        assert items[0].name == "WOK 30cm"


class TestCatalogJsonSaving:
    """Tests for saving items to catalog.json."""

    def test_catalog_item_to_product_dict(self) -> None:
        """Test converting CatalogItem to product dict."""
        from app.services.ingestion.import_service import _catalog_item_to_product_dict

        item = CatalogItem(
            name="Savarín 24cm TWISTER",
            section_name="COMPLEMENTOS",
            size_cm="24cm",
            skus=[CatalogItemSKU(sku="9455012")],
            prices=[CatalogPrice(
                sku="9455012",
                psvp_lista=170188.0,
                puntos=35,
            )],
        )

        product = _catalog_item_to_product_dict(item, 1)
        assert product["id"] == "9455012"
        assert product["name"] == "Savarín 24cm TWISTER"
        assert product["price"] == 170188.0
        assert product["category"] == "COMPLEMENTOS"

    def test_catalog_item_without_sku_gets_generated_id(self) -> None:
        """Test that items without SKU get a generated ID."""
        from app.services.ingestion.import_service import _catalog_item_to_product_dict

        item = CatalogItem(
            name="Test Product",
            section_name="Test Section",
        )

        product = _catalog_item_to_product_dict(item, 5)
        assert product["id"] == "P005"

    def test_catalog_item_propagates_12_installments(self) -> None:
        """The 12-cuotas value must be exposed in the product dict."""
        from app.services.ingestion.import_service import _catalog_item_to_product_dict

        item = CatalogItem(
            name="WOK TERRA",
            section_name="LÍNEA CONTEMPORÁNEA",
            skus=[CatalogItemSKU(sku="38223002")],
            prices=[CatalogPrice(
                psvp_lista=389000.0,
                installments_12=32417.0,
            )],
        )
        product = _catalog_item_to_product_dict(item, 1)
        assert product["price"] == 389000.0
        assert product["price_installments_12"] == 32417.0


class TestLineBasedParser:
    """Tests for the line-based section/product parser."""

    def _parse(self, text: str, page_number: int = 2) -> list[CatalogItem]:
        from app.services.ingestion.parser import _parse_pages, ParsedPage

        # We feed the parser two extra pages because it skips the cover
        # (first) and the bank-promotions trailer (last).
        cover = ParsedPage(page_number=1, raw_text="Actualizacion: 27/02/24")
        body = ParsedPage(page_number=page_number, raw_text=text)
        trailer = ParsedPage(page_number=page_number + 1, raw_text="")
        return _parse_pages([cover, body, trailer])

    def test_destacados_combo_with_plus_components(self) -> None:
        """A DESTACADOS row with components separated by '+' becomes a combo."""
        text = (
            "DESTACADOS\n"
            "Cuotas sin interés\n"
            "18 CUOTAS 15 CUOTAS 12 CUOTAS 10 CUOTAS PSVP LISTA\n"
            "BIFERA 33X23cm\n"
            "+ SARTÉN CHEF TERRA\n"
            "$ 33.497 $ 40.197 $ 50.246 $ 60.295 $ 602.950 $ 482.360 $ 434.124 124 124 0\n"
            "Combo a Tu Medida Chef\n"
            "Capri o Terra\n"
        )
        items = self._parse(text)
        assert len(items) == 1
        item = items[0]
        assert item.item_type == ItemType.COMBO
        assert "BIFERA 33X23cm" in item.name
        assert "SARTÉN CHEF TERRA" in item.name
        assert "Combo a Tu Medida Chef" in item.name
        assert item.prices[0].psvp_lista == 602950.0
        assert item.prices[0].installments_12 == 50246.0
        assert item.prices[0].puntos == 124

    def test_linea_product_with_size_and_sku_after_price(self) -> None:
        """Standard LÍNEA layout: product name, price line, then size + SKU."""
        text = (
            "LÍNEA CONTEMPORÁNEA\n"
            "18 CUOTAS 15 CUOTAS 12 CUOTAS 10 CUOTAS PSVP LISTA\n"
            "WOK TERRA\n"
            "3 LTS $ 21.611 $ 25.933 $ 32.417 $ 38.900 $ 389.000 $ 311.200 $ 280.080 80 80 0\n"
            "38223002\n"
        )
        items = self._parse(text)
        assert len(items) == 1
        item = items[0]
        assert item.item_type == ItemType.PRODUCT
        assert item.name == "WOK TERRA"
        assert item.skus[0].sku == "38223002"
        assert item.prices[0].psvp_lista == 389000.0
        assert item.prices[0].installments_12 == 32417.0

    def test_variant_tag_after_price_is_not_new_product(self) -> None:
        """Single uppercase tags like 'ROSA' should stay as variant info."""
        text = (
            "LÍNEA ROSA\n"
            "18 CUOTAS 15 CUOTAS 12 CUOTAS 10 CUOTAS PSVP LISTA\n"
            "SAVARIN 24cm $ 9.455 $ 11.346 $ 14.182 $ 17.019 $ 170.188 $ 136.150 $ 122.535 35 35 0\n"
            "ROSA\n"
            "38801339\n"
        )
        items = self._parse(text)
        assert len(items) == 1
        assert items[0].name == "SAVARIN 24cm"
        assert items[0].skus[0].sku == "38801339"
        assert items[0].prices[0].psvp_lista == 170188.0

    def test_destacados_essen_plus_combo_with_uppercase_name(self) -> None:
        """An uppercase 'COMBO ESSEN+ ...' line is treated as the combo name."""
        text = (
            "DESTACADOS - ESSEN+\n"
            "18 CUOTAS 15 CUOTAS 12 CUOTAS 10 CUOTAS PSVP LISTA\n"
            "REIN\n"
            "CACEROLA 24cm\n"
            "COMBO ESSEN+ REIN & CACEROLA 24 $ 200.475 $ 240.571 $ 300.713 $ 360.856 $ 3.608.559 $ 2.886.847 - - 635 0\n"
            "80010040 - 80010050 + 80010060\n"
            "Terra Cera Forte, Capri o Terra\n"
        )
        items = self._parse(text)
        assert len(items) == 1
        item = items[0]
        assert item.item_type == ItemType.COMBO
        assert "COMBO ESSEN+ REIN & CACEROLA 24" in item.name
        assert {s.sku for s in item.skus} == {"80010040", "80010050", "80010060"}
        assert item.prices[0].psvp_lista == 3608559.0
        assert item.prices[0].installments_12 == 300713.0

    def test_first_and_last_pages_are_skipped(self) -> None:
        """Cover and bank-promotions pages must produce no items."""
        from app.services.ingestion.parser import _parse_pages, ParsedPage

        cover = ParsedPage(page_number=1, raw_text="Actualizacion: 27/02/24")
        bank = ParsedPage(
            page_number=2,
            raw_text="Banco Galicia 12 cuotas sin interés con Visa",
        )
        items = _parse_pages([cover, bank])
        assert items == []

