"""
AI Assistant service powered by OpenAI with function calling.

Internal back-office assistant for sellers / shop owners. It is **not** a
customer-facing sales bot: it helps the operator look up products in the
catalog, check prices and assemble pedidos / presupuestos to send to clients.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from app.config import settings
from app.models import Order, OrderItem, Product
from app.services.catalog import CatalogService, catalog_service
from app.services.session import Session, SessionService, session_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un asistente interno para vendedores y dueños de negocio.
Tu trabajo NO es venderle a un cliente final: tu trabajo es asistir al operador
para que consulte el catálogo, encuentre productos rápido, controle precios y
arme pedidos o presupuestos para enviarle a sus clientes.

Estilo y tono:
- Hablás siempre con el vendedor, no con el cliente final.
- Tono profesional, directo y conciso. Nada de lenguaje de venta tipo
  "te conviene", "llevate", "aprovechá", etc.
- Sin emojis innecesarios. Listas cortas y datos al grano.
- Idioma: español (Argentina/Uruguay).

Reglas operativas:
- Toda la información de productos sale del catálogo cargado. Nunca inventes
  precios, SKUs ni descripciones. Si no encontrás algo, decilo.
- Cuando muestres un producto, mostrá siempre: SKU, nombre, sección/categoría,
  precio PSVP Lista y, si existe, precio en 12 cuotas.
- Cuando armes un pedido, identificá los items por SKU para evitar ambigüedad.
- Confirmá antes de modificar el pedido en curso (agregar, cambiar cantidad,
  cambiar precio unitario, aplicar descuento, vaciar).
- Si el operador pide "el resumen para mandarle al cliente", devolvé la versión
  limpia (`get_draft_summary` con `format="client"`), sin SKUs internos ni
  metadatos.
- Si hay ambigüedad (varios productos coinciden), listá las opciones con SKU
  y pedí que confirme cuál.
- Los precios siempre con símbolo $ y dos decimales.

Tenés herramientas para buscar en el catálogo, listar secciones y armar el
pedido en curso (borrador / presupuesto).
"""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Busca productos en el catálogo por nombre, SKU, descripción o "
                "tags. Opcionalmente filtra por sección/categoría. Devuelve un "
                "listado breve con SKU, nombre y precios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto libre o SKU a buscar",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Sección/categoría exacta para filtrar "
                            "(ej: 'Línea Rosa', 'Destacados')."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima de resultados (default 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": (
                "Devuelve la ficha detallada de un producto por su SKU/ID. "
                "Usar cuando el operador pide ver toda la info de un producto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "SKU o ID del producto",
                    }
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_sections",
            "description": (
                "Lista todas las secciones/categorías disponibles en el catálogo "
                "(ej: Destacados, Línea Rosa, Línea Contemporánea, etc.)."
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
            "name": "list_products_by_section",
            "description": (
                "Lista los productos de una sección específica. Útil para "
                "que el vendedor explore una línea entera."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Nombre de la sección/categoría",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima a listar (default 30)",
                    },
                },
                "required": ["section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_draft",
            "description": (
                "Agrega un producto al pedido en curso (borrador / presupuesto). "
                "Acepta SKU o ID. Permite override del precio unitario para "
                "casos de descuento puntual o precio negociado."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "SKU o ID del producto",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Cantidad a agregar",
                    },
                    "unit_price": {
                        "type": "number",
                        "description": (
                            "Precio unitario a usar (opcional). Si se omite, "
                            "se usa el PSVP Lista del catálogo."
                        ),
                    },
                },
                "required": ["identifier", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_draft_item",
            "description": (
                "Actualiza la cantidad de un item del pedido en curso. "
                "Cantidad 0 elimina el item."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string"},
                    "quantity": {"type": "integer"},
                },
                "required": ["identifier", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_item_price",
            "description": (
                "Cambia el precio unitario de un item ya cargado en el pedido."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string"},
                    "unit_price": {"type": "number"},
                },
                "required": ["identifier", "unit_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_draft",
            "description": "Elimina un item del pedido en curso.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_draft_discount",
            "description": (
                "Aplica un descuento global al pedido en curso. Pasar "
                "`percent` o `amount` (no ambos). Ambos en 0/null para limpiar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "number",
                        "description": "Descuento porcentual (0-100)",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Descuento en monto fijo",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_draft_metadata",
            "description": (
                "Setea metadatos del pedido (nombre del cliente y/o notas). "
                "Útil cuando el operador quiere personalizar el resumen final."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_draft_summary",
            "description": (
                "Devuelve el resumen del pedido en curso. Usar `format='detailed'` "
                "(default) para la vista interna con SKUs, o `format='client'` "
                "para una versión limpia lista para mandarle al cliente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["detailed", "client"],
                        "description": "Formato del resumen",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_draft",
            "description": "Vacía completamente el pedido en curso.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


class AssistantService:
    """OpenAI-powered back-office assistant for sellers / shop owners."""

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

        return "No pude procesar la consulta. Probá de nuevo."

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
                return self._search_products(
                    args.get("query", ""),
                    section=args.get("section"),
                    limit=int(args.get("limit") or 10),
                )
            if name == "get_product":
                return self._get_product(args.get("identifier", ""))
            if name == "list_sections":
                return self._list_sections()
            if name == "list_products_by_section":
                return self._list_products_by_section(
                    args.get("section", ""),
                    limit=int(args.get("limit") or 30),
                )
            if name == "add_to_draft":
                return self._add_to_draft(
                    args.get("identifier", ""),
                    int(args.get("quantity", 1)),
                    session.order,
                    unit_price=args.get("unit_price"),
                )
            if name == "update_draft_item":
                return self._update_draft_item(
                    args.get("identifier", ""),
                    int(args.get("quantity", 0)),
                    session.order,
                )
            if name == "set_item_price":
                return self._set_item_price(
                    args.get("identifier", ""),
                    float(args.get("unit_price", 0)),
                    session.order,
                )
            if name == "remove_from_draft":
                return self._remove_from_draft(
                    args.get("identifier", ""), session.order
                )
            if name == "set_draft_discount":
                return self._set_draft_discount(
                    percent=args.get("percent"),
                    amount=args.get("amount"),
                    order=session.order,
                )
            if name == "set_draft_metadata":
                return self._set_draft_metadata(
                    client_name=args.get("client_name"),
                    notes=args.get("notes"),
                    order=session.order,
                )
            if name == "get_draft_summary":
                fmt = args.get("format") or "detailed"
                if fmt == "client":
                    return session.order.to_client_summary()
                return session.order.to_summary()
            if name == "clear_draft":
                session.order.clear()
                return "Pedido vaciado."
        except Exception as exc:
            logger.exception("Error handling tool call %s: %s", name, exc)
            return f"Error al ejecutar la operación: {exc}"

        return f"Herramienta desconocida: {name}"

    # ---------- catalog tools ----------

    def _search_products(
        self, query: str, section: str | None = None, limit: int = 10
    ) -> str:
        if not query.strip() and not section:
            return "Indicá un texto de búsqueda o una sección."
        products = self._catalog.search(query, limit=limit, category=section)
        if not products:
            scope = f" en '{section}'" if section else ""
            return f"No encontré productos para '{query}'{scope}."
        header = f"Resultados ({len(products)}):"
        body = "\n".join(self._catalog.format_product_short(p) for p in products)
        return f"{header}\n{body}"

    def _get_product(self, identifier: str) -> str:
        product = self._catalog.get_by_id(identifier)
        if not product:
            return f"No encontré ningún producto con SKU/ID '{identifier}'."
        return self._catalog.format_product(product)

    def _list_sections(self) -> str:
        categories = self._catalog.list_categories()
        if not categories:
            return "El catálogo no tiene secciones cargadas."
        return "Secciones:\n" + "\n".join(f"• {c}" for c in categories)

    def _list_products_by_section(self, section: str, limit: int = 30) -> str:
        if not section.strip():
            return "Indicá la sección a listar."
        products = self._catalog.list_by_category(section, limit=limit)
        if not products:
            return f"No hay productos en la sección '{section}'."
        header = f"{section} ({len(products)} productos):"
        body = "\n".join(self._catalog.format_product_short(p) for p in products)
        return f"{header}\n{body}"

    # ---------- draft / order tools ----------

    def _resolve_product(self, identifier: str) -> Product | None:
        return self._catalog.get_by_id(identifier)

    def _add_to_draft(
        self,
        identifier: str,
        quantity: int,
        order: Order,
        unit_price: float | None = None,
    ) -> str:
        product = self._resolve_product(identifier)
        if not product:
            return f"No encontré ningún producto con SKU/ID '{identifier}'."
        if quantity <= 0:
            return "La cantidad debe ser mayor a 0."
        price = float(unit_price) if unit_price is not None else product.price
        if price < 0:
            return "El precio unitario no puede ser negativo."
        item = OrderItem(
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=price,
        )
        order.add_item(item)
        return (
            f"Agregado: [{product.id}] {product.name} x{quantity} "
            f"@ ${price:,.2f}. Total del pedido: ${order.total:,.2f}"
        )

    def _update_draft_item(
        self, identifier: str, quantity: int, order: Order
    ) -> str:
        product_id = self._resolve_id(identifier, order)
        updated = order.update_quantity(product_id, quantity)
        if not updated:
            return f"El item '{identifier}' no está en el pedido."
        if quantity <= 0:
            return f"Item '{identifier}' eliminado. Total: ${order.total:,.2f}"
        return f"Cantidad actualizada a {quantity}. Total: ${order.total:,.2f}"

    def _set_item_price(
        self, identifier: str, unit_price: float, order: Order
    ) -> str:
        if unit_price < 0:
            return "El precio unitario no puede ser negativo."
        product_id = self._resolve_id(identifier, order)
        updated = order.update_unit_price(product_id, unit_price)
        if not updated:
            return f"El item '{identifier}' no está en el pedido."
        return (
            f"Precio actualizado a ${unit_price:,.2f}. "
            f"Total: ${order.total:,.2f}"
        )

    def _remove_from_draft(self, identifier: str, order: Order) -> str:
        product_id = self._resolve_id(identifier, order)
        removed = order.remove_item(product_id)
        if not removed:
            return f"El item '{identifier}' no estaba en el pedido."
        return f"Item eliminado. Total: ${order.total:,.2f}"

    def _set_draft_discount(
        self,
        percent: float | None,
        amount: float | None,
        order: Order,
    ) -> str:
        percent_val = float(percent) if percent not in (None, 0) else None
        amount_val = float(amount) if amount not in (None, 0) else None
        if percent_val is not None and amount_val is not None:
            return "Indicá descuento por porcentaje o por monto, no ambos."
        if percent_val is not None and (percent_val < 0 or percent_val > 100):
            return "El descuento porcentual debe estar entre 0 y 100."
        order.set_discount(percent=percent_val, amount=amount_val)
        if percent_val is None and amount_val is None:
            return f"Descuento removido. Total: ${order.total:,.2f}"
        label = f"{percent_val:g}%" if percent_val else f"${amount_val:,.2f}"
        return (
            f"Descuento {label} aplicado. Subtotal: ${order.subtotal:,.2f} · "
            f"Total: ${order.total:,.2f}"
        )

    def _set_draft_metadata(
        self,
        client_name: str | None,
        notes: str | None,
        order: Order,
    ) -> str:
        changes: list[str] = []
        if client_name is not None:
            order.client_name = client_name.strip()
            changes.append(f"cliente='{order.client_name}'")
        if notes is not None:
            order.notes = notes.strip()
            changes.append("notas actualizadas")
        if not changes:
            return "No se recibieron cambios."
        return "Pedido actualizado: " + ", ".join(changes) + "."

    def _resolve_id(self, identifier: str, order: Order) -> str:
        """Match identifier to a product_id already in the draft (case-insensitive)."""
        ident = identifier.strip().upper()
        for item in order.items:
            if item.product_id.upper() == ident:
                return item.product_id
        return identifier


assistant_service = AssistantService()
