"""
AI Assistant service powered by OpenAI with function calling.

Handles natural language understanding, product queries and order management.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from app.config import settings
from app.models import Order, OrderItem
from app.services.catalog import CatalogService, catalog_service
from app.services.session import Session, SessionService, session_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un asistente de ventas inteligente para vendedores. Tu trabajo es:
- Responder preguntas sobre productos del catálogo (precios, stock, promociones, descripciones)
- Ayudar a armar pedidos mediante lenguaje natural
- Calcular totales y subtotales correctamente
- Generar resúmenes de pedido claros y prolijos

Reglas importantes:
- Solo informás sobre productos que existen en el catálogo. Si no encontrás el producto, lo decís claramente.
- Nunca inventás precios ni información.
- Mantenés el contexto de la conversación: recordás el último producto mencionado, cantidades, etc.
- Respondés de forma breve, clara y profesional, con tono cercano orientado a ventas.
- Usás el idioma español (Argentina/Uruguay), con términos como "llevás", "sumás", "te conviene", etc.
- Siempre confirmás las acciones importantes (agregar al pedido, modificar cantidades).
- Si hay ambigüedad, preguntás para aclarar.
- Los precios se muestran con el símbolo $ y dos decimales.

Tenés acceso a herramientas para buscar productos y gestionar el pedido en curso.
"""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Busca productos en el catálogo por nombre, categoría o descripción. "
                "Usar cuando el usuario pregunte por un producto o quiera saber el precio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto de búsqueda (nombre, categoría, descripción)",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_by_id",
            "description": "Obtiene un producto específico por su ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "ID del producto (ej: P001)",
                    }
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_order",
            "description": (
                "Agrega un producto al pedido en curso. "
                "Usar cuando el usuario quiera agregar un producto al pedido."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "ID del producto a agregar",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Cantidad a agregar",
                    },
                },
                "required": ["product_id", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_order_item",
            "description": (
                "Actualiza la cantidad de un producto en el pedido. "
                "Usar cuando el usuario quiera modificar una cantidad."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "ID del producto a actualizar",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Nueva cantidad (0 para eliminar del pedido)",
                    },
                },
                "required": ["product_id", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_order",
            "description": "Elimina un producto del pedido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "ID del producto a eliminar",
                    }
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_summary",
            "description": (
                "Devuelve el resumen actual del pedido con todos los productos, "
                "cantidades y total. Usar cuando el usuario pida ver el pedido."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_order",
            "description": "Vacía el pedido actual. Usar cuando el usuario quiera empezar de cero.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "Lista las categorías de productos disponibles en el catálogo.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


class AssistantService:
    """OpenAI-powered assistant with tool calling for catalog + order management."""

    def __init__(
        self,
        catalog: CatalogService = catalog_service,
        sessions: SessionService = session_service,
    ) -> None:
        self._catalog = catalog
        self._sessions = sessions
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    def chat(self, session_id: str, user_message: str) -> str:
        """Process a user message and return the assistant's reply."""
        session = self._sessions.get_or_create(session_id)
        session.add_message("user", user_message)

        reply = self._run_conversation(session)

        session.add_message("assistant", reply)
        return reply

    def _run_conversation(self, session: Session) -> str:
        """Run the LLM conversation loop with tool calling."""
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *session.get_history(),
        ]

        max_rounds = 5
        for _ in range(max_rounds):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                messages.append(choice.message)
                for tool_call in choice.message.tool_calls:
                    result = self._handle_tool_call(
                        tool_call.function.name,
                        tool_call.function.arguments,
                        session,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
            else:
                return choice.message.content or ""

        return "Lo siento, no pude procesar tu consulta. Por favor intentá de nuevo."

    def _handle_tool_call(
        self, name: str, arguments_json: str, session: Session
    ) -> str:
        """Dispatch a tool call and return the result as a string."""
        try:
            args: dict[str, Any] = json.loads(arguments_json)
        except json.JSONDecodeError:
            args = {}

        try:
            if name == "search_products":
                return self._search_products(args.get("query", ""))
            if name == "get_product_by_id":
                return self._get_product_by_id(args.get("product_id", ""))
            if name == "add_to_order":
                return self._add_to_order(
                    args.get("product_id", ""),
                    args.get("quantity", 1),
                    session.order,
                )
            if name == "update_order_item":
                return self._update_order_item(
                    args.get("product_id", ""),
                    args.get("quantity", 0),
                    session.order,
                )
            if name == "remove_from_order":
                return self._remove_from_order(
                    args.get("product_id", ""), session.order
                )
            if name == "get_order_summary":
                return session.order.to_summary()
            if name == "clear_order":
                session.order.clear()
                return "Pedido vaciado."
            if name == "list_categories":
                return self._list_categories()
        except Exception as exc:
            logger.exception("Error handling tool call %s: %s", name, exc)
            return f"Error al ejecutar la operación: {exc}"

        return f"Herramienta desconocida: {name}"

    def _search_products(self, query: str) -> str:
        products = self._catalog.search(query)
        if not products:
            return f"No encontré productos para '{query}'."
        parts = [self._catalog.format_product(p) for p in products]
        return "\n\n".join(parts)

    def _get_product_by_id(self, product_id: str) -> str:
        product = self._catalog.get_by_id(product_id)
        if not product:
            return f"No encontré el producto con ID '{product_id}'."
        return self._catalog.format_product(product)

    def _add_to_order(self, product_id: str, quantity: int, order: Order) -> str:
        product = self._catalog.get_by_id(product_id)
        if not product:
            return f"No encontré el producto con ID '{product_id}'."
        if quantity <= 0:
            return "La cantidad debe ser mayor a 0."
        item = OrderItem(
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
        )
        order.add_item(item)
        return (
            f"Agregado: {quantity} x {product.name} @ ${product.price:,.2f}. "
            f"Total del pedido: ${order.total:,.2f}"
        )

    def _update_order_item(
        self, product_id: str, quantity: int, order: Order
    ) -> str:
        updated = order.update_quantity(product_id, quantity)
        if not updated:
            return f"El producto '{product_id}' no está en el pedido."
        if quantity <= 0:
            return f"Producto '{product_id}' eliminado del pedido. Total: ${order.total:,.2f}"
        return f"Cantidad actualizada a {quantity}. Total: ${order.total:,.2f}"

    def _remove_from_order(self, product_id: str, order: Order) -> str:
        removed = order.remove_item(product_id)
        if not removed:
            return f"El producto '{product_id}' no estaba en el pedido."
        return f"Producto eliminado. Total: ${order.total:,.2f}"

    def _list_categories(self) -> str:
        categories = self._catalog.list_categories()
        if not categories:
            return "No hay categorías disponibles."
        return "Categorías disponibles:\n" + "\n".join(f"• {c}" for c in categories)


assistant_service = AssistantService()
