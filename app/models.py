from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class Product(BaseModel):
    id: str
    name: str
    description: str = ""
    price: float
    price_installments_12: float | None = None
    unit: str = "unidad"
    category: str = ""
    stock: int | None = None
    promotions: list[Promotion] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Promotion(BaseModel):
    description: str
    discount_percent: float | None = None
    discount_amount: float | None = None
    min_quantity: int | None = None
    conditions: str = ""


class OrderItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        self.subtotal = round(self.quantity * self.unit_price, 2)


class Order(BaseModel):
    """Draft order (a.k.a. presupuesto) being assembled by a seller.

    Tracks line items, an optional discount and free-form notes for the
    client. ``total`` always reflects the discounted total; ``subtotal``
    is the sum before any discount.
    """

    items: list[OrderItem] = Field(default_factory=list)
    subtotal: float = 0.0
    discount_percent: float | None = None
    discount_amount: float | None = None
    total: float = 0.0
    notes: str = ""
    client_name: str = ""

    def _compute_subtotal(self) -> float:
        return round(sum(item.subtotal for item in self.items), 2)

    def _compute_discount_amount(self, subtotal: float) -> float:
        if self.discount_amount is not None and self.discount_amount > 0:
            return round(min(self.discount_amount, subtotal), 2)
        if self.discount_percent is not None and self.discount_percent > 0:
            return round(subtotal * (self.discount_percent / 100.0), 2)
        return 0.0

    def recalculate_total(self) -> None:
        self.subtotal = self._compute_subtotal()
        discount = self._compute_discount_amount(self.subtotal)
        self.total = round(self.subtotal - discount, 2)

    def add_item(self, item: OrderItem) -> None:
        for existing in self.items:
            if existing.product_id == item.product_id:
                existing.quantity += item.quantity
                existing.subtotal = round(existing.quantity * existing.unit_price, 2)
                self.recalculate_total()
                return
        self.items.append(item)
        self.recalculate_total()

    def remove_item(self, product_id: str) -> bool:
        for i, existing in enumerate(self.items):
            if existing.product_id == product_id:
                self.items.pop(i)
                self.recalculate_total()
                return True
        return False

    def update_quantity(self, product_id: str, quantity: int) -> bool:
        for existing in self.items:
            if existing.product_id == product_id:
                if quantity <= 0:
                    return self.remove_item(product_id)
                existing.quantity = quantity
                existing.subtotal = round(quantity * existing.unit_price, 2)
                self.recalculate_total()
                return True
        return False

    def update_unit_price(self, product_id: str, unit_price: float) -> bool:
        for existing in self.items:
            if existing.product_id == product_id:
                existing.unit_price = unit_price
                existing.subtotal = round(existing.quantity * unit_price, 2)
                self.recalculate_total()
                return True
        return False

    def set_discount(
        self,
        percent: float | None = None,
        amount: float | None = None,
    ) -> None:
        """Set a discount as either a percent or a fixed amount.

        Passing ``None`` to both clears the discount.
        """
        self.discount_percent = percent if percent and percent > 0 else None
        self.discount_amount = amount if amount and amount > 0 else None
        self.recalculate_total()

    def clear(self) -> None:
        self.items.clear()
        self.subtotal = 0.0
        self.discount_percent = None
        self.discount_amount = None
        self.total = 0.0
        self.notes = ""
        self.client_name = ""

    def to_summary(self) -> str:
        """Detailed internal summary of the draft order."""
        if not self.items:
            return "El pedido está vacío."

        lines = ["📋 *Resumen del pedido:*"]
        if self.client_name:
            lines.append(f"Cliente: {self.client_name}")
        lines.append("")
        for item in self.items:
            lines.append(
                f"• [{item.product_id}] {item.product_name} x{item.quantity} "
                f"@ ${item.unit_price:,.2f} = ${item.subtotal:,.2f}"
            )
        lines.append("")
        if self.discount_percent or self.discount_amount:
            discount_value = self._compute_discount_amount(self.subtotal)
            label = (
                f"{self.discount_percent:g}%"
                if self.discount_percent
                else f"${self.discount_amount:,.2f}"
            )
            lines.append(f"Subtotal: ${self.subtotal:,.2f}")
            lines.append(f"Descuento ({label}): -${discount_value:,.2f}")
        lines.append(f"💰 *Total: ${self.total:,.2f}*")
        if self.notes:
            lines.append("")
            lines.append(f"📝 Notas: {self.notes}")
        return "\n".join(lines)

    def to_client_summary(self) -> str:
        """Clean, copy-paste-ready summary for sending to a customer."""
        if not self.items:
            return "El pedido está vacío."

        lines: list[str] = []
        if self.client_name:
            lines.append(f"Hola {self.client_name}, te paso el detalle del pedido:")
            lines.append("")
        else:
            lines.append("Detalle del pedido:")
            lines.append("")

        for item in self.items:
            lines.append(
                f"- {item.product_name} x{item.quantity}: ${item.subtotal:,.2f}"
            )
        lines.append("")
        if self.discount_percent or self.discount_amount:
            discount_value = self._compute_discount_amount(self.subtotal)
            label = (
                f"{self.discount_percent:g}%"
                if self.discount_percent
                else f"${self.discount_amount:,.2f}"
            )
            lines.append(f"Subtotal: ${self.subtotal:,.2f}")
            lines.append(f"Descuento {label}: -${discount_value:,.2f}")
        lines.append(f"Total: ${self.total:,.2f}")
        if self.notes:
            lines.append("")
            lines.append(self.notes)
        return "\n".join(lines)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    order: Order | None = None


class VoiceRequest(BaseModel):
    session_id: str
