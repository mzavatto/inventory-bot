# inventory-bot – Asistente de Ventas Conversacional

Un asistente conversacional inteligente para vendedores, capaz de consultar catálogos de productos, gestionar pedidos en tiempo real y mantener el contexto de la conversación. Integra WhatsApp vía Twilio y soporta mensajes de voz mediante Whisper.

---

## Características

| Capacidad | Descripción |
|-----------|-------------|
| 🔍 **Consulta de productos** | Precios, stock, descripciones y promociones por lenguaje natural |
| 🛒 **Gestión de pedidos** | Agregar, modificar y eliminar productos con cálculo automático de totales |
| 📋 **Resumen de orden** | Genera resúmenes claros con lista de productos, cantidades y total |
| 🧠 **Contexto conversacional** | Recuerda la conversación completa y permite repreguntas encadenadas |
| 📱 **WhatsApp** | Integración con Twilio para recibir y responder mensajes de WhatsApp |
| 🎙️ **Voz** | Transcripción de mensajes de audio con OpenAI Whisper |
| 🌐 **API REST** | Endpoints para integrar con cualquier frontend o chatbot |

---

## Arquitectura

```
inventory-bot/
├── app/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Configuración (env vars)
│   ├── models.py            # Modelos Pydantic (Product, Order, Chat)
│   ├── api/
│   │   ├── chat.py          # Endpoints de chat (texto + voz)
│   │   ├── catalog.py       # Endpoints del catálogo
│   │   └── whatsapp.py      # Webhook de WhatsApp (Twilio)
│   ├── services/
│   │   ├── assistant.py     # Asistente IA (OpenAI + function calling)
│   │   ├── catalog.py       # Carga y búsqueda del catálogo
│   │   └── session.py       # Gestión de sesiones y contexto
│   └── data/
│       └── catalog.json     # Catálogo de productos (editable)
├── tests/                   # Tests unitarios
├── requirements.txt
├── pyproject.toml
├── run.py                   # Script de inicio
└── .env.example
```

---

## Instalación y configuración

### 1. Requisitos previos
- Python 3.11+
- Una clave de API de OpenAI

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

Copiar el archivo de ejemplo y completar los valores:

```bash
cp .env.example .env
```

Editar `.env`:

```env
# Requerido
OPENAI_API_KEY=sk-tu-clave-aqui

# Opcional (por defecto: gpt-4o-mini)
OPENAI_MODEL=gpt-4o-mini

# Opcional: WhatsApp con Twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

### 4. Iniciar el servidor

```bash
python run.py
```

O directamente con uvicorn:

```bash
uvicorn app.main:app --reload
```

El servidor queda disponible en `http://localhost:8000`.

La documentación interactiva de la API (Swagger) está en `http://localhost:8000/docs`.

---

## Uso de la API

### Chat por texto

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "vendedor-001",
    "message": "¿Cuánto sale la yerba mate Canarias?"
  }'
```

**Respuesta:**
```json
{
  "session_id": "vendedor-001",
  "reply": "La Yerba Mate Canarias 500g cuesta $850.00 por bolsa...",
  "order": { "items": [], "total": 0.0 }
}
```

### Agregar al pedido

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "vendedor-001",
    "message": "Sumame 3 bolsas de la Canarias 500g"
  }'
```

### Ver resumen del pedido

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "vendedor-001",
    "message": "Mostrá el pedido"
  }'
```

### Chat por voz

```bash
curl -X POST "http://localhost:8000/chat/voice?session_id=vendedor-001" \
  -F "audio=@mensaje.ogg"
```

### Catálogo de productos

```bash
# Listar todos los productos
curl http://localhost:8000/catalog

# Buscar productos
curl "http://localhost:8000/catalog/search?q=yerba"

# Ver un producto específico
curl http://localhost:8000/catalog/P001
```

---

## Integración con WhatsApp

### Configuración con Twilio

1. Crear una cuenta en [Twilio](https://www.twilio.com) y configurar un número de WhatsApp (sandbox o producción).
2. Configurar las credenciales de Twilio en `.env`.
3. Exponer el servidor públicamente (usar [ngrok](https://ngrok.com) para desarrollo):
   ```bash
   ngrok http 8000
   ```
4. Configurar la URL del webhook en Twilio:
   ```
   https://tu-dominio.ngrok.io/whatsapp/webhook
   ```

A partir de ese momento, cada mensaje de WhatsApp que reciba el número configurado llegará al asistente. El número de teléfono del remitente se usa como `session_id`, por lo que cada conversación mantiene su propio contexto.

### Mensajes de voz en WhatsApp

Los mensajes de voz enviados por WhatsApp son automáticamente transcritos con Whisper y procesados como texto.

---

## Catálogo de productos

El catálogo se encuentra en `app/data/catalog.json`. Para agregar o modificar productos, editar ese archivo con el siguiente formato:

```json
{
  "id": "P011",
  "name": "Nombre del producto",
  "description": "Descripción detallada",
  "price": 1500.0,
  "unit": "unidad",
  "category": "Categoría",
  "stock": 100,
  "promotions": [
    {
      "description": "10% de descuento por 6 o más unidades",
      "discount_percent": 10,
      "min_quantity": 6,
      "conditions": "Mínimo 6 unidades"
    }
  ],
  "tags": ["etiqueta1", "etiqueta2"]
}
```

---

## Tests

```bash
python -m pytest tests/ -v
```

---

## Ejemplos de conversación

```
Vendedor: ¿Cuánto sale la yerba Canarias?
Bot: La Yerba Mate Canarias 500g cuesta $850.00 por bolsa. Si llevás 10 o más bolsas, tenés un 15% de descuento.

Vendedor: ¿Y con descuento?
Bot: Con el 15% de descuento, cada bolsa saldría $722.50. Para 10 bolsas serían $7.225,00 en total.

Vendedor: Sumame 5 de esas y 2 termos Stanley
Bot: Agregué al pedido:
• 5 x Yerba Mate Canarias 500g @ $850.00 = $4.250,00
• 2 x Termo Stanley 1L @ $8.500,00 = $17.000,00
Total actual: $21.250,00

Vendedor: No, eran 3 de la yerba
Bot: Actualicé la cantidad de Yerba Mate Canarias 500g a 3 unidades.
Total actualizado: $19.750,00

Vendedor: Mostrame el pedido
Bot: 📋 Resumen del pedido:
• Yerba Mate Canarias 500g x3 @ $850,00 = $2.550,00
• Termo Stanley 1L x2 @ $8.500,00 = $17.000,00

💰 Total: $19.550,00
```
