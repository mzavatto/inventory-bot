from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class Product(BaseModel):
    id: str
    name: str
    description: str = ""
    price: float
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
    items: list[OrderItem] = Field(default_factory=list)
    total: float = 0.0

    def recalculate_total(self) -> None:
        self.total = round(sum(item.subtotal for item in self.items), 2)

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

    def clear(self) -> None:
        self.items.clear()
        self.total = 0.0

    def to_summary(self) -> str:
        if not self.items:
            return "El pedido está vacío."
        lines = ["📋 *Resumen del pedido:*", ""]
        for item in self.items:
            lines.append(
                f"• {item.product_name} x{item.quantity} "
                f"@ ${item.unit_price:,.2f} = ${item.subtotal:,.2f}"
            )
        lines.append("")
        lines.append(f"💰 *Total: ${self.total:,.2f}*")
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
