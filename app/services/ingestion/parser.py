"""PDF parsing service for catalog ingestion.

Implements a line-based parser tailored to the Essen catalog layout:
- Cover page (first page) is skipped.
- Last page (bank promotions) is discarded.
- "DESTACADOS" sections produce COMBO items composed of multiple components.
- Other sections ("LINEA X", "COMPLEMENTOS", "BAZAR PREMIUM", "REPUESTOS")
  produce single-product items.
- The PSVP LISTA column is used as the canonical price; the 12 CUOTAS
  installment value is also stored.
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
    CatalogItemComponent,
    CatalogItemSKU,
    CatalogMetadata,
    CatalogPrice,
    CatalogPromotion,
    CatalogSection,
    ItemType,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "2.0.0"


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
    tables: list[list[list[str | None]]] = field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Generic helpers (kept for backward compatibility with existing tests)
# ---------------------------------------------------------------------------


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

    lines = text.split("\n")[:10]
    for line in lines:
        line = line.strip()
        if 5 < len(line) < 100:
            if "catálogo" in line.lower() or "catalogo" in line.lower():
                metadata.catalog_name = line
                break
            if "essen" in line.lower():
                metadata.catalog_name = line
                break

    cycle_match = re.search(
        r"(?:ciclo|campaña|edición|edition)\s*[:\s]*(\d+|\w+)",
        text,
        re.IGNORECASE,
    )
    if cycle_match:
        metadata.cycle = cycle_match.group(1)

    date_match = re.search(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", text)
    if date_match:
        metadata.updated_date = date_match.group()

    return metadata


KNOWN_SECTIONS = [
    "destacados",
    "destacados - essen+",
    "destacados essen+",
    "línea contemporánea",
    "linea contemporanea",
    "línea rosa",
    "linea rosa",
    "línea nuit",
    "linea nuit",
    "línea especiales essen",
    "linea especiales essen",
    "complementos",
    "bazar premium",
    "repuestos",
    "essen+",
    "ofertas",
    "promociones",
    "combos",
]


def _detect_sections_from_text(text: str, page_number: int) -> list[tuple[str, int]]:
    """Detect section headers from text content (used by tests)."""
    sections: list[tuple[str, int]] = []
    for raw_line in text.split("\n"):
        line_clean = raw_line.strip()
        line_norm = _normalize_text(line_clean)
        for kw in KNOWN_SECTIONS:
            if kw in line_norm and len(line_clean) < 60:
                sections.append((line_clean, page_number))
                break
    return sections


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------

PRICE_TOKEN_RE = re.compile(r"\$\s*[\d.,]+")
SIZE_LINE_RE = re.compile(
    r"^\s*[\d.,]+\s*(LTS?\b|L\b|CM\b|cm\b|×|x\s*\d)", re.IGNORECASE
)
SIZE_DIM_RE = re.compile(r"^\s*\d+\s*[xX]\s*\d+\s*(cm)?\s*$")
SKU_LINE_RE = re.compile(r"^[\d\s\-+/]+$")
SKU_TOKEN_RE = re.compile(r"\b\d{5,8}\b")
SIZE_PATTERN = re.compile(r"(\d+)\s*(?:cm|CM)", re.IGNORECASE)


def _is_price_line(line: str) -> bool:
    """A price line has at least 4 currency tokens (the four installment cols)."""
    return len(PRICE_TOKEN_RE.findall(line)) >= 4


# Whitelist of section headers that may appear at the top of a page.
# Comparison is accent-insensitive and case-insensitive.
SECTION_HEADERS = (
    "DESTACADOS",
    "DESTACADOS - ESSEN+",
    "DESTACADOS ESSEN+",
    "LÍNEA CONTEMPORÁNEA",
    "LÍNEA ROSA",
    "LÍNEA NUIT",
    "LÍNEA ESPECIALES ESSEN",
    "COMPLEMENTOS",
    "BAZAR PREMIUM",
    "REPUESTOS",
    "PROMOCIONES",
    "OFERTAS",
)
_SECTION_HEADERS_NORMALIZED = {_normalize_text(h) for h in SECTION_HEADERS}


def _is_section_header(line: str) -> bool:
    """Strict section-header detection.

    A line is a section header only when its (accent-insensitive) text
    matches one of the known section names. This avoids treating product
    descriptors such as "Bazar" or component names from a combo as new
    sections.
    """
    s = line.strip()
    if not s or len(s) > 60:
        return False
    return _normalize_text(s) in _SECTION_HEADERS_NORMALIZED


def _is_table_header(line: str) -> bool:
    upper = line.upper()
    if "CUOTAS" in upper and ("INTER" in upper or upper.count("CUOTAS") >= 2):
        return True
    if "PSVP" in upper and "LISTA" in upper:
        return True
    if "PUNTOS" in upper and "ESSEN" in upper:
        return True
    return False


def _is_meta_line(line: str) -> bool:
    upper = line.upper()
    return "ACTUALIZACION" in upper or "ACTUALIZACIÓN" in upper


def _is_sku_line(line: str) -> bool:
    s = line.strip()
    if not s or not SKU_LINE_RE.match(s):
        return False
    digits = re.sub(r"\D", "", s)
    return len(digits) >= 5


def _is_size_line(line: str) -> bool:
    s = line.strip()
    return bool(SIZE_LINE_RE.match(s) or SIZE_DIM_RE.match(s))


def _is_combo_name_line(line: str) -> bool:
    """Detects lines that name a combo / promotional offer."""
    upper = line.strip().upper()
    if not upper:
        return False
    if upper.startswith(("COMBO ", "COMBO:")):
        return True
    if upper.startswith("OPORTUNIDAD "):
        return True
    return False


def _classify_line(line: str) -> str:
    """Classify a single line. Returns one of:
    EMPTY, META, PRICE, SECTION, HEADER, SKU, SIZE, COMBO_NAME, TITLE, DESCRIPTOR.
    """
    stripped = line.strip()
    if not stripped:
        return "EMPTY"
    if _is_meta_line(stripped):
        return "META"
    if _is_price_line(stripped):
        return "PRICE"
    if _is_section_header(stripped):
        return "SECTION"
    if _is_table_header(stripped):
        return "HEADER"
    if _is_sku_line(stripped):
        return "SKU"
    if _is_combo_name_line(stripped):
        return "COMBO_NAME"
    if _is_size_line(stripped):
        return "SIZE"

    alpha = [c for c in stripped if c.isalpha()]
    if not alpha:
        return "DESCRIPTOR"
    upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
    if stripped.startswith("+") or upper_ratio >= 0.6:
        return "TITLE"
    return "DESCRIPTOR"


# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------


def _parse_price_value(cell: str | None) -> float | None:
    """Parse a price value from a cell.

    Spanish number formats:
    - "$ 9.455" -> 9455.0
    - "9.455,50" -> 9455.50
    - "9455" -> 9455.0
    - "1234,56" -> 1234.56
    Returns ``None`` for empty cells or values that look like a dash.
    """
    if cell is None:
        return None
    cell = cell.strip()
    if not cell or cell == "-":
        return None

    cell = re.sub(r"^\$\s*", "", cell).strip()
    if not cell or cell == "-":
        return None

    try:
        if "," in cell and "." in cell:
            cell = cell.replace(".", "").replace(",", ".")
        elif "." in cell:
            parts = cell.split(".")
            if len(parts) >= 2 and len(parts[-1]) == 3:
                cell = cell.replace(".", "")
        elif "," in cell:
            cell = cell.replace(",", ".")
        return float(cell)
    except ValueError:
        return None


def _parse_points_value(cell: str | None) -> int | None:
    """Parse a points value from a cell."""
    if cell is None:
        return None
    cell = cell.strip()
    if not cell or cell == "-":
        return None
    numeric = re.sub(r"[^\d]", "", cell)
    if not numeric:
        return None
    try:
        return int(numeric)
    except ValueError:
        return None


def _extract_skus_from_cell(cell: str | None) -> list[str]:
    """Extract numeric SKU codes from a string."""
    if not cell:
        return []
    skus: list[str] = []
    for match in SKU_TOKEN_RE.findall(cell):
        if match not in skus:
            skus.append(match)
    return skus


# Order of price columns as they appear in the Essen catalog:
#   18 CUOTAS | 15 CUOTAS | 12 CUOTAS | 10 CUOTAS |
#   PSVP LISTA | PSVP NEGOCIO | PRECIO PREFERENCIAL |
#   PUNTOS ESSEN+ | PUNTOS | (PUNTOS XL?)
_PRICE_FIELD_ORDER = (
    "installments_18",
    "installments_15",
    "installments_12",
    "installments_10",
    "psvp_lista",
    "psvp_negocio",
    "precio_preferencial",
)
_POINT_FIELD_ORDER = (
    "puntos_essen_plus",
    "puntos",
    "puntos_xl",
)


def _parse_price_line(line: str) -> tuple[CatalogPrice, str]:
    """Parse a price line and return (CatalogPrice, leading_text).

    The leading text is whatever appears before the first ``$``/dash; this
    is often the product name's tail (or a size descriptor).
    """
    head_idx = line.find("$")
    if head_idx == -1:
        head_idx = 0
    head = line[:head_idx].strip()
    rest = line[head_idx:]

    tokens = rest.split()
    prices: list[float | None] = []
    points: list[int | None] = []
    seen_currency = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "$":
            seen_currency = True
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            prices.append(_parse_price_value(value))
            i += 2
            continue
        if tok.startswith("$"):
            seen_currency = True
            prices.append(_parse_price_value(tok[1:]))
            i += 1
            continue
        if tok == "-":
            # A dash can be a missing currency value or a missing points value.
            if seen_currency and len(prices) < len(_PRICE_FIELD_ORDER):
                prices.append(None)
            else:
                points.append(None)
            i += 1
            continue
        # Pure integer (points)
        if re.fullmatch(r"\d+", tok):
            points.append(int(tok))
        else:
            # Anything unrecognized aborts parsing of the line.
            break
        i += 1

    price = CatalogPrice()
    for idx, value in enumerate(prices[: len(_PRICE_FIELD_ORDER)]):
        setattr(price, _PRICE_FIELD_ORDER[idx], value)
    for idx, value in enumerate(points[: len(_POINT_FIELD_ORDER)]):
        setattr(price, _POINT_FIELD_ORDER[idx], value)
    return price, head


# ---------------------------------------------------------------------------
# Line-based product extraction
# ---------------------------------------------------------------------------


@dataclass
class _ProductBlock:
    section_name: str
    page_number: int
    title_parts: list[str] = field(default_factory=list)
    descriptors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    skus: list[str] = field(default_factory=list)
    combo_name: str | None = None
    price: CatalogPrice | None = None
    raw_lines: list[str] = field(default_factory=list)
    # True once we've already collected an SKU after the price line for
    # this block. Used as the boundary-detection heuristic in non-combo
    # sections: a TITLE line that appears AFTER an SKU is treated as the
    # start of the next product (rather than a variant tag).
    sku_seen_after_price: bool = False

    def is_empty(self) -> bool:
        return not (self.title_parts or self.combo_name or self.skus or self.price)


def _is_combo_section(section_name: str) -> bool:
    return "destacados" in section_name.lower()


def _looks_like_new_product_title(line: str) -> bool:
    """Heuristic: a TITLE line that strongly looks like a *new* product.

    Used to decide whether a TITLE encountered after a price line in a
    non-combo section should start a new product, even when no SKU has
    been seen yet for the current block. Single uppercase words like
    "ROSA", "NUIT" or "TERRA" are variant tags and do NOT pass this test.
    """
    s = line.strip()
    if SIZE_PATTERN.search(s):
        return True
    if re.search(r"\d+\s*(LTS?|cm|CM|×|x\s*\d)", s, re.IGNORECASE):
        return True
    if len(s.split()) >= 2:
        return True
    return False


def _strip_lead_plus(s: str) -> str:
    return re.sub(r"^[\s+\-]+", "", s).strip()


def _build_combo_name(block: _ProductBlock) -> str:
    """Build a human-readable combo name."""
    components = [_strip_lead_plus(p) for p in block.title_parts if p.strip()]
    components = [c for c in components if c]

    if block.combo_name:
        base = block.combo_name.strip()
        if components:
            return f"{base}: {' + '.join(components)}"
        return base

    if components:
        if len(components) == 1:
            return f"Combo {components[0]}"
        return "Combo " + " + ".join(components)

    return "Combo"


def _build_product_name(block: _ProductBlock) -> str:
    """Build the name of a non-combo product."""
    parts = [p.strip() for p in block.title_parts if p.strip()]
    if not parts:
        if block.descriptors:
            return block.descriptors[0].strip()
        return "Producto sin nombre"

    name = parts[0]
    extras = [p for p in parts[1:] if p.lower() not in name.lower()]
    if extras:
        name = name + " " + " ".join(extras)
    return name.strip()


def _block_to_catalog_item(block: _ProductBlock) -> CatalogItem | None:
    """Convert a parsed product block into a ``CatalogItem``."""
    if block.price is None:
        return None
    if not (block.title_parts or block.combo_name):
        return None

    is_combo = _is_combo_section(block.section_name) or block.combo_name is not None
    name = _build_combo_name(block) if is_combo else _build_product_name(block)
    name = _clean_text(name)
    if not name:
        return None

    components: list[CatalogItemComponent] = []
    if is_combo:
        for order_idx, part in enumerate(block.title_parts):
            comp_name = _strip_lead_plus(part)
            if comp_name:
                components.append(
                    CatalogItemComponent(
                        component_name=comp_name,
                        order=order_idx,
                    )
                )

    primary_sku = block.skus[0] if block.skus else None
    if primary_sku and block.price is not None:
        block.price.sku = primary_sku

    skus = [CatalogItemSKU(sku=s) for s in block.skus]

    description = ""
    desc_parts: list[str] = []
    if block.sizes:
        desc_parts.append(" / ".join(s.strip() for s in block.sizes))
    if block.descriptors:
        desc_parts.extend(d.strip() for d in block.descriptors)
    if desc_parts:
        description = " | ".join(desc_parts)

    size_cm = ""
    for source in (name, *block.sizes, *block.title_parts):
        m = SIZE_PATTERN.search(source)
        if m:
            size_cm = f"{m.group(1)}cm"
            break

    item_type = ItemType.COMBO if is_combo else ItemType.PRODUCT
    if not is_combo and block.skus and len(block.skus) > 1:
        item_type = ItemType.BUNDLE

    raw_text = "\n".join(block.raw_lines)[:500]

    return CatalogItem(
        item_type=item_type,
        name=name,
        description=description,
        section_name=block.section_name,
        page_number=block.page_number,
        raw_extracted_text=raw_text,
        extraction_confidence=0.95,
        size_cm=size_cm,
        skus=skus,
        components=components,
        prices=[block.price] if block.price else [],
    )


def _parse_pages(pages: list[ParsedPage]) -> list[CatalogItem]:
    """Run the line-based parser across all pages, skipping the cover and
    the trailing bank-promotions page.
    """
    items: list[CatalogItem] = []
    if not pages:
        return items

    # Pages to process: drop first (cover) and last (bank promotions).
    pages_to_process = pages[1:-1] if len(pages) >= 3 else pages

    current_section = ""
    block: _ProductBlock | None = None
    state = "BEFORE_PRICE"  # or AFTER_PRICE

    def flush() -> None:
        nonlocal block
        if block is not None and not block.is_empty():
            item = _block_to_catalog_item(block)
            if item is not None:
                items.append(item)
        block = None

    for page in pages_to_process:
        for raw_line in page.raw_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            cls = _classify_line(line)

            if cls in ("EMPTY", "META", "HEADER"):
                continue

            if cls == "SECTION":
                flush()
                current_section = line
                state = "BEFORE_PRICE"
                continue

            if not current_section:
                # Skip stray text before any known section is entered.
                continue

            if block is None:
                block = _ProductBlock(
                    section_name=current_section, page_number=page.page_number
                )
                state = "BEFORE_PRICE"
            block.raw_lines.append(line)

            if cls == "PRICE":
                if block.price is not None:
                    # The current block already has a price, so this line
                    # belongs to a new product. Flush and start fresh.
                    flush()
                    block = _ProductBlock(
                        section_name=current_section,
                        page_number=page.page_number,
                    )
                    block.raw_lines.append(line)
                price, head = _parse_price_line(line)
                if head:
                    head_cls = _classify_line(head)
                    if head_cls == "COMBO_NAME":
                        block.combo_name = head
                    elif head_cls == "SIZE":
                        block.sizes.append(head)
                    elif head_cls == "DESCRIPTOR":
                        block.descriptors.append(head)
                    else:
                        block.title_parts.append(head)
                block.price = price
                state = "AFTER_PRICE"
                continue

            if cls == "COMBO_NAME":
                if state == "AFTER_PRICE" and block.combo_name is not None:
                    # We already have a combo name; this starts a new combo.
                    flush()
                    block = _ProductBlock(
                        section_name=current_section,
                        page_number=page.page_number,
                    )
                    block.raw_lines.append(line)
                    block.combo_name = line
                else:
                    block.combo_name = line
                continue

            if cls == "TITLE":
                if state == "AFTER_PRICE":
                    is_combo = _is_combo_section(current_section)
                    if is_combo and block.combo_name is None:
                        # Continuation of the combo (component listed after price).
                        block.title_parts.append(line)
                    elif (
                        not is_combo
                        and not block.sku_seen_after_price
                        and not _looks_like_new_product_title(line)
                    ):
                        # In a regular (non-combo) section, a single
                        # uppercase tag like "ROSA" or "TERRA" before the
                        # SKU is a variant indicator, not the next product.
                        block.descriptors.append(line)
                    else:
                        # Boundary: this title belongs to the next product.
                        flush()
                        block = _ProductBlock(
                            section_name=current_section,
                            page_number=page.page_number,
                        )
                        block.raw_lines.append(line)
                        block.title_parts.append(line)
                        state = "BEFORE_PRICE"
                else:
                    block.title_parts.append(line)
                continue

            if cls == "SIZE":
                block.sizes.append(line)
                continue

            if cls == "SKU":
                for sku in _extract_skus_from_cell(line):
                    if sku not in block.skus:
                        block.skus.append(sku)
                if state == "AFTER_PRICE":
                    block.sku_seen_after_price = True
                continue

            if cls == "DESCRIPTOR":
                block.descriptors.append(line)
                continue

    flush()
    return items


# ---------------------------------------------------------------------------
# Backward-compatible table extractor (used by older tests)
# ---------------------------------------------------------------------------


def _extract_items_from_table(
    table: list[list[str | None]],
    page_number: int,
    current_section: str,
) -> list[CatalogItem]:
    """Extract catalog items from a structured table.

    Kept for backward compatibility with existing tests; the main parser now
    works directly on extracted text.
    """
    items: list[CatalogItem] = []
    if not table or len(table) < 2:
        return items

    header_row = table[0]
    if not header_row:
        return items

    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        cell_norm = _normalize_text(str(cell))
        if "18" in cell_norm and "cuota" in cell_norm:
            col_map["installments_18"] = idx
        elif "15" in cell_norm and "cuota" in cell_norm:
            col_map["installments_15"] = idx
        elif "12" in cell_norm and "cuota" in cell_norm:
            col_map["installments_12"] = idx
        elif "10" in cell_norm and "cuota" in cell_norm:
            col_map["installments_10"] = idx
        elif "psvp" in cell_norm and "lista" in cell_norm:
            col_map["psvp_lista"] = idx
        elif "psvp" in cell_norm and "negocio" in cell_norm:
            col_map["psvp_negocio"] = idx
        elif "preferencial" in cell_norm:
            col_map["precio_preferencial"] = idx
        elif "essen" in cell_norm and "punto" in cell_norm:
            col_map["puntos_essen_plus"] = idx
        elif "punto" in cell_norm and "essen" not in cell_norm:
            col_map["puntos"] = idx

    def get_cell(row: list[str | None], col_name: str) -> str | None:
        idx = col_map.get(col_name)
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        if value is None:
            return None
        return str(value)

    for row in table[1:]:
        if not row or all(
            cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row
        ):
            continue

        product_name = ""
        skus: list[str] = []
        for cell in row[:3]:
            if not cell or not isinstance(cell, str) or not cell.strip():
                continue
            cell_text = cell.strip()
            cell_skus = _extract_skus_from_cell(cell_text)
            if cell_skus:
                for sku in cell_skus:
                    if sku not in skus:
                        skus.append(sku)
            elif not product_name:
                cell_text = re.sub(r"^[\s\-–•]+", "", cell_text)
                if len(cell_text) > 2 and not cell_text.replace(" ", "").isdigit():
                    product_name = cell_text

        if not product_name:
            continue

        size_cm = ""
        size_match = SIZE_PATTERN.search(product_name)
        if size_match:
            size_cm = f"{size_match.group(1)}cm"

        price = CatalogPrice(
            sku=skus[0] if skus else None,
            installments_18=_parse_price_value(get_cell(row, "installments_18")),
            installments_15=_parse_price_value(get_cell(row, "installments_15")),
            installments_12=_parse_price_value(get_cell(row, "installments_12")),
            installments_10=_parse_price_value(get_cell(row, "installments_10")),
            psvp_lista=_parse_price_value(get_cell(row, "psvp_lista")),
            psvp_negocio=_parse_price_value(get_cell(row, "psvp_negocio")),
            precio_preferencial=_parse_price_value(get_cell(row, "precio_preferencial")),
            puntos_essen_plus=_parse_points_value(get_cell(row, "puntos_essen_plus")),
            puntos=_parse_points_value(get_cell(row, "puntos")),
        )

        item_type = ItemType.PRODUCT
        name_lower = product_name.lower()
        if "combo" in name_lower or "kit" in name_lower:
            item_type = ItemType.COMBO

        items.append(
            CatalogItem(
                item_type=item_type,
                name=product_name,
                section_name=current_section,
                page_number=page_number,
                raw_extracted_text=str(row)[:500],
                extraction_confidence=0.85,
                size_cm=size_cm,
                skus=[CatalogItemSKU(sku=s) for s in skus],
                prices=[price],
            )
        )

    return items


# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------


def _extract_promotions_from_text(text: str) -> list[CatalogPromotion]:
    """Extract promotion information from text."""
    promotions: list[CatalogPromotion] = []

    bank_pattern = re.compile(
        r"(banco|visa|mastercard|amex|naranja|cabal|bbva|galicia|santander|macro)\s+"
        r"(\d+)\s*(?:cuotas?|pagos?)",
        re.IGNORECASE,
    )
    for match in bank_pattern.finditer(text):
        bank = match.group(1).title()
        installments = match.group(2)
        promotions.append(
            CatalogPromotion(
                description=f"{bank} - {installments} cuotas",
                bank_name=bank,
                installment_conditions=f"{installments} cuotas",
            )
        )

    discount_pattern = re.compile(r"(\d+)\s*%\s*(?:off|descuento|dto)", re.IGNORECASE)
    for match in discount_pattern.finditer(text):
        discount = float(match.group(1))
        promotions.append(
            CatalogPromotion(
                description=f"{int(discount)}% de descuento",
                discount_percent=discount,
            )
        )

    return promotions


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


class PDFParser:
    """Parser for catalog PDF files using structured text extraction."""

    def __init__(self) -> None:
        self._pdfplumber_available = False
        self._pytesseract_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        import importlib.util

        if importlib.util.find_spec("pdfplumber") is not None:
            self._pdfplumber_available = True
        else:
            logger.warning(
                "pdfplumber not installed. Structured PDF extraction unavailable."
            )

        if importlib.util.find_spec("pytesseract") is not None:
            self._pytesseract_available = True

    def parse(self, pdf_path: str, filename: str) -> ParseResult:
        """Parse a PDF catalog file and return a ``ParseResult``."""
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

            all_text = "\n".join(p.raw_text for p in pages)
            result.metadata = _extract_metadata_from_text(all_text, filename)

            # Build the section list from pages we actually inspect.
            seen_sections: dict[str, CatalogSection] = {}
            pages_for_sections = pages[1:-1] if len(pages) >= 3 else pages
            for page in pages_for_sections:
                for line in page.raw_text.split("\n"):
                    stripped = line.strip()
                    if _is_section_header(stripped):
                        key = _normalize_text(stripped)
                        if key not in seen_sections:
                            section = CatalogSection(
                                id=f"section_{len(seen_sections):03d}",
                                name=stripped,
                                display_name=stripped,
                                page_start=page.page_number,
                            )
                            seen_sections[key] = section
            result.sections = list(seen_sections.values())

            # Run line-based item extraction.
            items = _parse_pages(pages)
            result.items = items
            for item in items:
                result.prices.extend(item.prices)

            # Promotions only come from the last page (bank promos).
            if len(pages) >= 1:
                bank_text = pages[-1].raw_text if pages else ""
                result.promotions = _extract_promotions_from_text(bank_text)

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
        import pdfplumber

        pages: list[ParsedPage] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                parsed = ParsedPage(page_number=page_num)
                text = page.extract_text() or ""
                parsed.raw_text = text

                if page.images:
                    parsed.has_images = True

                # Tables are not used by the new parser, but we still
                # extract them so callers (and legacy helpers) can see them.
                try:
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            if table:
                                parsed.tables.append(table)
                except Exception as table_exc:
                    logger.debug(
                        "Table extraction failed for page %d: %s", page_num, table_exc
                    )

                if text.strip():
                    for para in text.split("\n\n"):
                        if para.strip():
                            parsed.blocks.append(
                                ExtractedBlock(
                                    page_number=page_num,
                                    text=para.strip(),
                                    extraction_method="structured",
                                )
                            )

                if not text.strip() and page.images and self._pytesseract_available:
                    ocr_text = self._extract_text_with_ocr(page)
                    if ocr_text:
                        parsed.raw_text = ocr_text
                        parsed.used_ocr = True
                        parsed.blocks.append(
                            ExtractedBlock(
                                page_number=page_num,
                                text=ocr_text,
                                extraction_method="ocr",
                                confidence=0.8,
                            )
                        )

                pages.append(parsed)
        return pages

    def _extract_text_with_ocr(self, page: "pdfplumber.page.Page") -> str:
        try:
            import pytesseract

            image = page.to_image(resolution=300)
            text = pytesseract.image_to_string(image.original, lang="spa")
            return _clean_text(text)
        except Exception as exc:
            logger.warning("OCR extraction failed: %s", exc)
            return ""


_parser: PDFParser | None = None


def get_parser() -> PDFParser:
    """Return the singleton PDF parser instance."""
    global _parser
    if _parser is None:
        _parser = PDFParser()
    return _parser
