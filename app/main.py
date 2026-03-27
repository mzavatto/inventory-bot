"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.catalog import router as catalog_router
from app.api.chat import router as chat_router
from app.api.whatsapp import router as whatsapp_router
from app.admin.routes import router as admin_api_router
from app.admin.pages import router as admin_pages_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Inventory Bot – Sales Assistant",
    description=(
        "Asistente conversacional para vendedores. "
        "Permite consultar catálogos, gestionar pedidos y recibir mensajes "
        "por WhatsApp o API REST. Incluye interfaz de administración para "
        "importar y actualizar el catálogo."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chat and catalog API routes
app.include_router(chat_router)
app.include_router(catalog_router)
app.include_router(whatsapp_router)

# Admin routes
app.include_router(admin_api_router)
app.include_router(admin_pages_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}
