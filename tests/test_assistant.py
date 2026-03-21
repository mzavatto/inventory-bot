"""Tests for the assistant service using mocked OpenAI calls."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from app.services.assistant import AssistantService
from app.services.catalog import CatalogService
from app.services.session import SessionService
from app.models import Product


@pytest.fixture
def mock_catalog() -> CatalogService:
    catalog = MagicMock(spec=CatalogService)
    product = Product(
        id="P001",
        name="Yerba Mate Test",
        description="Yerba de prueba",
        price=850.0,
        unit="bolsa",
        category="Yerba Mate",
        stock=100,
        promotions=[],
        tags=["yerba", "mate"],
    )
    catalog.search.return_value = [product]
    catalog.get_by_id.return_value = product
    catalog.format_product.return_value = "📦 *Yerba Mate Test*\n   💵 $850.00 por bolsa"
    catalog.list_categories.return_value = ["Yerba Mate", "Café"]
    return catalog


@pytest.fixture
def sessions() -> SessionService:
    return SessionService()


@pytest.fixture
def assistant(mock_catalog: CatalogService, sessions: SessionService) -> AssistantService:
    svc = AssistantService(catalog=mock_catalog, sessions=sessions)
    return svc


def _make_openai_response(content: str, finish_reason: str = "stop") -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = None

    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call_response(
    tool_name: str, arguments: dict, tool_call_id: str = "call_1"
) -> MagicMock:
    """Build a mock OpenAI response that calls a tool."""
    tool_call = MagicMock()
    tool_call.id = tool_call_id
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps(arguments)

    message = MagicMock()
    message.content = None
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


class TestAssistantService:
    def test_chat_returns_reply(
        self, assistant: AssistantService
    ) -> None:
        final_response = _make_openai_response("¡Hola! ¿En qué te ayudo?")
        with patch.object(
            assistant._client.chat.completions, "create", return_value=final_response
        ):
            reply = assistant.chat("session-1", "Hola")
        assert reply == "¡Hola! ¿En qué te ayudo?"

    def test_chat_stores_history(
        self, assistant: AssistantService, sessions: SessionService
    ) -> None:
        final_response = _make_openai_response("Precio: $850")
        with patch.object(
            assistant._client.chat.completions, "create", return_value=final_response
        ):
            assistant.chat("session-2", "¿Cuánto sale la yerba?")

        session = sessions.get("session-2")
        assert session is not None
        assert len(session.history) == 2
        assert session.history[0].role == "user"
        assert session.history[1].role == "assistant"

    def test_chat_with_search_tool_call(
        self, assistant: AssistantService
    ) -> None:
        tool_response = _make_tool_call_response(
            "search_products", {"query": "yerba"}, "call_1"
        )
        final_response = _make_openai_response(
            "La Yerba Mate Test cuesta $850.00 por bolsa."
        )
        with patch.object(
            assistant._client.chat.completions,
            "create",
            side_effect=[tool_response, final_response],
        ):
            reply = assistant.chat("session-3", "¿Cuánto sale la yerba?")

        assert "850" in reply

    def test_chat_add_to_order(
        self, assistant: AssistantService, sessions: SessionService
    ) -> None:
        tool_response = _make_tool_call_response(
            "add_to_order", {"product_id": "P001", "quantity": 3}, "call_2"
        )
        final_response = _make_openai_response(
            "Agregué 3 bolsas de Yerba Mate Test al pedido."
        )
        with patch.object(
            assistant._client.chat.completions,
            "create",
            side_effect=[tool_response, final_response],
        ):
            reply = assistant.chat("session-4", "Sumame 3 yerba mate")

        session = sessions.get("session-4")
        assert session is not None
        assert len(session.order.items) == 1
        assert session.order.items[0].quantity == 3
        assert session.order.total == 2550.0

    def test_tool_search_products(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        result = assistant._search_products("yerba")
        assert "Yerba Mate Test" in result
        mock_catalog.search.assert_called_once_with("yerba")

    def test_tool_add_to_order_unknown_product(
        self, assistant: AssistantService
    ) -> None:
        assistant._catalog.get_by_id.return_value = None
        session = assistant._sessions.get_or_create("s-unknown")
        result = assistant._add_to_order("INVALID", 1, session.order)
        assert "No encontré" in result

    def test_tool_add_to_order_invalid_quantity(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("s-qty")
        result = assistant._add_to_order("P001", 0, session.order)
        assert "mayor a 0" in result

    def test_tool_order_summary_empty(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("s-empty")
        summary = session.order.to_summary()
        assert "vacío" in summary.lower()

    def test_tool_clear_order(
        self, assistant: AssistantService, sessions: SessionService
    ) -> None:
        session = sessions.get_or_create("s-clear")
        from app.models import OrderItem

        session.order.add_item(
            OrderItem(
                product_id="P001",
                product_name="Yerba",
                quantity=2,
                unit_price=850.0,
            )
        )
        result = assistant._handle_tool_call("clear_order", "{}", session)
        assert "vaciado" in result.lower()
        assert session.order.total == 0.0

    def test_tool_list_categories(
        self, assistant: AssistantService
    ) -> None:
        result = assistant._list_categories()
        assert "Yerba Mate" in result
        assert "Café" in result
