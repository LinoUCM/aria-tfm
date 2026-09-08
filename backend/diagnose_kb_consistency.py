"""
Diagnostico de solo lectura: compara los documentos auto-generados que
existen en ChromaDB (metadata source="feedback_loop") frente a los que
existen en la tabla `documents` de Postgres (source=AUTO_GENERATED).

Objetivo: encontrar TODOS los "documentos fantasma" (indexados en el
vector store pero sin fila en Postgres, como el RUNBOOK-AUTO-PAYMENT-SERVICE
que ya detectamos) y el caso inverso, antes de decidir que borrar.

NO BORRA NADA. Solo compara y lista.

Uso (desde backend/, mismo venv que usa uvicorn):
    python3 diagnose_kb_consistency.py
"""
import asyncio
from collections import defaultdict

from sqlalchemy import select

from core.database import AsyncSessionLocal
from models.database import Document, DocumentSource
from services.indexing_service import get_indexing_service


async def main():
    indexer = get_indexing_service()

    # ChromaDB: todos los chunks con source="feedback_loop" (auto-generados)
    chroma_result = indexer.collection.get(
        where={"source": {"$eq": "feedback_loop"}},
        include=["metadatas"],
    )
    chroma_files = defaultdict(lambda: {"chunks": 0, "doc_ids": set()})
    for meta in chroma_result.get("metadatas", []):
        fn = meta.get("filename") or "(sin filename)"
        chroma_files[fn]["chunks"] += 1
        chroma_files[fn]["doc_ids"].add(meta.get("doc_id", ""))

    # Postgres: todos los Document con source=AUTO_GENERATED
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(Document.source == DocumentSource.AUTO_GENERATED)
        )
        pg_docs = result.scalars().all()

    pg_files = defaultdict(int)
    pg_ids_by_filename = defaultdict(list)
    for d in pg_docs:
        pg_files[d.filename] += 1
        pg_ids_by_filename[d.filename].append(str(d.id))

    chroma_names = set(chroma_files.keys())
    pg_names = set(pg_files.keys())

    print(f"Archivos distintos en ChromaDB (feedback_loop): {len(chroma_names)}")
    print(f"Archivos distintos en Postgres (AUTO_GENERATED):  {len(pg_names)}")

    only_chroma = chroma_names - pg_names
    only_pg = pg_names - chroma_names
    both = chroma_names & pg_names

    print("\n=== SOLO en ChromaDB, SIN fila en Postgres ('documentos fantasma') ===")
    if not only_chroma:
        print("  (ninguno)")
    for fn in sorted(only_chroma):
        info = chroma_files[fn]
        print(f"  - {fn}: {info['chunks']} chunks, doc_id(s) en Chroma = {info['doc_ids']}")

    print("\n=== SOLO en Postgres, SIN vectores en ChromaDB (huerfanos inversos) ===")
    if not only_pg:
        print("  (ninguno)")
    for fn in sorted(only_pg):
        print(f"  - {fn}: {pg_files[fn]} fila(s), ids Postgres = {pg_ids_by_filename[fn]}")

    print("\n=== En ambos sitios (consistentes, o con filas duplicadas en Postgres) ===")
    for fn in sorted(both):
        c = chroma_files[fn]
        p = pg_files[fn]
        flag = "  <-- MAS DE UNA FILA en Postgres para el mismo archivo" if p > 1 else ""
        print(f"  - {fn}: {c['chunks']} chunks en Chroma, {p} fila(s) en Postgres{flag}")
        if p > 1:
            print(f"      ids Postgres: {pg_ids_by_filename[fn]}")


if __name__ == "__main__":
    asyncio.run(main())
