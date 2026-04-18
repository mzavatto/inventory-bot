"""Tests for the assistant service using mocked OpenAI calls."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.models import OrderItem, Product
from app.services.assistant import AssistantService
from app.services.catalog import CatalogService
from app.services.session import SessionService


@pytest.fixture
def mock_catalog() -> CatalogService:
    catalog = MagicMock(spec=CatalogService)
    product = Product(
        id="38223002",
        name="Sartén Chef Terra 24cm",
        description="Sartén línea Terra",
        price=18500.0,
        price_installments_12=1850.0,
        unit="unidad",
        category="Línea Rosa",
        stock=None,
        promotions=[],
        tags=["sarten", "terra"],
    )
    other = Product(
        id="P002",
        name="Combo Bifera 33X23 + Sartén Chef",
        description="Combo destacado",
        price=42000.0,
        price_installments_12=4200.0,
        unit="combo",
        category="Destacados",
        stock=None,
        promotions=[],
        tags=["combo"],
    )

    def _get_by_id(pid: str):
        store = {"38223002": product, "P002": other}
        return store.get(pid.upper()) or store.get(pid)

    catalog.search.return_value = [product]
    catalog.get_by_id.side_effect = _get_by_id
    catalog.format_product.return_value = (
        "📦 *Sartén Chef Terra 24cm*\n   SKU `38223002` · Línea Rosa\n"
        "   💵 PSVP Lista: $18,500.00\n   💳 12 cuotas: $1,850.00"
    )
    catalog.format_product_short.side_effect = (
        lambda p: f"`{p.id}` · {p.name} — ${p.price:,.2f}"
    )
    catalog.list_categories.return_value = ["Destacados", "Línea Rosa"]
    catalog.list_by_category.return_value = [product]
    return catalog


@pytest.fixture
def sessions() -> SessionService:
    return SessionService()


@pytest.fixture
def assistant(mock_catalog: CatalogService, sessions: SessionService) -> AssistantService:
    return AssistantService(catalog=mock_catalog, sessions=sessions)


def _make_openai_response(content: str) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.tool_calls = None

    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call_response(
    tool_name: str, arguments: dict, tool_call_id: str = "call_1"
) -> MagicMock:
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


class TestAssistantConversation:
    def test_chat_returns_reply(self, assistant: AssistantService) -> None:
        final_response = _make_openai_response("Listo, ¿algo más?")
        with patch.object(
            assistant._client.chat.completions, "create", return_value=final_response
        ):
            reply = assistant.chat("session-1", "Hola")
        assert reply == "Listo, ¿algo más?"

    def test_chat_stores_history(
        self, assistant: AssistantService, sessions: SessionService
    ) -> None:
        final_response = _make_openai_response("PSVP $18.500")
        with patch.object(
            assistant._client.chat.completions, "create", return_value=final_response
        ):
            assistant.chat("session-2", "Precio del 38223002")

        session = sessions.get("session-2")
        assert session is not None
        assert len(session.history) == 2

    def test_chat_runs_tool_call(self, assistant: AssistantService) -> None:
        tool_response = _make_tool_call_response(
            "search_products", {"query": "sarten"}
        )
        final_response = _make_openai_response("Encontré 1 producto.")
        with patch.object(
            assistant._client.chat.completions,
            "create",
            side_effect=[tool_response, final_response],
        ):
            reply = assistant.chat("session-3", "buscame sartenes")
        assert "1 producto" in reply

    def test_chat_add_to_draft_updates_order(
        self, assistant: AssistantService, sessions: SessionService
    ) -> None:
        tool_response = _make_tool_call_response(
            "add_to_draft", {"identifier": "38223002", "quantity": 2}
        )
        final_response = _make_openai_response("Agregado.")
        with patch.object(
            assistant._client.chat.completions,
            "create",
            side_effect=[tool_response, final_response],
        ):
            assistant.chat("session-4", "sumá 2 sartenes 38223002")

        session = sessions.get("session-4")
        assert session is not None
        assert len(session.order.items) == 1
        assert session.order.items[0].quantity == 2
        assert session.order.total == 37000.0


class TestCatalogTools:
    def test_search_products_returns_short_listing(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        result = assistant._search_products("sarten")
        assert "Sartén Chef Terra 24cm" in result
        assert "38223002" in result
        mock_catalog.search.assert_called_once_with(
            "sarten", limit=10, category=None
        )

    def test_search_products_with_section(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        assistant._search_products("sarten", section="Línea Rosa", limit=5)
        mock_catalog.search.assert_called_once_with(
            "sarten", limit=5, category="Línea Rosa"
        )

    def test_search_products_empty_query_and_section(
        self, assistant: AssistantService
    ) -> None:
        result = assistant._search_products("")
        assert "búsqueda" in result.lower()

    def test_get_product_returns_full_card(
        self, assistant: AssistantService
    ) -> None:
        result = assistant._get_product("38223002")
        assert "PSVP Lista" in result
        assert "12 cuotas" in result

    def test_get_product_unknown(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        mock_catalog.get_by_id.side_effect = lambda _: None
        result = assistant._get_product("ZZZZ")
        assert "No encontré" in result

    def test_list_sections(self, assistant: AssistantService) -> None:
        result = assistant._list_sections()
        assert "Destacados" in result
        assert "Línea Rosa" in result

    def test_list_products_by_section(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        result = assistant._list_products_by_section("Línea Rosa", limit=20)
        assert "Línea Rosa" in result
        assert "Sartén Chef Terra 24cm" in result
        mock_catalog.list_by_category.assert_called_once_with(
            "Línea Rosa", limit=20
        )


class TestDraftTools:
    def test_add_to_draft_unknown_product(
        self, assistant: AssistantService, mock_catalog: CatalogService
    ) -> None:
        mock_catalog.get_by_id.side_effect = lambda _: None
        session = assistant._sessions.get_or_create("d1")
        result = assistant._add_to_draft("ZZZ", 1, session.order)
        assert "No encontré" in result

    def test_add_to_draft_invalid_quantity(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d2")
        result = assistant._add_to_draft("38223002", 0, session.order)
        assert "mayor a 0" in result

    def test_add_to_draft_with_price_override(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d3")
        result = assistant._add_to_draft(
            "38223002", 1, session.order, unit_price=15000.0
        )
        assert "15,000" in result or "15.000" in result
        assert session.order.items[0].unit_price == 15000.0

    def test_set_item_price(self, assistant: AssistantService) -> None:
        session = assistant._sessions.get_or_create("d4")
        assistant._add_to_draft("38223002", 1, session.order)
        result = assistant._set_item_price("38223002", 12000.0, session.order)
        assert "Precio actualizado" in result
        assert session.order.items[0].unit_price == 12000.0

    def test_set_item_price_unknown_item(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d5")
        result = assistant._set_item_price("38223002", 100.0, session.order)
        assert "no está en el pedido" in result

    def test_remove_from_draft(self, assistant: AssistantService) -> None:
        session = assistant._sessions.get_or_create("d6")
        assistant._add_to_draft("38223002", 1, session.order)
        result = assistant._remove_from_draft("38223002", session.order)
        assert "eliminado" in result
        assert session.order.items == []

    def test_set_draft_discount_percent(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d7")
        assistant._add_to_draft("38223002", 2, session.order)
        result = assistant._set_draft_discount(
            percent=10, amount=None, order=session.order
        )
        assert "10%" in result
        assert session.order.total == 33300.0

    def test_set_draft_discount_amount(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d8")
        assistant._add_to_draft("38223002", 1, session.order)
        result = assistant._set_draft_discount(
            percent=None, amount=500, order=session.order
        )
        assert "$500" in result
        assert session.order.total == 18000.0

    def test_set_draft_discount_rejects_both(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d9")
        result = assistant._set_draft_discount(
            percent=10, amount=500, order=session.order
        )
        assert "no ambos" in result

    def test_set_draft_discount_rejects_invalid_percent(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d10")
        result = assistant._set_draft_discount(
            percent=150, amount=None, order=session.order
        )
        assert "entre 0 y 100" in result

    def test_set_draft_metadata(self, assistant: AssistantService) -> None:
        session = assistant._sessions.get_or_create("d11")
        result = assistant._set_draft_metadata(
            client_name="Juan", notes="Entregar viernes", order=session.order
        )
        assert "Juan" in result
        assert session.order.client_name == "Juan"
        assert session.order.notes == "Entregar viernes"

    def test_get_draft_summary_client_format(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d12")
        assistant._add_to_draft("38223002", 1, session.order)
        session.order.client_name = "Juan"
        result = assistant._handle_tool_call(
            "get_draft_summary", json.dumps({"format": "client"}), session
        )
        assert "Juan" in result
        assert "38223002" not in result

    def test_get_draft_summary_detailed_format(
        self, assistant: AssistantService
    ) -> None:
        session = assistant._sessions.get_or_create("d13")
        assistant._add_to_draft("38223002", 1, session.order)
        result = assistant._handle_tool_call(
            "get_draft_summary", json.dumps({}), session
        )
        assert "38223002" in result

    def test_clear_draft(self, assistant: AssistantService) -> None:
        session = assistant._sessions.get_or_create("d14")
        session.order.add_item(
            OrderItem(
                product_id="P002",
                product_name="Combo",
                quantity=1,
                unit_price=42000.0,
            )
        )
        result = assistant._handle_tool_call("clear_draft", "{}", session)
        assert "vaciado" in result.lower()
        assert session.order.total == 0.0
