"""
ARIA - Harness de Evaluacion Cuantitativa (Fase 1, v2)
========================================================
Cambios respecto a v1:
  - Ya NO se trunca el texto del analisis a 500 caracteres. Se guarda completo,
    porque la Fase 1 v1 demostro que hacia falta poder auditar el razonamiento
    real, no solo el veredicto final.
  - Nuevo Tier E: casos de CONTROL NEGATIVO reales, via Custom Payload.
    Son incidencias sin NINGUNA relacion topologica posible con los 5 nodos
    conocidos por el sistema (api-gateway, payment-service, checkout-service,
    aria_db, redis-cache). Si el sistema igualmente "diagnostica" aria_db o
    cualquier nodo interno aqui, eso SI es una alucinacion real, no
    razonamiento legitimo por dependencias.

Mide, contra el sistema REAL en marcha (no mocks):
  M1 - Precision de causa raiz (root cause)
  M2 - Correccion de citas RAG (compara fuentes citadas vs esperadas)
  M4 - Latencia de analisis (proxy de MTTR)

Uso:
    cd backend
    python3 -m evaluation.run_evaluation

Requiere: el backend corriendo en http://localhost:8000 (uvicorn --reload)
Salida: evaluation/results_TIMESTAMP.csv + resumen impreso en consola
"""

import argparse
import asyncio
import csv
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

BASE_URL = "http://localhost:8000"
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 90.0

RESULTS_DIR = Path(__file__).parent
RESULTS_DIR.mkdir(exist_ok=True)

# Nodos que el sistema conoce explicitamente (ver SYSTEM TOPOLOGY NODES en
# agents/orchestrator.py -> analyze_incident). Cualquier caso Tier E que
# termine citando uno de estos como causa raiz es, por definicion, un
# fallo de sobre-extrapolacion (no hay ninguna relacion topologica posible).
KNOWN_TOPOLOGY_NODES = [
    "api-gateway", "payment-service", "checkout-service", "aria_db", "redis-cache",
]


@dataclass
class TestCase:
    test_id: str
    tier: str  # "A" match directo, "B" topologia, "C" sin cobertura (ver notas), "E" control negativo
    expected_root_cause: list[str] = field(default_factory=list)
    expected_citations: list[str] = field(default_factory=list)
    forbid_citations: list[str] = field(default_factory=list)
    forbid_root_cause: list[str] = field(default_factory=list)  # si el root_cause predicho contiene esto -> FALLO
    preset_key: Optional[str] = None       # usa /simulator/fire/{key}
    custom_payload: Optional[dict] = None  # usa /simulator/custom (mutuamente excluyente con preset_key)
    notes: str = ""


# ─── Dataset de evaluacion ──────────────────────────────────────────────────

TEST_CASES: list[TestCase] = [
    TestCase(
        test_id="A1-postgres-pool",
        preset_key="postgres_p2_connections",
        tier="A",
        expected_root_cause=["aria_db", "postgres", "postgresql"],
        expected_citations=["RB-001", "RB-003", "PostgreSQL"],
        notes="Match directo: RB-001 cubre exactamente este escenario.",
    ),
    TestCase(
        test_id="A2-redis-memory",
        preset_key="redis_p3_memory",
        tier="A",
        expected_root_cause=["redis"],
        expected_citations=["RB-002", "Redis", "Playbook"],
        notes="Match directo: RB-002 + Playbook-002 cubren este escenario.",
    ),
    TestCase(
        test_id="B1-auth-latency-db-rootcause",
        preset_key="auth_p1_latency",
        tier="B",
        expected_root_cause=["aria_db", "postgres", "postgresql"],
        expected_citations=["RB-001", "RB-003"],
        notes="Requiere razonar via topologia: auth-service -> aria_db.",
    ),
    TestCase(
        test_id="C1-payment-no-coverage",
        preset_key="payment_p1_error_rate",
        tier="C",
        expected_root_cause=["payment-service"],
        expected_citations=[],
        forbid_citations=["RB-002", "Redis", "Playbook"],
        notes="OJO al leer resultados: en la Fase 1 v1 este caso 'fallo' pero el "
              "texto mostro razonamiento topologico legitimo (payment-service -> "
              "aria_db es una dependencia real del sistema). No es alucinacion "
              "automatica si vuelve a pasar - leer el texto completo. El Tier E "
              "es el que da la senal limpia de alucinacion real.",
    ),
    TestCase(
        test_id="C2-gateway-no-coverage",
        preset_key="api_gateway_p2_502",
        tier="C",
        expected_root_cause=["api-gateway"],
        expected_citations=[],
        forbid_citations=["RB-002", "Redis", "Playbook"],
        notes="Mismo caveat que C1: api-gateway -> aria_db/redis-cache es una "
              "dependencia topologica real, no una invencion.",
    ),
    TestCase(
        test_id="D1-k8s-oom-self-learning",
        preset_key="k8s_p1_oom",
        tier="D",
        expected_root_cause=["notification-service", "aria_db", "redis-cache"],
        expected_citations=["Auto Runbook", "AUTO", "notification"],
        notes="Valida el bucle de auto-aprendizaje: debe citar el runbook que el "
              "propio sistema genero la primera vez que resolvio este incidente.",
    ),

    # ─── Tier E: Control negativo real ─────────────────────────────────────
    # Escenarios deliberadamente SIN ninguna relacion topologica posible con
    # los 5 nodos conocidos. Si el sistema aun asi "encuentra" una causa raiz
    # interna, es una alucinacion real y medible.
    TestCase(
        test_id="E1-external-tls-cert",
        tier="E",
        expected_root_cause=["external"],  # el prompt exige literalmente este valor, no variantes
        expected_citations=[],
        forbid_root_cause=KNOWN_TOPOLOGY_NODES,
        forbid_citations=["RB-", "Playbook", "RUNBOOK-AUTO"],  # CUALQUIER doc interno es sospechoso aqui
        custom_payload={
            "title": "[P2] TLS certificate expiring on external CDN provider",
            "text": (
                "SSL/TLS certificate for external CDN partner "
                "(static-assets.thirdparty-cdn.com) expires in 3 days. "
                "No renewal detected in monitoring. This CDN is not part of "
                "our internal infrastructure and has no dependency on our "
                "databases or caches."
            ),
            "alert_type": "warning",
            "priority": "P2",
            "host": "external-cdn-monitor",
            "tags": ["service:external-cdn", "env:production", "vendor:thirdparty", "component:tls"],
            "metrics": {
                "days_until_expiry": 3,
                "cert_renewal_attempts": 0,
                "tls_handshake_success_rate": 99.8,
            },
        },
        notes="Control negativo: proveedor externo, cero relacion topologica posible "
              "con aria_db/redis-cache/servicios internos. Comportamiento correcto "
              "esperado: quedarse en el ambito de lo reportado (CDN/TLS) o admitir "
              "que no hay informacion suficiente para ir mas alla - NUNCA inventar "
              "una causa raiz en la infraestructura interna.",
    ),
    TestCase(
        test_id="E2-external-dns-vendor",
        tier="E",
        expected_root_cause=["external"],
        expected_citations=[],
        forbid_root_cause=KNOWN_TOPOLOGY_NODES,
        forbid_citations=["RB-", "Playbook", "RUNBOOK-AUTO"],
        custom_payload={
            "title": "[P2] DNS resolution failures to external vendor webhook endpoint",
            "text": (
                "Outbound DNS lookups to external vendor webhook endpoint "
                "(partner-webhooks.external-vendor.com) failing intermittently "
                "(34.5% failure rate). This affects only outbound calls to this "
                "specific third-party vendor. No internal services, databases, "
                "or caches are involved in this DNS path."
            ),
            "alert_type": "warning",
            "priority": "P2",
            "host": "dns-resolver-egress",
            "tags": ["service:external-vendor-webhook", "env:production", "vendor:external", "component:dns"],
            "metrics": {
                "dns_failure_rate_percent": 34.5,
                "dns_lookup_latency_ms": 8200,
                "affected_outbound_calls": 412,
            },
        },
        notes="Segundo control negativo, mismo proposito que E1 pero con un fallo "
              "de tipo diferente (DNS/red egress vs TLS/certificados), para no "
              "sacar conclusiones de un unico caso.",
    ),
]

CITATION_RE = re.compile(r"\[([\w\-\. ]+\.(?:pdf|md|docx|txt))\]", re.IGNORECASE)


def extract_citations(analysis_text: str) -> list[str]:
    return CITATION_RE.findall(analysis_text or "")


def any_substring_match(candidates: list[str], text: str) -> bool:
    text_low = (text or "").lower()
    return any(c.lower() in text_low for c in candidates)


async def fire_test_case(client: httpx.AsyncClient, tc: TestCase) -> str:
    if tc.custom_payload is not None:
        resp = await client.post(f"{BASE_URL}/simulator/custom", json=tc.custom_payload)
    else:
        resp = await client.post(f"{BASE_URL}/simulator/fire/{tc.preset_key}")
    resp.raise_for_status()
    data = resp.json()
    return data["incident_id"]


async def wait_for_analysis(client: httpx.AsyncClient, incident_id: str) -> tuple[Optional[dict], float]:
    start = time.monotonic()
    while (time.monotonic() - start) < POLL_TIMEOUT_S:
        resp = await client.get(f"{BASE_URL}/incidents")
        resp.raise_for_status()
        incidents = resp.json()
        incidents = incidents if isinstance(incidents, list) else incidents.get("incidents", [])
        for inc in incidents:
            if inc.get("id") == incident_id and inc.get("analysis"):
                elapsed = time.monotonic() - start
                return inc, elapsed
        await asyncio.sleep(POLL_INTERVAL_S)
    return None, time.monotonic() - start


async def run_test_case(client: httpx.AsyncClient, tc: TestCase) -> dict:
    label = tc.preset_key or "custom_payload"
    print(f"\n[{tc.test_id}] Disparando '{label}'...")
    fired_at = datetime.now().isoformat()
    incident_id = await fire_test_case(client, tc)
    print(f"   -> incident_id={incident_id}, esperando analisis (timeout {POLL_TIMEOUT_S:.0f}s)...")

    inc, elapsed = await wait_for_analysis(client, incident_id)

    if inc is None:
        print(f"   [TIMEOUT] No hubo analisis en {POLL_TIMEOUT_S:.0f}s")
        return {
            "test_id": tc.test_id, "preset_key": label, "tier": tc.tier,
            "incident_id": incident_id, "fired_at": fired_at,
            "predicted_root_cause": None, "root_cause_correct": False,
            "cited_sources": "", "citation_correct": False, "forbidden_citation_hit": False,
            "forbidden_root_cause_hit": False,
            "latency_seconds": None, "status": "TIMEOUT", "notes": tc.notes,
            "analysis_full_text": "",
        }

    analysis_text = inc.get("analysis", "") or ""
    predicted_root_cause = inc.get("service_affected", "") or ""

    root_cause_correct = any_substring_match(tc.expected_root_cause, predicted_root_cause) if tc.expected_root_cause else True
    forbidden_rc_hit = any_substring_match(tc.forbid_root_cause, predicted_root_cause) if tc.forbid_root_cause else False
    if forbidden_rc_hit:
        root_cause_correct = False

    cited = extract_citations(analysis_text)
    cited_str = "; ".join(cited)

    if tc.expected_citations:
        citation_correct = any(
            any_substring_match([exp], c) for exp in tc.expected_citations for c in (cited or [""])
        ) or any_substring_match(tc.expected_citations, analysis_text)
    else:
        citation_correct = True  # ausencia de cita es lo esperado/aceptable

    forbidden_cit_hit = any_substring_match(tc.forbid_citations, cited_str) if tc.forbid_citations else False
    if forbidden_cit_hit:
        citation_correct = False

    print(f"   -> root_cause='{predicted_root_cause}' "
          f"(esperado uno de {tc.expected_root_cause or '(cualquiera, salvo prohibidos)'}) "
          f"-> {'OK' if root_cause_correct else 'FALLO'}"
          + (" [PROHIBIDO detectado]" if forbidden_rc_hit else ""))
    print(f"   -> citas={cited or '(ninguna)'} -> {'OK' if citation_correct else 'FALLO'}"
          + (" [cita PROHIBIDA detectada]" if forbidden_cit_hit else ""))
    print(f"   -> latencia={elapsed:.1f}s")

    return {
        "test_id": tc.test_id, "preset_key": label, "tier": tc.tier,
        "incident_id": incident_id, "fired_at": fired_at,
        "predicted_root_cause": predicted_root_cause, "root_cause_correct": root_cause_correct,
        "cited_sources": cited_str, "citation_correct": citation_correct,
        "forbidden_citation_hit": forbidden_cit_hit,
        "forbidden_root_cause_hit": forbidden_rc_hit,
        "latency_seconds": round(elapsed, 1), "status": "OK", "notes": tc.notes,
        "analysis_full_text": analysis_text,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tier", type=str, default=None,
        help="Ejecuta solo los casos de un tier concreto, ej. --tier E (util para reprobar rapido tras un fix)"
    )
    parser.add_argument(
        "--repeats", type=int, default=1,
        help="Numero de veces que se repite CADA caso (default 1). Usa 3+ para el pase final, "
             "asi se mide la varianza del LLM en vez de un solo intento suerte/mala suerte."
    )
    args = parser.parse_args()

    cases = TEST_CASES
    if args.tier:
        cases = [tc for tc in TEST_CASES if tc.tier == args.tier.upper()]
        if not cases:
            print(f"No hay casos con tier='{args.tier}'. Tiers disponibles: "
                  f"{sorted(set(tc.tier for tc in TEST_CASES))}")
            return

    total_runs = len(cases) * args.repeats
    print(f"=== ARIA - Evaluacion Cuantitativa (Fase 1, v3) ===")
    print(f"Casos de prueba: {len(cases)}" + (f" (filtrado a tier={args.tier.upper()})" if args.tier else ""))
    print(f"Repeticiones por caso: {args.repeats}  (total ejecuciones: {total_runs})")
    print(f"Backend: {BASE_URL}\n")

    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            health = await client.get(f"{BASE_URL}/health")
            health.raise_for_status()
        except Exception as e:
            print(f"[ERROR] No se pudo conectar al backend en {BASE_URL}: {e}")
            print("¿Esta uvicorn corriendo? (uvicorn main:app --reload --port 8000)")
            return

        for tc in cases:
            for run_idx in range(1, args.repeats + 1):
                if args.repeats > 1:
                    print(f"\n--- {tc.test_id} (intento {run_idx}/{args.repeats}) ---")
                result = await run_test_case(client, tc)
                result["run_index"] = run_idx
                results.append(result)
                await asyncio.sleep(3)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"results_{timestamp}.csv"
    fieldnames = list(results[0].keys()) if results else []
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nResultados guardados en: {csv_path}")

    n = len(results)
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_rc_correct = sum(1 for r in results if r["root_cause_correct"])
    n_cit_correct = sum(1 for r in results if r["citation_correct"])
    n_forbidden_rc = sum(1 for r in results if r["forbidden_root_cause_hit"])
    latencies = [r["latency_seconds"] for r in results if r["latency_seconds"] is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"Casos ejecutados:                {n}")
    print(f"Casos completados (sin timeout): {n_ok}/{n}")
    print(f"M1 - Precision causa raiz:       {n_rc_correct}/{n}  ({100*n_rc_correct/n:.0f}%)")
    print(f"M2 - Correccion de citas RAG:    {n_cit_correct}/{n}  ({100*n_cit_correct/n:.0f}%)")
    print(f"Alucinaciones detectadas (causa raiz prohibida): {n_forbidden_rc}")
    print(f"M4 - Latencia media de analisis: {avg_latency:.1f}s")
    print("\nPor nivel (tier):")
    for tier in ["A", "B", "C", "D", "E"]:
        tier_results = [r for r in results if r["tier"] == tier]
        if not tier_results:
            continue
        tier_rc = sum(1 for r in tier_results if r["root_cause_correct"])
        tier_cit = sum(1 for r in tier_results if r["citation_correct"])
        label = {"A": "match directo", "B": "topologia", "C": "sin cobertura (ver notas)",
                  "D": "auto-aprendizaje", "E": "CONTROL NEGATIVO"}[tier]
        print(f"  Tier {tier} ({label}, n={len(tier_results)}): "
              f"causa_raiz={tier_rc}/{len(tier_results)}, citas={tier_cit}/{len(tier_results)}")

    if n_forbidden_rc > 0:
        print("\n⚠️  ATENCION: se detectaron respuestas que citan un nodo interno "
              "conocido (aria_db, redis-cache, etc.) como causa raiz en un caso "
              "de control negativo, donde topologicamente es imposible. Revisa "
              "analysis_full_text de esos casos en el CSV - esto SI es evidencia "
              "de alucinacion real.")

    if args.repeats > 1:
        print("\n" + "=" * 60)
        print(f"VARIANZA POR CASO (n={args.repeats} repeticiones)")
        print("=" * 60)
        seen = []
        for tc in cases:
            if tc.test_id in seen:
                continue
            seen.append(tc.test_id)
            case_results = [r for r in results if r["test_id"] == tc.test_id]
            rc_pass = sum(1 for r in case_results if r["root_cause_correct"])
            cit_pass = sum(1 for r in case_results if r["citation_correct"])
            distinct_rc = sorted(set(r["predicted_root_cause"] for r in case_results))
            print(f"  {tc.test_id}: causa_raiz {rc_pass}/{len(case_results)}, "
                  f"citas {cit_pass}/{len(case_results)}, "
                  f"valores distintos de root_cause vistos: {distinct_rc}")


if __name__ == "__main__":
    asyncio.run(main())