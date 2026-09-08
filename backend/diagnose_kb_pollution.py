"""
Diagnostico de solo lectura: cuantos runbooks auto-generados (RUNBOOK-AUTO-*)
hay en la Knowledge Base, agrupados por servicio, para decidir con datos
reales si conviene limpiar duplicados antes de la entrega.

NO BORRA NADA. Solo lista.

Uso (desde backend/, mismo venv que usa uvicorn):
    python3 diagnose_kb_pollution.py
"""
import asyncio
import re
from collections import defaultdict

from sqlalchemy import select

from core.database import AsyncSessionLocal
from models.database import Document, DocumentSource

# Agrupamos por el "servicio" que aparece en el nombre de archivo, ej.
# RUNBOOK-AUTO-PAYMENT-SERVICE-894bd98f.md -> PAYMENT-SERVICE
SERVICE_RE = re.compile(r"RUNBOOK-AUTO-(.+)-[0-9a-fA-F]{8}\.md$")


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document)
            .where(Document.source == DocumentSource.AUTO_GENERATED)
            .order_by(Document.filename)
        )
        docs = result.scalars().all()

    if not docs:
        print("No hay documentos AUTO_GENERATED en la Knowledge Base.")
        return

    by_service = defaultdict(list)
    for d in docs:
        m = SERVICE_RE.match(d.filename or "")
        service = m.group(1) if m else "(patron no reconocido)"
        by_service[service].append(d)

    print(f"Total documentos auto-generados: {len(docs)}\n")
    print("=" * 70)

    for service, items in sorted(by_service.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{service}  ({len(items)} documento(s))")
        for d in items:
            created = getattr(d, "created_at", None) or getattr(d, "indexed_at", None)
            print(f"  - id={d.id}  chunks={d.chunks_count}  creado={created}  "
                  f"archivo={d.filename}  path={d.file_path}")

    print("\n" + "=" * 70)
    print("Servicios con mas de 1 documento auto-generado (candidatos a "
          "consolidar, dejando solo el mas reciente):")
    for service, items in sorted(by_service.items(), key=lambda kv: -len(kv[1])):
        if len(items) > 1:
            print(f"  - {service}: {len(items)} documentos")


if __name__ == "__main__":
    asyncio.run(main())
