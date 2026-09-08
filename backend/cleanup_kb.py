"""
Limpieza y migracion de la Knowledge Base — PASO 2, despues de aplicar el fix
de consistencia de doc_id en webhooks.py.

Hace dos cosas:

  1. RETAG (sin re-embeber, sin tocar contenido): para los documentos que se
     mantienen, actualiza la metadata `doc_id` de sus chunks en ChromaDB para
     que coincida con el id real de su fila `Document` en Postgres. Asi el
     borrado/reindexado desde la UI (documents.py) funcionara correctamente
     tambien para estos documentos antiguos, no solo para los nuevos creados
     tras el fix de webhooks.py.

  2. BORRA (ChromaDB + Postgres + archivo en disco, los tres a la vez) los
     documentos acordados como duplicados o fantasma.

Por defecto se ejecuta en modo DRY-RUN: no cambia nada, solo imprime el plan
exacto. Pasa --confirm para ejecutar de verdad.

Uso (desde backend/, mismo venv que usa uvicorn):
    python3 cleanup_kb.py            # dry-run, solo muestra el plan
    python3 cleanup_kb.py --confirm  # ejecuta de verdad
"""
import argparse
import asyncio
import os

from sqlalchemy import select

from core.database import AsyncSessionLocal
from models.database import Document
from services.indexing_service import get_indexing_service

# Documentos a MANTENER, pero re-etiquetando su doc_id en Chroma para que
# coincida con el id real del Document en Postgres (el mas reciente de cada
# servicio, o el unico existente cuando no hay duplicados).
KEEP_AND_RETAG = [
    "RUNBOOK-AUTO-ARIA_DB-fa5dcc79.md",
    "RUNBOOK-AUTO-API-GATEWAY-191861cc.md",
    "RUNBOOK-AUTO-CACHE-90a86901.md",
    "RUNBOOK-AUTO-CHECKOUT-SERVICE-f74c3808.md",
    "RUNBOOK-AUTO-NOTIFICATION-SERVICE-2466de34.md",  # necesario para Tier D
    "RUNBOOK-AUTO-REDIS-CACHE-5b945abe.md",
]

# Documentos a BORRAR por completo (Chroma + Postgres + archivo en disco).
DELETE = [
    "RUNBOOK-AUTO-ARIA_DB-0b619ff0.md",
    "RUNBOOK-AUTO-ARIA_DB-2beee1e3.md",
    "RUNBOOK-AUTO-ARIA_DB-66bd443f.md",   # 2 filas en Postgres, mismo archivo
    "RUNBOOK-AUTO-ARIA_DB-8f8caa5d.md",
    "RUNBOOK-AUTO-ARIA_DB-b2300d9c.md",
    "RUNBOOK-AUTO-ARIA_DB-d1c823ac.md",
    "RUNBOOK-AUTO-ARIA_DB-e0bd6f66.md",
    "RUNBOOK-AUTO-ARIA_DB-f2e2569f.md",
    "RUNBOOK-AUTO-ARIA_DB-fca47a86.md",
    "RUNBOOK-AUTO-ARIA_DB-ff5aa999.md",   # 2 filas en Postgres, mismo archivo
    "RUNBOOK-AUTO-PAYMENT-SERVICE-894bd98f.md",  # fantasma: solo existe en Chroma
]


async def retag(indexer, db, filename: str, dry_run: bool):
    result = await db.execute(select(Document).where(Document.filename == filename))
    docs = result.scalars().all()
    if not docs:
        print(f"  [AVISO] {filename}: no encontrado en Postgres, no se puede re-etiquetar. Se omite.")
        return
    if len(docs) > 1:
        print(f"  [AVISO] {filename}: {len(docs)} filas en Postgres (inesperado en la lista de "
              f"'mantener'); se usa la primera ({docs[0].id}) como id canonico.")
    new_doc_id = str(docs[0].id)

    chroma_data = indexer.collection.get(where={"filename": {"$eq": filename}}, include=["metadatas"])
    ids = chroma_data.get("ids", [])
    metadatas = chroma_data.get("metadatas", [])
    if not ids:
        print(f"  [AVISO] {filename}: no encontrado en ChromaDB, nada que re-etiquetar.")
        return

    old_doc_ids = {m.get("doc_id") for m in metadatas}
    print(f"  {filename}: {len(ids)} chunks, doc_id actual(es) en Chroma = {old_doc_ids} "
          f"-> nuevo doc_id = {new_doc_id}")

    if dry_run:
        return

    new_metadatas = [{**m, "doc_id": new_doc_id} for m in metadatas]
    indexer.collection.update(ids=ids, metadatas=new_metadatas)
    print(f"    -> re-etiquetado.")


async def delete_everywhere(indexer, db, filename: str, dry_run: bool):
    chroma_data = indexer.collection.get(where={"filename": {"$eq": filename}}, include=[])
    chroma_ids = chroma_data.get("ids", [])

    result = await db.execute(select(Document).where(Document.filename == filename))
    docs = result.scalars().all()

    print(f"  {filename}: {len(chroma_ids)} chunks en Chroma, {len(docs)} fila(s) en Postgres")
    for d in docs:
        exists = bool(d.file_path and os.path.exists(d.file_path))
        print(f"    - Postgres id={d.id}, archivo={d.file_path or '(sin archivo)'}"
              f"{' [existe en disco]' if exists else ''}")

    if dry_run:
        return

    if chroma_ids:
        indexer.collection.delete(where={"filename": {"$eq": filename}})
    for d in docs:
        if d.file_path and os.path.exists(d.file_path):
            os.remove(d.file_path)
        await db.delete(d)
    await db.commit()
    print(f"    -> borrado.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true",
                         help="Ejecuta de verdad. Sin este flag, solo muestra el plan (dry-run).")
    args = parser.parse_args()
    dry_run = not args.confirm

    indexer = get_indexing_service()

    print("=" * 70)
    print("DRY-RUN (nada se modifica todavia)" if dry_run else "EJECUTANDO DE VERDAD")
    print("=" * 70)

    async with AsyncSessionLocal() as db:
        print("\n--- Documentos a MANTENER (re-etiquetar doc_id en Chroma) ---")
        for fn in KEEP_AND_RETAG:
            await retag(indexer, db, fn, dry_run)

        print("\n--- Documentos a BORRAR (Chroma + Postgres + disco) ---")
        for fn in DELETE:
            await delete_everywhere(indexer, db, fn, dry_run)

    if dry_run:
        print("\nEsto ha sido un dry-run, no se ha modificado nada. Revisa el plan de "
              "arriba y, si esta bien, ejecuta:\n    python3 cleanup_kb.py --confirm")
    else:
        print("\nListo. Te recomiendo volver a correr diagnose_kb_consistency.py para "
              "confirmar que ya no quedan fantasmas ni filas duplicadas.")


if __name__ == "__main__":
    asyncio.run(main())
