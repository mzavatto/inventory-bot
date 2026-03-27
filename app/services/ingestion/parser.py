"""PDF parsing service for catalog ingestion.

Implements structured PDF text extraction with OCR fallback.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pdfplumber.page

from app.admin.models import (
    CatalogItem,
    CatalogItemSKU,
    CatalogMetadata,
    CatalogPrice,
    CatalogPromotion,
    CatalogSection,
    ItemType,
)

logger = logging.getLogger(__name__)

# Version of the parser for tracking
PARSER_VERSION = "1.1.0"


@dataclass
class ExtractedBlock:
    """A block of text extracted from a PDF page."""

    page_number: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    block_type: str = "text"  # text, table, image
    confidence: float = 1.0
    extraction_method: str = "structured"  # structured, ocr


@dataclass
class ParsedPage:
    """Parsed content from a single PDF page."""

    page_number: int
    blocks: list[ExtractedBlock] = field(default_factory=list)
    raw_text: str = ""
    has_images: bool = False
    used_ocr: bool = False
    tables: list[list[list[str | None]]] = field(default_factory=list)


@dataclass
class TableRow:
    """A row extracted from a PDF table."""

    product_name: str
    skus: list[str] = field(default_factory=list)
    installments_6: float | None = None
    installments_15: float | None = None
    installments_12: float | None = None
    installments_10: float | None = None
    psvp_lista: float | None = None
    psvp_negocio: float | None = None
    precio_preferencial: float | None = None
    puntos_essen_plus: int | None = None
    puntos: int | None = None


@dataclass
class ParseResult:
    """Result of parsing a PDF catalog."""

    success: bool
    error_message: str | None = None
    parser_version: str = PARSER_VERSION
    pages: list[ParsedPage] = field(default_factory=list)
    metadata: CatalogMetadata = field(default_factory=CatalogMetadata)
    sections: list[CatalogSection] = field(default_factory=list)
    items: list[CatalogItem] = field(default_factory=list)
    prices: list[CatalogPrice] = field(default_factory=list)
    promotions: list[CatalogPromotion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    """Normalize text by removing accents and converting to lowercase."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _clean_text(text: str) -> str:
    """Clean extracted text by removing extra whitespace."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_metadata_from_text(text: str, filename: str) -> CatalogMetadata:
    """Extract catalog metadata from text content."""
    metadata = CatalogMetadata(source_file_name=filename, parser_version=PARSER_VERSION)

    # Try to extract catalog name (often at the beginning)
    lines = text.split("\n")[:10]  # Check first 10 lines
    for line in lines:
        line = line.strip()
        if len(line) > 5 and len(line) < 100:
            if "catálogo" in line.lower() or "catalogo" in line.lower():
                metadata.catalog_name = line
                break
            if "essen" in line.lower():
                metadata.catalog_name = line
                break

    # Try to extract cycle/edition
    cycle_pattern = r"(?:ciclo|campaña|edición|edition)\s*[:\s]*(\d+|\w+)"
    cycle_match = re.search(cycle_pattern, text, re.IGNORECASE)
    if cycle_match:
        metadata.cycle = cycle_match.group(1)

    # Try to extract date
    date_pattern = r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
    date_match = re.search(date_pattern, text)
    if date_match:
        metadata.updated_date = date_match.group()

    return metadata


# Known section names for Essen catalogs
KNOWN_SECTIONS = [
    "destacados",
    "línea contemporánea",
    "linea contemporanea",
    "línea rosa",
    "linea rosa",
    "línea nuit",
    "linea nuit",
    "complementos",
    "bazar premium",
    "repuestos",
    "destacados essen+",
    "essen+",
    "ofertas",
    "promociones",
    "combos",
]


def _detect_sections_from_text(
    text: str, page_number: int
) -> list[tuple[str, int]]:
    """Detect section headers from text content."""
    sections: list[tuple[str, int]] = []
    lines = text.split("\n")

    for line in lines:
        line_clean = line.strip()
        line_normalized = _normalize_text(line_clean)

        for section_name in KNOWN_SECTIONS:
            if section_name in line_normalized and len(line_clean) < 60:
                sections.append((line_clean, page_number))
                break

    return sections


# Patterns for extracting item information
SKU_PATTERN = re.compile(r"\b([A-Z]{2,4}[\s\-]?\d{3,6})\b", re.IGNORECASE)
# Pattern for SKU codes in table format (just numeric, often multiple separated by spaces/dashes)
TABLE_SKU_PATTERN = re.compile(r"\b(\d{5,8})\b")
PRICE_PATTERN = re.compile(r"\$\s?([\d.,]+)")
DIMENSION_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:x|X|×)\s*(\d+(?:[.,]\d+)?)")
CAPACITY_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:lt?s?|litros?)", re.IGNORECASE)
POINTS_PATTERN = re.compile(r"(\d+)\s*(?:pts?|puntos)", re.IGNORECASE)
# Pattern for size in cm (e.g., "24cm", "28 cm")
SIZE_PATTERN = re.compile(r"(\d+)\s*(?:cm|CM)", re.IGNORECASE)


def _parse_price_value(cell: str | None) -> float | None:
    """Parse a price value from a table cell.
    
    Handles Spanish number formats commonly used in Essen catalogs:
    - "$ 9.455" (dot as thousands separator) -> 9455.0
    - "9.455,50" (dot as thousands, comma as decimal) -> 9455.50
    - "9455" (no separators) -> 9455.0
    - "1234,56" (comma as decimal only) -> 1234.56
    """
    if not cell:
        return None
    
    cell = cell.strip()
    if not cell:
        return None
    
    # Remove currency symbol and extra whitespace
    cell = re.sub(r"^\$\s*", "", cell)
    cell = cell.strip()
    
    if not cell:
        return None
    
    try:
        # Handle thousands separator (.) and decimal separator (,)
        # In Spanish format: 9.455 means 9455, 9.455,50 means 9455.50
        if "," in cell and "." in cell:
            # Both present: 1.234,56 format
            cell = cell.replace(".", "").replace(",", ".")
        elif "." in cell:
            # Check if it's a thousands separator or decimal
            # If there are more than 2 digits after the dot, it's likely thousands
            parts = cell.split(".")
            if len(parts) == 2 and len(parts[1]) == 3:
                # Thousands separator: 1.234 -> 1234
                cell = cell.replace(".", "")
            # Otherwise assume it's already a decimal number
        elif "," in cell:
            # Decimal separator only: 1234,56 -> 1234.56
            cell = cell.replace(",", ".")
        
        return float(cell)
    except ValueError:
        return None


def _parse_points_value(cell: str | None) -> int | None:
    """Parse a points value from a table cell."""
    if not cell:
        return None
    
    cell = cell.strip()
    if not cell:
        return None
    
    try:
        # Remove any non-numeric characters
        numeric = re.sub(r"[^\d]", "", cell)
        if numeric:
            return int(numeric)
        return None
    except ValueError:
        return None


def _extract_skus_from_cell(cell: str | None) -> list[str]:
    """Extract SKU codes from a table cell."""
    if not cell:
        return []
    
    skus = []
    # Look for numeric SKU codes (5-8 digits)
    matches = TABLE_SKU_PATTERN.findall(cell)
    for match in matches:
        skus.append(match)
    
    # Also try the alphanumeric pattern
    alpha_matches = SKU_PATTERN.findall(cell)
    for match in alpha_matches:
        sku_clean = match.upper().replace(" ", "").replace("-", "")
        if sku_clean not in skus:
            skus.append(sku_clean)
    
    return skus


def _extract_items_from_table(
    table: list[list[str | None]], 
    page_number: int,
    current_section: str
) -> list[CatalogItem]:
    """Extract catalog items from a table.
    
    Expected table format (based on Essen catalog):
    - Column 0: Product name/description
    - Column 1 (optional): SKU codes
    - Columns: 6 CUOTAS, 15 CUOTAS, 12 CUOTAS, 10 CUOTAS
    - Columns: PSVP LISTA, PSVP NEGOCIO, PRECIO PREFERENCIAL
    - Columns: PUNTOS ESSEN+, PUNTOS
    """
    items: list[CatalogItem] = []
    
    if not table or len(table) < 2:
        return items
    
    # Try to identify column indices from header row
    header_row = table[0]
    if not header_row:
        return items
    
    # Map column names to indices
    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        cell_norm = _normalize_text(cell)
        
        if "15" in cell_norm and "cuota" in cell_norm:
            col_map["installments_15"] = idx
        elif "12" in cell_norm and "cuota" in cell_norm:
            col_map["installments_12"] = idx
        elif "10" in cell_norm and "cuota" in cell_norm:
            col_map["installments_10"] = idx
        elif "psvp" in cell_norm and "lista" in cell_norm:
            col_map["psvp_lista"] = idx
        elif "psvp" in cell_norm and "negocio" in cell_norm:
            col_map["psvp_negocio"] = idx
        elif "preferencial" in cell_norm or ("precio" in cell_norm and "pref" in cell_norm):
            col_map["precio_preferencial"] = idx
        elif "essen" in cell_norm and "punto" in cell_norm:
            col_map["puntos_essen_plus"] = idx
        elif "punto" in cell_norm and "essen" not in cell_norm:
            col_map["puntos"] = idx
    
    # Process data rows
    for row_idx, row in enumerate(table[1:], start=1):
        if not row or all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row):
            continue
        
        # First non-empty cell is usually the product name
        product_name = ""
        skus: list[str] = []
        
        # Check first few cells for product name and SKUs
        for cell_idx, cell in enumerate(row[:3]):  # Only check first 3 columns
            if cell and isinstance(cell, str) and cell.strip():
                cell_text = cell.strip()
                
                # Check if this cell looks like SKUs (mostly numbers/dashes)
                sku_matches = _extract_skus_from_cell(cell_text)
                if sku_matches and len(sku_matches) > 0:
                    # This cell contains SKUs
                    for sku in sku_matches:
                        if sku not in skus:
                            skus.append(sku)
                elif not product_name:
                    # This is likely the product name
                    # Clean up the name - remove leading dashes/bullets
                    cell_text = re.sub(r"^[\s\-–•]+", "", cell_text)
                    if len(cell_text) > 2 and not cell_text.replace(" ", "").isdigit():
                        product_name = cell_text
        
        if not product_name:
            continue
        
        # Extract size from product name
        size_cm = ""
        size_match = SIZE_PATTERN.search(product_name)
        if size_match:
            size_cm = f"{size_match.group(1)}cm"
        
        # Extract prices from columns
        def get_cell(col_name: str) -> str | None:
            idx = col_map.get(col_name)
            if idx is not None and idx < len(row):
                return row[idx] if isinstance(row[idx], str) else str(row[idx]) if row[idx] is not None else None
            return None
        
        installments_15 = _parse_price_value(get_cell("installments_15"))
        installments_12 = _parse_price_value(get_cell("installments_12"))
        installments_10 = _parse_price_value(get_cell("installments_10"))
        psvp_lista = _parse_price_value(get_cell("psvp_lista"))
        psvp_negocio = _parse_price_value(get_cell("psvp_negocio"))
        precio_preferencial = _parse_price_value(get_cell("precio_preferencial"))
        puntos_essen_plus = _parse_points_value(get_cell("puntos_essen_plus"))
        puntos = _parse_points_value(get_cell("puntos"))
        
        # If we couldn't map columns, try to extract prices from cells by position
        # Look for price-like values in the row
        if not psvp_lista:
            for cell in row:
                if cell and isinstance(cell, str):
                    price = _parse_price_value(cell)
                    if price and price > 1000:  # Reasonable price threshold
                        psvp_lista = price
                        break
        
        # Create the catalog item
        item_skus = [CatalogItemSKU(sku=sku) for sku in skus] if skus else []
        
        prices: list[CatalogPrice] = []
        if psvp_lista or puntos or installments_12:
            price = CatalogPrice(
                sku=skus[0] if skus else None,
                installments_15=installments_15,
                installments_12=installments_12,
                installments_10=installments_10,
                psvp_lista=psvp_lista,
                psvp_negocio=psvp_negocio,
                precio_preferencial=precio_preferencial,
                puntos_essen_plus=puntos_essen_plus,
                puntos=puntos,
            )
            prices.append(price)
        
        # Determine item type
        item_type = ItemType.PRODUCT
        name_lower = product_name.lower()
        if "combo" in name_lower or "kit" in name_lower:
            item_type = ItemType.COMBO
        elif "repuesto" in name_lower or "reemplazo" in name_lower:
            item_type = ItemType.REPLACEMENT_PART
        elif "bundle" in name_lower or "pack" in name_lower or "x2" in name_lower or "x3" in name_lower:
            item_type = ItemType.BUNDLE
        
        item = CatalogItem(
            item_type=item_type,
            name=product_name,
            section_name=current_section,
            page_number=page_number,
            raw_extracted_text=str(row)[:500],
            extraction_confidence=0.9,
            size_cm=size_cm,
            skus=item_skus,
            prices=prices,
        )
        
        items.append(item)
    
    return items


def _extract_items_from_blocks(
    blocks: list[ExtractedBlock], current_section: str
) -> list[CatalogItem]:
    """Extract catalog items from text blocks."""
    items: list[CatalogItem] = []

    for block in blocks:
        text = block.text

        # Skip very short blocks
        if len(text) < 10:
            continue

        # Look for SKU patterns
        sku_matches = SKU_PATTERN.findall(text)
        if not sku_matches:
            continue

        # Create item for each detected SKU
        for sku in sku_matches:
            sku_clean = sku.upper().replace(" ", "").replace("-", "")

            # Extract name (text before SKU or first line)
            lines = text.split("\n")
            name = lines[0].strip() if lines else ""

            # Clean up name
            name = re.sub(SKU_PATTERN, "", name).strip()
            name = re.sub(r"^\s*[-–•]\s*", "", name).strip()

            if not name or len(name) < 3:
                name = f"Item {sku_clean}"

            # Determine item type
            item_type = ItemType.PRODUCT
            text_lower = text.lower()
            if "combo" in text_lower or "kit" in text_lower:
                item_type = ItemType.COMBO
            elif "repuesto" in text_lower or "reemplazo" in text_lower:
                item_type = ItemType.REPLACEMENT_PART
            elif "bundle" in text_lower or "pack" in text_lower:
                item_type = ItemType.BUNDLE

            # Extract dimensions
            dimensions = ""
            dim_match = DIMENSION_PATTERN.search(text)
            if dim_match:
                dimensions = f"{dim_match.group(1)} x {dim_match.group(2)}"

            # Extract capacity
            capacity: float | None = None
            cap_match = CAPACITY_PATTERN.search(text)
            if cap_match:
                capacity = float(cap_match.group(1).replace(",", "."))

            # Extract prices
            prices: list[CatalogPrice] = []
            price_matches = PRICE_PATTERN.findall(text)
            if price_matches:
                price_value = float(price_matches[0].replace(".", "").replace(",", "."))
                prices.append(
                    CatalogPrice(
                        sku=sku_clean,
                        psvp_lista=price_value,
                    )
                )

            # Extract points
            points: int | None = None
            points_match = POINTS_PATTERN.search(text)
            if points_match:
                points = int(points_match.group(1))
                if prices:
                    prices[0].puntos = points

            item = CatalogItem(
                item_type=item_type,
                name=name,
                section_name=current_section,
                page_number=block.page_number,
                raw_extracted_text=text[:500],  # Limit raw text size
                extraction_confidence=block.confidence,
                dimensions=dimensions,
                capacity_liters=capacity,
                skus=[CatalogItemSKU(sku=sku_clean)],
                prices=prices,
            )

            items.append(item)

    return items


def _extract_promotions_from_text(text: str) -> list[CatalogPromotion]:
    """Extract promotion information from text."""
    promotions: list[CatalogPromotion] = []

    # Look for bank promotion patterns
    bank_pattern = re.compile(
        r"(banco|visa|mastercard|amex|naranja|cabal|bbva|galicia|santander|macro)\s+"
        r"(\d+)\s*(?:cuotas?|pagos?)",
        re.IGNORECASE,
    )

    for match in bank_pattern.finditer(text):
        bank = match.group(1).title()
        installments = match.group(2)

        promo = CatalogPromotion(
            description=f"{bank} - {installments} cuotas",
            bank_name=bank,
            installment_conditions=f"{installments} cuotas",
        )
        promotions.append(promo)

    # Look for discount patterns
    discount_pattern = re.compile(r"(\d+)\s*%\s*(?:off|descuento|dto)", re.IGNORECASE)
    for match in discount_pattern.finditer(text):
        discount = float(match.group(1))
        promo = CatalogPromotion(
            description=f"{int(discount)}% de descuento",
            discount_percent=discount,
        )
        promotions.append(promo)

    return promotions


class PDFParser:
    """Parser for catalog PDF files.

    Uses structured text extraction first, with OCR fallback for
    image-based pages or unreadable regions.
    """

    def __init__(self) -> None:
        self._pdfplumber_available = False
        self._pytesseract_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check which PDF extraction libraries are available."""
        import importlib.util

        if importlib.util.find_spec("pdfplumber") is not None:
            self._pdfplumber_available = True
        else:
            logger.warning(
                "pdfplumber not installed. Structured PDF extraction unavailable."
            )

        if importlib.util.find_spec("pytesseract") is not None:
            self._pytesseract_available = True
        else:
            logger.warning("pytesseract not installed. OCR fallback unavailable.")

    def parse(self, pdf_path: str, filename: str) -> ParseResult:
        """Parse a PDF catalog file.

        Args:
            pdf_path: Path to the PDF file.
            filename: Original filename for metadata.

        Returns:
            ParseResult with extracted catalog data.
        """
        result = ParseResult(success=False, parser_version=PARSER_VERSION)

        if not self._pdfplumber_available:
            result.error_message = (
                "PDF parsing not available: pdfplumber library not installed"
            )
            result.errors.append(result.error_message)
            return result

        try:

            pages = self._extract_pages_with_pdfplumber(pdf_path)
            result.pages = pages

            # Combine all text for metadata extraction
            all_text = "\n".join(page.raw_text for page in pages)

            # Extract metadata
            result.metadata = _extract_metadata_from_text(all_text, filename)

            # Detect sections
            all_sections: list[tuple[str, int]] = []
            for page in pages:
                sections = _detect_sections_from_text(page.raw_text, page.page_number)
                all_sections.extend(sections)

            # Create section objects
            section_map: dict[str, CatalogSection] = {}
            for idx, (section_name, page_num) in enumerate(all_sections):
                section_id = f"section_{idx:03d}"
                section = CatalogSection(
                    id=section_id,
                    name=section_name,
                    display_name=section_name,
                    page_start=page_num,
                )
                section_map[_normalize_text(section_name)] = section
                result.sections.append(section)

            # Extract items from each page
            current_section = ""
            for page in pages:
                # Update current section if new one detected
                for section_name, page_num in all_sections:
                    if page_num <= page.page_number:
                        current_section = section_name

                # Track items extracted from tables on this page
                table_items_count = 0
                
                # First, try to extract items from tables (preferred for catalog PDFs)
                for table in page.tables:
                    table_items = _extract_items_from_table(
                        table, page.page_number, current_section
                    )
                    result.items.extend(table_items)
                    table_items_count += len(table_items)

                # If no items from tables on this page, fall back to block extraction
                if table_items_count == 0:
                    items = _extract_items_from_blocks(page.blocks, current_section)
                    result.items.extend(items)

            # Extract promotions
            result.promotions = _extract_promotions_from_text(all_text)

            # Collect prices
            for item in result.items:
                result.prices.extend(item.prices)

            # Add warnings for pages that needed OCR
            for page in pages:
                if page.used_ocr:
                    result.warnings.append(
                        f"Page {page.page_number}: Used OCR due to image-based content"
                    )

            result.success = True

            if not result.items:
                result.warnings.append(
                    "No items could be extracted from the PDF. "
                    "The document may require manual review."
                )

        except Exception as exc:
            logger.exception("Error parsing PDF: %s", exc)
            result.error_message = f"Error parsing PDF: {exc}"
            result.errors.append(str(exc))

        return result

    def _extract_pages_with_pdfplumber(self, pdf_path: str) -> list[ParsedPage]:
        """Extract pages using pdfplumber for structured text extraction."""
        import pdfplumber

        pages: list[ParsedPage] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                parsed_page = ParsedPage(page_number=page_num)

                # Extract text
                text = page.extract_text() or ""
                parsed_page.raw_text = text

                # Check for images
                if page.images:
                    parsed_page.has_images = True

                # Extract tables from the page
                try:
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            if table:  # Table is not empty
                                parsed_page.tables.append(table)
                except Exception as table_exc:
                    logger.debug("Table extraction failed for page %d: %s", page_num, table_exc)

                # Create blocks from text
                if text.strip():
                    # Split into paragraphs/blocks
                    paragraphs = text.split("\n\n")
                    for para in paragraphs:
                        if para.strip():
                            block = ExtractedBlock(
                                page_number=page_num,
                                text=para.strip(),
                                extraction_method="structured",
                            )
                            parsed_page.blocks.append(block)

                # If no text extracted but has images, try OCR
                if not text.strip() and page.images and self._pytesseract_available:
                    ocr_text = self._extract_text_with_ocr(page)
                    if ocr_text:
                        parsed_page.raw_text = ocr_text
                        parsed_page.used_ocr = True
                        block = ExtractedBlock(
                            page_number=page_num,
                            text=ocr_text,
                            extraction_method="ocr",
                            confidence=0.8,  # Lower confidence for OCR
                        )
                        parsed_page.blocks.append(block)

                pages.append(parsed_page)

        return pages

    def _extract_text_with_ocr(self, page: "pdfplumber.page.Page") -> str:
        """Extract text from a page using OCR as fallback.
        
        Args:
            page: A pdfplumber Page object.
        """
        try:
            import pytesseract

            # Convert page to image
            image = page.to_image(resolution=300)
            pil_image = image.original

            # Run OCR
            text = pytesseract.image_to_string(pil_image, lang="spa")
            return _clean_text(text)
        except Exception as exc:
            logger.warning("OCR extraction failed: %s", exc)
            return ""


# Singleton parser instance
_parser: PDFParser | None = None


def get_parser() -> PDFParser:
    """Get the PDF parser singleton."""
    global _parser
    if _parser is None:
        _parser = PDFParser()
    return _parser
