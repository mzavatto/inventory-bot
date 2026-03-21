"""Tests for Order and OrderItem models."""
from __future__ import annotations

import pytest
from app.models import Order, OrderItem


def make_item(product_id: str, name: str, qty: int, price: float) -> OrderItem:
    return OrderItem(
        product_id=product_id,
        product_name=name,
        quantity=qty,
        unit_price=price,
    )


class TestOrderItem:
    def test_subtotal_calculated_on_creation(self) -> None:
        item = make_item("P001", "Yerba", 3, 850.0)
        assert item.subtotal == 2550.0

    def test_subtotal_rounds_correctly(self) -> None:
        item = make_item("P001", "Product", 3, 0.333)
        assert item.subtotal == 1.0


class TestOrder:
    def test_new_order_is_empty(self) -> None:
        order = Order()
        assert order.items == []
        assert order.total == 0.0

    def test_add_item(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        assert len(order.items) == 1
        assert order.total == 1700.0

    def test_add_same_item_increases_quantity(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        order.add_item(make_item("P001", "Yerba", 3, 850.0))
        assert len(order.items) == 1
        assert order.items[0].quantity == 5
        assert order.total == 4250.0

    def test_add_different_items(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 1, 850.0))
        order.add_item(make_item("P002", "Café", 2, 1200.0))
        assert len(order.items) == 2
        assert order.total == 850.0 + 2400.0

    def test_remove_existing_item(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        removed = order.remove_item("P001")
        assert removed is True
        assert order.items == []
        assert order.total == 0.0

    def test_remove_nonexistent_item(self) -> None:
        order = Order()
        assert order.remove_item("XXXX") is False

    def test_update_quantity(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        updated = order.update_quantity("P001", 5)
        assert updated is True
        assert order.items[0].quantity == 5
        assert order.total == 4250.0

    def test_update_quantity_to_zero_removes_item(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        order.update_quantity("P001", 0)
        assert order.items == []
        assert order.total == 0.0

    def test_update_nonexistent_item(self) -> None:
        order = Order()
        assert order.update_quantity("XXXX", 3) is False

    def test_clear_order(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 850.0))
        order.add_item(make_item("P002", "Café", 1, 1200.0))
        order.clear()
        assert order.items == []
        assert order.total == 0.0

    def test_to_summary_empty(self) -> None:
        order = Order()
        summary = order.to_summary()
        assert "vacío" in summary.lower()

    def test_to_summary_with_items(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba Mate", 2, 850.0))
        summary = order.to_summary()
        assert "Yerba Mate" in summary
        assert "1.700" in summary or "1,700" in summary
        assert "Total" in summary
