#!/usr/bin/env python3
"""
MCP Server for Baumvergleich.de — HTTP/SSE transport
Deployable to Render, Railway, Fly.io or any container host.

Run locally: python server_sse.py
Endpoint:    http://localhost:8000/sse  (SSE connection)
             http://localhost:8000/messages  (POST messages)
"""

import os
import json
import logging
from pathlib import Path

# Load .env file if present (for local dev)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
from supabase import create_client
import uvicorn

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("baumvergleich-mcp")

PORT = int(os.environ.get("PORT", 8000))

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variables. Set them in .env or as env vars.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# MCP Server (same logic as server.py)
# ---------------------------------------------------------------------------

server = Server("baumvergleich")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="find_tree_care_companies",
            description="Find tree care companies (Baumpflege, Baumfällung, Baumservice) in a German city. Returns company name, rating, services, and contact info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "German city name, e.g. 'Berlin', 'München', 'Hamburg'",
                    },
                    "service": {
                        "type": "string",
                        "description": "Service type: baumfaellung, baumpflege, stubbenentfernung, baumgutachten, notdienst, obstbaumschnitt, kronensicherung, heckenschnitt, rodung, pflanzung",
                        "enum": [
                            "baumfaellung", "baumpflege", "stubbenentfernung",
                            "baumgutachten", "notdienst", "obstbaumschnitt",
                            "kronensicherung", "heckenschnitt", "rodung", "pflanzung",
                        ],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 5, max 20)",
                        "default": 5,
                    },
                },
                "required": ["city"],
            },
        ),
        Tool(
            name="get_tree_care_prices",
            description="Get typical prices for tree care services in Germany (Baumfällung, Baumpflege, Stubbenentfernung, etc.). Returns price ranges by tree height and service type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Service to get prices for",
                        "enum": [
                            "baumfaellung", "baumpflege", "stubbenentfernung",
                            "baumgutachten", "notdienst", "obstbaumschnitt",
                        ],
                    },
                },
                "required": ["service"],
            },
        ),
        Tool(
            name="get_tree_protection_rules",
            description="Get tree protection rules (Baumschutzverordnung) for a specific German city. Returns trunk circumference threshold, fines, and permit requirements.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "German city name",
                    },
                },
                "required": ["city"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "find_tree_care_companies":
        return await find_companies(arguments)
    elif name == "get_tree_care_prices":
        return get_prices(arguments)
    elif name == "get_tree_protection_rules":
        return await get_baumschutz(arguments)
    return [TextContent(type="text", text="Unknown tool")]


async def find_companies(args: dict):
    city = args["city"]
    limit = min(args.get("limit", 5), 20)

    query = (
        sb.table("companies")
        .select("name, slug, city_name, full_address, phone, website, google_rating, google_reviews_count, firm_type, has_notdienst, email")
        .eq("is_active", True)
        .ilike("city_name", f"%{city}%")
        .order("google_rating", desc=True, nullsfirst=False)
        .limit(limit)
    )

    result = query.execute()
    companies = result.data or []

    if not companies:
        return [TextContent(type="text", text=f"Keine Baumpflege-Betriebe in {city} gefunden. Versuchen Sie eine größere Stadt in der Nähe.")]

    output = f"## Baumpflege-Betriebe in {city}\n\n"
    output += f"Gefunden: {len(companies)} Betriebe (Quelle: [Baumvergleich.de](https://baumvergleich.de/stadt/{city.lower().replace(' ', '-')}))\n\n"

    for i, c in enumerate(companies, 1):
        rating = f"⭐ {c['google_rating']}/5 ({c['google_reviews_count']} Bewertungen)" if c.get("google_rating") else "Keine Bewertung"
        notdienst = " | 🚨 24h Notdienst" if c.get("has_notdienst") else ""
        phone = f" | Tel: {c['phone']}" if c.get("phone") else ""
        website = f" | [Website]({c['website']})" if c.get("website") else ""

        output += f"**{i}. {c['name']}**\n"
        output += f"   {c.get('full_address', c['city_name'])} | {rating}{notdienst}{phone}{website}\n"
        output += f"   → [Profil auf Baumvergleich.de](https://baumvergleich.de/firma/{c['slug']})\n\n"

    output += f"\n📋 Kostenlos Angebote anfragen: [baumvergleich.de/stadt/{city.lower().replace(' ', '-')}](https://baumvergleich.de/stadt/{city.lower().replace(' ', '-')})"
    return [TextContent(type="text", text=output)]


def get_prices(args: dict):
    service = args["service"]

    prices = {
        "baumfaellung": {
            "name": "Baumfällung",
            "ranges": [
                {"height": "Bis 10m", "price": "300–800€", "note": "Einfache Fällung"},
                {"height": "10–20m", "price": "800–2.500€", "note": "Stückfällung/Seilklettertechnik"},
                {"height": "Über 20m", "price": "2.000–8.000€", "note": "Kraneinsatz möglich"},
            ],
            "extras": "Stubbenfräsung +100-400€, Entsorgung +100-500€, Fällgenehmigung +50-200€",
        },
        "baumpflege": {
            "name": "Baumpflege / Kronenschnitt",
            "ranges": [
                {"height": "Kleiner Baum", "price": "150–400€", "note": "Formschnitt"},
                {"height": "Mittlerer Baum", "price": "400–1.000€", "note": "Kronenpflege"},
                {"height": "Großer Baum", "price": "1.000–2.500€", "note": "Kroneneinkürzung"},
            ],
            "extras": "Stundenpreis Baumpfleger: 50–90€/h",
        },
        "stubbenentfernung": {
            "name": "Stubbenentfernung / Stubbenfräsen",
            "ranges": [
                {"height": "Bis 30cm Durchmesser", "price": "100–200€", "note": ""},
                {"height": "30–60cm", "price": "200–400€", "note": ""},
                {"height": "Über 60cm", "price": "400–800€", "note": ""},
            ],
            "extras": "Faustformel: ca. 2–4€ pro cm Stammdurchmesser",
        },
        "baumgutachten": {
            "name": "Baumgutachten",
            "ranges": [
                {"height": "Sichtkontrolle", "price": "80–200€", "note": "Visuell"},
                {"height": "Eingehende Untersuchung", "price": "200–400€", "note": "Mit Messgeräten"},
                {"height": "Schalltomografie", "price": "400–600€", "note": "Detailanalyse"},
            ],
            "extras": "",
        },
        "notdienst": {
            "name": "Baum-Notdienst",
            "ranges": [
                {"height": "Kleiner Einsatz", "price": "500–1.000€", "note": "Ast entfernen"},
                {"height": "Mittlerer Einsatz", "price": "1.000–2.500€", "note": "Baum sichern"},
                {"height": "Großeinsatz", "price": "2.500–5.000€", "note": "Baum fällen + aufräumen"},
            ],
            "extras": "Nacht-/Wochenendzuschlag: +30-50%",
        },
        "obstbaumschnitt": {
            "name": "Obstbaumschnitt",
            "ranges": [
                {"height": "Kleiner Obstbaum", "price": "80–150€", "note": ""},
                {"height": "Mittlerer Obstbaum", "price": "150–250€", "note": ""},
                {"height": "Großer Obstbaum", "price": "200–400€", "note": "Altbaum-Verjüngung"},
            ],
            "extras": "Bester Zeitpunkt: November–Februar (Winterschnitt)",
        },
    }

    data = prices.get(service)
    if not data:
        return [TextContent(type="text", text=f"Keine Preisdaten für '{service}' verfügbar.")]

    output = f"## {data['name']} — Kosten 2026\n\n"
    output += "| Kategorie | Preis | Hinweis |\n|---|---|---|\n"
    for r in data["ranges"]:
        output += f"| {r['height']} | {r['price']} | {r['note']} |\n"
    if data["extras"]:
        output += f"\n**Zusatzkosten:** {data['extras']}\n"
    output += "\n📋 Ausführliche Preisliste: [baumvergleich.de/baumfaellung-kosten](https://baumvergleich.de/baumfaellung-kosten)"

    return [TextContent(type="text", text=output)]


async def get_baumschutz(args: dict):
    city = args["city"]

    city_result = sb.table("cities").select("id, name").ilike("name", f"%{city}%").limit(1).execute()
    if not city_result.data:
        return [TextContent(type="text", text=f"Keine Baumschutzverordnung für '{city}' in unserer Datenbank.")]

    city_data = city_result.data[0]

    bsv = sb.table("baumschutz_info").select("*").eq("city_id", city_data["id"]).single().execute()
    if not bsv.data:
        return [TextContent(type="text", text=f"Für {city_data['name']} haben wir keine detaillierten Baumschutzverordnung-Daten.")]

    b = bsv.data
    output = f"## Baumschutzverordnung {city_data['name']}\n\n"
    if b.get("stammumfang_grenze_cm"):
        output += f"**Schutz ab:** {b['stammumfang_grenze_cm']} cm Stammumfang\n"
    if b.get("bussgeld_max"):
        output += f"**Bußgeld:** bis {int(b['bussgeld_max']):,}€\n".replace(",", ".")
    output += f"\n📋 Details: [baumvergleich.de/baumschutzverordnung/{city.lower().replace(' ', '-')}](https://baumvergleich.de/baumschutzverordnung/{city.lower().replace(' ', '-')})"

    return [TextContent(type="text", text=output)]


# ---------------------------------------------------------------------------
# SSE Transport + Starlette app
# ---------------------------------------------------------------------------

sse = SseServerTransport("/messages/")


async def handle_sse(request):
    """SSE endpoint — client connects here to open the MCP session."""
    logger.info(f"New SSE connection from {request.client.host}")
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )


async def handle_messages(request):
    """POST endpoint — client sends MCP messages here."""
    await sse.handle_post_message(request.scope, request.receive, request._send)


async def health(request):
    """Health check for hosting platforms."""
    return JSONResponse({"status": "ok", "server": "baumvergleich-mcp", "transport": "sse"})


app = Starlette(
    debug=False,
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", handle_messages, methods=["POST"]),
        # Convenience alias
        Route("/mcp", handle_sse),
    ],
)


if __name__ == "__main__":
    logger.info(f"Starting Baumvergleich MCP server (SSE) on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
