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
        assert "P001" in summary
        assert "1.700" in summary or "1,700" in summary
        assert "Total" in summary

    def test_set_discount_percent(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 1000.0))
        order.set_discount(percent=10)
        assert order.subtotal == 2000.0
        assert order.total == 1800.0

    def test_set_discount_amount(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 1, 5000.0))
        order.set_discount(amount=500)
        assert order.subtotal == 5000.0
        assert order.total == 4500.0

    def test_clear_discount(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 1, 1000.0))
        order.set_discount(percent=20)
        order.set_discount()
        assert order.discount_percent is None
        assert order.discount_amount is None
        assert order.total == 1000.0

    def test_update_unit_price(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 1000.0))
        ok = order.update_unit_price("P001", 800.0)
        assert ok is True
        assert order.items[0].unit_price == 800.0
        assert order.items[0].subtotal == 1600.0
        assert order.total == 1600.0

    def test_update_unit_price_nonexistent(self) -> None:
        order = Order()
        assert order.update_unit_price("ghost", 100.0) is False

    def test_to_client_summary_includes_client_name(self) -> None:
        order = Order()
        order.client_name = "Juan"
        order.add_item(make_item("P001", "Yerba", 1, 1000.0))
        summary = order.to_client_summary()
        assert "Juan" in summary
        assert "Yerba" in summary
        assert "P001" not in summary
        assert "Total" in summary

    def test_to_client_summary_with_discount(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 2, 1000.0))
        order.set_discount(percent=10)
        summary = order.to_client_summary()
        assert "10%" in summary
        assert "1.800" in summary or "1,800" in summary

    def test_clear_resets_discount_and_metadata(self) -> None:
        order = Order()
        order.add_item(make_item("P001", "Yerba", 1, 1000.0))
        order.set_discount(percent=10)
        order.client_name = "Juan"
        order.notes = "Entregar el viernes"
        order.clear()
        assert order.items == []
        assert order.total == 0.0
        assert order.subtotal == 0.0
        assert order.discount_percent is None
        assert order.client_name == ""
        assert order.notes == ""
