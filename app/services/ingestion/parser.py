"""PDF parsing service for catalog ingestion.

Uses pdfplumber's positional word extraction to correctly parse the
two-column layout of Essen price-list catalogs:
  • Left column  (~0–45 % of page width): product names, variants, SKUs
  • Right column (~45–100 %):             price table (18C / 15C / 12C / 10C /
                                           PSVP LISTA / NEGOCIO / PREF /
                                           PUNTOS E+ / PUNTOS / PUNTOS XL)

Both columns are read top-to-bottom independently, then zipped by index so
that product-block N is matched with price-row N.
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
    CatalogItemComponent,  # noqa: F401 – kept for public re-export
    CatalogItemSKU,
    CatalogMetadata,
    CatalogPrice,
    CatalogPromotion,
    CatalogSection,
    ItemType,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "1.2.0"


# ── Public dataclasses (interface unchanged) ──────────────────────────────────

@dataclass
class ExtractedBlock:
    """A block of text extracted from a PDF page."""

    page_number: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    block_type: str = "text"
    confidence: float = 1.0
    extraction_method: str = "structured"


@dataclass
class ParsedPage:
    """Parsed content from a single PDF page."""

    page_number: int
    blocks: list[ExtractedBlock] = field(default_factory=list)
    raw_text: str = ""
    has_images: bool = False
    used_ocr: bool = False


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


# ── Constants ─────────────────────────────────────────────────────────────────

# Token classification
_PRICE_TOKEN_RE = re.compile(r"^\$\s*[\d.,]+$")
_PLAIN_NUMBER_RE = re.compile(r"^[\d.,]+$")
_DASH_RE = re.compile(r"^[-–]$")
_SKU_RE = re.compile(r"^\d{7,9}$")

# Capacity-only line: "2,8 LTS", "3 LTS", "1,7 LTS"
_CAPACITY_LINE_RE = re.compile(
    r"^(\d+[,.]\d*|\d+)\s*(?:lt?s?|litros?)\s*$", re.IGNORECASE
)

# Boilerplate phrases found in column-header rows
_BOILERPLATE_PHRASES = (
    "cuotas sin inter",
    "psvp lista",
    "psvp negocio",
    "precio preferencial",
    "puntos essen",
    "puntos xl",
    "sin interés",
    "sin interes",
)

# Single-token column-header words (only match when the whole row is just these)
_BOILERPLATE_SINGLE_TOKENS = frozenset(
    {
        "psvp", "puntos", "preferencial", "negocio", "lista",
        "essen+", "precio", "cuotas", "interés", "interes",
    }
)

# Section name: normalised key → canonical display string
_SECTION_MAP: dict[str, str] = {
    "destacados": "DESTACADOS",
    "linea contemporanea": "LÍNEA CONTEMPORÁNEA",
    "contemporanea": "LÍNEA CONTEMPORÁNEA",
    "linea rosa": "LÍNEA ROSA",
    "linea nuit": "LÍNEA NUIT",
    "especiales essen": "LÍNEA ESPECIALES ESSEN",
    "complementos": "COMPLEMENTOS",
    "bazar premium": "BAZAR PREMIUM",
    "repuestos": "REPUESTOS",
    "destacados - essen+": "DESTACADOS - ESSEN+",
    "destacados essen+": "DESTACADOS - ESSEN+",
    "combo essen+": "COMBO ESSEN+",
}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Lowercase + strip accents."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _row_text(row: list[dict]) -> str:
    return _clean(" ".join(w["text"] for w in row))


def _group_into_rows(
    words: list[dict], y_tol: float = 4.0
) -> list[list[dict]]:
    """Group words into visual rows by proximity of their top-Y coordinate."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    current: list[dict] = [sorted_words[0]]
    base_y = sorted_words[0]["top"]
    for w in sorted_words[1:]:
        if abs(w["top"] - base_y) <= y_tol:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
            base_y = w["top"]
    if current:
        rows.append(sorted(current, key=lambda x: x["x0"]))
    return rows


def _is_boilerplate_row(row: list[dict]) -> bool:
    """Return True if this row is a price-table column header / boilerplate."""
    text = _row_text(row).lower()
    if any(phrase in text for phrase in _BOILERPLATE_PHRASES):
        return True
    # Row made entirely of boilerplate single-tokens
    tokens = [w["text"].lower() for w in row]
    if tokens and all(t in _BOILERPLATE_SINGLE_TOKENS for t in tokens):
        return True
    return False


def _is_price_data_row(row: list[dict]) -> bool:
    """Return True if this row contains at least one price ($) token."""
    return any(w["text"].startswith("$") for w in row)


def _is_sku_line(text: str) -> bool:
    """Return True if the line consists of one or more 7–9-digit SKU codes."""
    if _SKU_RE.match(text.strip()):
        return True
    parts = re.split(r"[\s\-+]+", text.strip())
    return len(parts) > 1 and all(_SKU_RE.match(p) for p in parts if p)


def _extract_skus_from_text(text: str) -> list[str]:
    return re.findall(r"\b(\d{7,9})\b", text)


def _parse_ars_number(text: str) -> float:
    """Parse a Spanish-format number to float.

    In Essen catalog prices the dot is a thousands separator
    (e.g. '27.446' → 27 446, '1.201.038' → 1 201 038).
    Trailing commas are treated as decimal separators.
    """
    text = text.replace("$", "").strip()
    if "," in text:
        # comma = decimal separator → remove thousand-dots first
        text = text.replace(".", "").replace(",", ".")
    else:
        # dot = thousands separator → remove all dots
        text = text.replace(".", "")
    return float(text)


def _detect_section_on_page(page_text: str) -> str | None:
    """Return the canonical section name if found in the page text."""
    norm_text = _norm(page_text)
    # Match longest key first to prefer "destacados essen+" over "destacados"
    for key in sorted(_SECTION_MAP, key=len, reverse=True):
        if key in norm_text:
            return _SECTION_MAP[key]
    return None


def _extract_metadata_from_text(text: str, filename: str) -> CatalogMetadata:
    metadata = CatalogMetadata(
        source_file_name=filename, parser_version=PARSER_VERSION
    )
    date_match = re.search(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", text)
    if date_match:
        metadata.updated_date = date_match.group()
    return metadata


def _extract_promotions_from_text(text: str) -> list[CatalogPromotion]:
    # This catalog has no explicit bank promotions
    return []


# ── Core page-parsing logic ───────────────────────────────────────────────────

def _group_name_rows_into_blocks(
    rows: list[list[dict]],
) -> list[list[list[dict]]]:
    """Cluster name-zone rows into per-product blocks.

    A block boundary is detected when:
    - A SKU line appears (it ends the current block).
    - A vertical gap > 12 pt exists between consecutive rows.
    """
    if not rows:
        return []

    blocks: list[list[list[dict]]] = []
    current: list[list[dict]] = []
    prev_bottom: float | None = None

    for row in rows:
        row_text = _row_text(row)
        row_top = min(w["top"] for w in row)
        row_bottom = max(w.get("bottom", w["top"] + 10) for w in row)

        # Large vertical gap → start a new block
        if prev_bottom is not None and (row_top - prev_bottom) > 12:
            if current:
                blocks.append(current)
            current = []

        current.append(row)
        prev_bottom = row_bottom

        # SKU line closes the current block
        if _is_sku_line(row_text):
            blocks.append(current)
            current = []
            prev_bottom = None

    if current:
        blocks.append(current)

    return [b for b in blocks if b]


def _parse_price_row(row: list[dict]) -> CatalogPrice | None:
    """Parse a right-zone price row into a CatalogPrice.

    Expected column order (left → right):
      0: 18 cuotas   1: 15 cuotas   2: 12 cuotas   3: 10 cuotas
      4: PSVP LISTA  5: PSVP NEGOCIO  6: PRECIO PREFERENCIAL
      7: PUNTOS ESSEN+  8: PUNTOS  9: PUNTOS XL
    """
    tokens = sorted(row, key=lambda w: w["x0"])
    values: list[float | None] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]["text"].strip()

        if t == "$":
            # The price number is the next token
            if i + 1 < len(tokens):
                try:
                    values.append(_parse_ars_number(tokens[i + 1]["text"]))
                except (ValueError, ZeroDivisionError):
                    values.append(None)
                i += 2
            else:
                i += 1
            continue

        if t.startswith("$"):
            try:
                values.append(_parse_ars_number(t[1:].strip()))
            except (ValueError, ZeroDivisionError):
                values.append(None)
            i += 1
            continue

        if _DASH_RE.match(t):
            values.append(None)
            i += 1
            continue

        if _PLAIN_NUMBER_RE.match(t):
            try:
                values.append(_parse_ars_number(t))
            except (ValueError, ZeroDivisionError):
                pass
            i += 1
            continue

        i += 1

    if not any(v is not None for v in values):
        return None

    def _v(idx: int) -> float | None:
        return values[idx] if idx < len(values) else None

    def _vi(idx: int) -> int | None:
        val = _v(idx)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                return None
        return None

    return CatalogPrice(
        currency="ARS",
        installments_18=_v(0),
        installments_15=_v(1),
        installments_12=_v(2),
        installments_10=_v(3),
        psvp_lista=_v(4),
        psvp_negocio=_v(5),
        precio_preferencial=_v(6),
        puntos_essen_plus=_vi(7),
        puntos=_vi(8),
        puntos_xl=_vi(9),
    )


def _block_to_catalog_item(
    block: list[list[dict]],
    price: CatalogPrice | None,
    section_name: str,
    page_num: int,
) -> CatalogItem | None:
    """Convert a name-zone product block into a CatalogItem."""
    if not block:
        return None

    name_lines: list[str] = []
    skus: list[CatalogItemSKU] = []
    capacity: float | None = None
    size_cm = ""

    for row in block:
        text = _row_text(row)
        if not text:
            continue

        # Skip section headers that may bleed into the name zone
        if any(k in _norm(text) for k in _SECTION_MAP):
            continue

        # SKU line
        if _is_sku_line(text):
            for code in _extract_skus_from_text(text):
                skus.append(CatalogItemSKU(sku=code))
            continue

        # Capacity-only line: "2,8 LTS"
        cap_m = _CAPACITY_LINE_RE.match(text.strip())
        if cap_m:
            try:
                capacity = float(cap_m.group(1).replace(",", "."))
                if capacity < 100:  # sanity: no 200-litre pots
                    size_cm = text.strip()
            except ValueError:
                pass
            continue

        # Everything else is part of the product name / description
        name_lines.append(text)

    if not name_lines:
        return None

    name = name_lines[0]
    description = " | ".join(name_lines[1:]) if len(name_lines) > 1 else ""

    # Item type heuristics
    combined = " ".join(name_lines).lower()
    if "combo" in combined or "kit" in combined:
        item_type = ItemType.COMBO
    elif section_name.upper() == "REPUESTOS":
        item_type = ItemType.REPLACEMENT_PART
    else:
        item_type = ItemType.PRODUCT

    # Attach first SKU to price record
    if price and skus:
        price.sku = skus[0].sku

    prices = [price] if price else []

    return CatalogItem(
        item_type=item_type,
        name=name,
        display_name=name,
        description=description,
        section_name=section_name,
        page_number=page_num,
        capacity_liters=capacity,
        size_cm=size_cm,
        skus=skus,
        prices=prices,
        raw_extracted_text=f"{name_lines} | skus={[s.sku for s in skus]}"[:500],
        extraction_confidence=0.9 if prices else 0.7,
    )


def _parse_page_items(
    page,  # pdfplumber.page.Page
    page_num: int,
    section_name: str,
) -> list[CatalogItem]:
    """Extract CatalogItems from a single PDF page using word positions."""
    words: list[dict] = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words:
        return []

    # ── Determine the X boundary between name zone and price zone ─────────────
    # The price zone starts at the leftmost '$' token.
    # We use the 10th-percentile X position to be robust against outliers.
    price_xs = sorted(w["x0"] for w in words if w["text"].startswith("$"))
    if not price_xs:
        return []  # No prices on this page

    p10_idx = max(0, len(price_xs) // 10)
    price_zone_x = price_xs[p10_idx]
    name_max_x = price_zone_x - 4  # 4 pt buffer

    # If the split is too far left (< 25% of page), the layout is unusual –
    # fall back to empty to avoid incorrect matches.
    if name_max_x < page.width * 0.20:
        logger.debug(
            "Page %d: price zone starts too early (x=%.1f) – skipping",
            page_num,
            price_zone_x,
        )
        return []

    # ── Split words by zone ───────────────────────────────────────────────────
    name_words = [w for w in words if w["x0"] < name_max_x]
    price_words = [w for w in words if w["x0"] >= name_max_x]

    # ── Group into visual rows ────────────────────────────────────────────────
    name_rows = _group_into_rows(name_words, y_tol=4)
    price_rows_all = _group_into_rows(price_words, y_tol=6)

    # ── Filter boilerplate ────────────────────────────────────────────────────
    name_rows = [r for r in name_rows if not _is_boilerplate_row(r)]
    price_rows_filtered = [
        r
        for r in price_rows_all
        if _is_price_data_row(r) and not _is_boilerplate_row(r)
    ]

    # ── Group name rows into per-product blocks ───────────────────────────────
    product_blocks = _group_name_rows_into_blocks(name_rows)

    # ── Parse price rows ──────────────────────────────────────────────────────
    parsed_prices: list[CatalogPrice] = []
    for row in price_rows_filtered:
        p = _parse_price_row(row)
        if p is not None:
            parsed_prices.append(p)

    if len(product_blocks) != len(parsed_prices):
        logger.debug(
            "Page %d: %d product blocks vs %d price rows",
            page_num,
            len(product_blocks),
            len(parsed_prices),
        )

    # ── Zip and build items ───────────────────────────────────────────────────
    items: list[CatalogItem] = []
    for idx, block in enumerate(product_blocks):
        price = parsed_prices[idx] if idx < len(parsed_prices) else None
        item = _block_to_catalog_item(block, price, section_name, page_num)
        if item is not None:
            items.append(item)

    return items


# ── PDFParser class ───────────────────────────────────────────────────────────

class PDFParser:
    """Parser for Essen catalog PDF files.

    Uses positioned word extraction to correctly associate product names
    with their price rows from a two-column layout.
    """

    def __init__(self) -> None:
        self._pdfplumber_available = False
        self._pytesseract_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        try:
            import pdfplumber  # noqa: F401

            self._pdfplumber_available = True
        except ImportError:
            logger.warning("pdfplumber not installed. PDF extraction unavailable.")

        try:
            import pytesseract  # noqa: F401

            self._pytesseract_available = True
        except ImportError:
            logger.warning("pytesseract not installed. OCR fallback unavailable.")

    def parse(self, pdf_path: str, filename: str) -> ParseResult:
        """Parse a catalog PDF and return a ParseResult."""
        result = ParseResult(success=False, parser_version=PARSER_VERSION)

        if not self._pdfplumber_available:
            result.error_message = (
                "PDF parsing not available: pdfplumber library not installed"
            )
            result.errors.append(result.error_message)
            return result

        try:
            import pdfplumber

            all_text_parts: list[str] = []
            current_section = ""
            section_idx = 0
            seen_sections: dict[str, int] = {}

            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    parsed_page = ParsedPage(page_number=page_num)

                    page_text = page.extract_text() or ""
                    parsed_page.raw_text = page_text
                    all_text_parts.append(page_text)

                    if page.images:
                        parsed_page.has_images = True

                    # ── Detect section ────────────────────────────────────────
                    detected = _detect_section_on_page(page_text)
                    if detected:
                        if detected not in seen_sections:
                            seen_sections[detected] = page_num
                            result.sections.append(
                                CatalogSection(
                                    id=f"section_{section_idx:03d}",
                                    name=detected,
                                    display_name=detected,
                                    page_start=page_num,
                                )
                            )
                            section_idx += 1
                        current_section = detected

                    # ── Extract items ─────────────────────────────────────────
                    items = _parse_page_items(page, page_num, current_section)

                    # If positional extraction found nothing and the page has
                    # images, try OCR as a last resort.
                    if not items and parsed_page.has_images and self._pytesseract_available:
                        ocr_text = self._extract_text_with_ocr(page)
                        if ocr_text:
                            parsed_page.raw_text = ocr_text
                            parsed_page.used_ocr = True
                            result.warnings.append(
                                f"Page {page_num}: used OCR (image-based content)"
                            )

                    result.items.extend(items)
                    result.pages.append(parsed_page)

                    # Store blocks in ParsedPage for downstream inspection
                    for item in items:
                        parsed_page.blocks.append(
                            ExtractedBlock(
                                page_number=page_num,
                                text=item.raw_extracted_text,
                                confidence=item.extraction_confidence or 1.0,
                            )
                        )

            all_text = "\n".join(all_text_parts)
            result.metadata = _extract_metadata_from_text(all_text, filename)
            result.promotions = _extract_promotions_from_text(all_text)

            # Collect all prices
            for item in result.items:
                result.prices.extend(item.prices)

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

    def _extract_text_with_ocr(self, page: "pdfplumber.page.Page") -> str:
        """Extract text from a page using Tesseract OCR as fallback."""
        try:
            import io

            import pytesseract
            from PIL import Image  # noqa: F401

            image = page.to_image(resolution=300)
            pil_image = image.original
            text = pytesseract.image_to_string(pil_image, lang="spa")
            return _clean(text)
        except Exception as exc:
            logger.warning("OCR extraction failed: %s", exc)
            return ""


# ── Singleton ─────────────────────────────────────────────────────────────────

_parser: PDFParser | None = None


def get_parser() -> PDFParser:
    """Return the shared PDFParser singleton."""
    global _parser
    if _parser is None:
        _parser = PDFParser()
    return _parser
