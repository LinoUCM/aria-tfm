"""
Diagnostico puntual: por que RB-002 (Redis) se cita en C1 (payment-service,
sin cobertura directa) incluso tras el fix del umbral 70.

Hipotesis a confirmar: _rag_node construye la query de busqueda semantica a
partir del PROMPT COMPLETO de analyze_incident() (incluye literalmente el
bloque "SYSTEM TOPOLOGY NODES" con los 6 nombres de servicio, entre ellos
"redis-cache", mas las instrucciones del JSON de salida) en vez de solo el
titulo/descripcion del incidente. Si RB-002 puntua alto con la query
completa pero bajo con una query "limpia" (solo titulo+descripcion), el
problema no es el umbral (ya arreglado para Tier E) sino que la query de
retrieval esta contaminada con texto que no es parte del incidente real.

Uso (desde backend/, mismo venv que usa uvicorn):
    export ARIA_EVAL_USERNAME="tu_usuario_o_email"
    export ARIA_EVAL_PASSWORD="tu_contraseña"
    python3 diagnose_rag_c1.py <incident_id>

<incident_id> es el id de una de las ejecuciones C1 ya lanzadas, p.ej.
84a39047-c804-4d08-ab36-64a87b7c392f (del CSV que ya tienes).
"""
import asyncio
import json
import os
import sys

import httpx

BASE_URL = "http://localhost:8000"


async def main():
    if len(sys.argv) < 2:
        print("Uso: python3 diagnose_rag_c1.py <incident_id>")
        return
    incident_id = sys.argv[1]

    username = os.environ.get("ARIA_EVAL_USERNAME")
    password = os.environ.get("ARIA_EVAL_PASSWORD")
    if not username or not password:
        print("[ERROR] Define ARIA_EVAL_USERNAME y ARIA_EVAL_PASSWORD.")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/auth/login",
            data={"username": username, "password": password},
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"{BASE_URL}/incidents", headers=headers)
        resp.raise_for_status()
        incidents = resp.json()
        incidents = incidents if isinstance(incidents, list) else incidents.get("incidents", [])
        inc = next((i for i in incidents if i["id"] == incident_id), None)
        if not inc:
            print(f"[ERROR] No encuentro el incidente {incident_id} en /incidents "
                  "(¿está entre los últimos 50? sube 'limit' si hace falta).")
            return

    title = inc["title"]
    text = inc["description"]
    metrics = inc.get("metrics", {})
    tags = inc.get("tags", [])
    service = next(
        (t.split("service:")[1] for t in tags if t.startswith("service:")),
        "unknown"
    )

    # Query 1: EXACTAMENTE la que usa _rag_node hoy (prompt completo de
    # analyze_incident, incluyendo el bloque de topologia y las instrucciones
    # del JSON de salida) — copiado literal de agents/orchestrator.py.
    full_query = f"""Datadog Alert Received:
Title: {title}
Description: {text}
Reported Service: {service}
Metrics: {json.dumps(metrics, indent=2)}

SYSTEM TOPOLOGY NODES:
- api-gateway
- payment-service
- checkout-service
- aria_db
- redis-cache
- notification-service

Please analyze this alert and provide:
1. Likely root cause
2. Immediate actions to take
3. Relevant runbooks or past incidents

CRITICAL REQUIREMENT:
At the very end of your response, you MUST append a JSON code block identifying
the root cause and affected service.

If, and ONLY if, the root cause clearly belongs to one of the SYSTEM TOPOLOGY
NODES listed above, use that exact node_id for both fields.

If the root cause is external to our system (e.g., a third-party vendor, CDN,
DNS provider, external API, or any dependency NOT listed in SYSTEM TOPOLOGY
NODES), you MUST use the literal value "external" for both fields instead of
guessing or forcing an internal node. Do not force a match to an internal node
when the evidence points outside our system.

Additionally, if — and ONLY if — one of the following pre-approved automated
remediation actions would directly and safely resolve this specific incident,
include its exact action_id as "suggested_action". If no listed action
clearly applies, or you are not confident it is the correct fix, use the
literal value null. Do not force a match.

- "RESTART_CONTAINER": restarting a specific hung or crashed Docker container.
- "TERMINATE_IDLE_CONNECTIONS": terminating idle/stale database connections
  that are exhausting the connection pool.
- "FLUSH_REDIS_CACHE": purging a corrupted or bloated Redis cache."""

    # Query 2: "limpia" — solo lo que realmente describe el incidente,
    # sin el boilerplate de topologia ni las instrucciones del JSON.
    clean_query = f"{title} {text} Reported Service: {service} Metrics: {json.dumps(metrics)}"

    from services.indexing_service import get_indexing_service
    indexer = get_indexing_service()

    print("=== Query COMPLETA (la que usa _rag_node hoy, con bloque de topologia) ===")
    for r in indexer.search(query=full_query, n_results=5):
        print(f"  {r['filename']}: {r['relevance_score']}%")

    print("\n=== Query LIMPIA (solo titulo + descripcion + metricas, sin topologia) ===")
    for r in indexer.search(query=clean_query, n_results=5):
        print(f"  {r['filename']}: {r['relevance_score']}%")


if __name__ == "__main__":
    asyncio.run(main())
