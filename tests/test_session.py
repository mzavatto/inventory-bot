"""Tests for the session service."""
from __future__ import annotations

import pytest
from app.services.session import SessionService
from app.models import Order


class TestSessionService:
    def setup_method(self) -> None:
        self.service = SessionService()

    def test_create_new_session(self) -> None:
        session = self.service.create_new()
        assert session.session_id is not None
        assert session.history == []
        assert isinstance(session.order, Order)

    def test_get_or_create_returns_same_session(self) -> None:
        s1 = self.service.get_or_create("test-session")
        s2 = self.service.get_or_create("test-session")
        assert s1 is s2

    def test_get_existing_session(self) -> None:
        self.service.get_or_create("my-session")
        assert self.service.get("my-session") is not None

    def test_get_nonexistent_session(self) -> None:
        assert self.service.get("does-not-exist") is None

    def test_delete_session(self) -> None:
        self.service.get_or_create("to-delete")
        result = self.service.delete("to-delete")
        assert result is True
        assert self.service.get("to-delete") is None

    def test_delete_nonexistent_session(self) -> None:
        assert self.service.delete("ghost") is False

    def test_reset_order(self) -> None:
        session = self.service.get_or_create("order-session")
        from app.models import OrderItem

        session.order.add_item(
            OrderItem(
                product_id="P001",
                product_name="Yerba",
                quantity=2,
                unit_price=850.0,
            )
        )
        assert session.order.total > 0

        result = self.service.reset_order("order-session")
        assert result is True
        assert session.order.total == 0.0
        assert session.order.items == []

    def test_reset_order_nonexistent_session(self) -> None:
        assert self.service.reset_order("ghost") is False

    def test_add_message_to_session(self) -> None:
        session = self.service.get_or_create("msg-session")
        session.add_message("user", "Hola!")
        session.add_message("assistant", "¡Hola! ¿En qué te ayudo?")
        assert len(session.history) == 2
        history = session.get_history()
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "¡Hola! ¿En qué te ayudo?"
