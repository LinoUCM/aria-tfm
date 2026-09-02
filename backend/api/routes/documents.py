import os
import uuid
import aiofiles
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from core.database import get_db
from core.config import settings
from models.database import Document, DocumentStatus, DocumentCategory, DocumentSource
from models.schemas import (
    DocumentUploadResponse, DocumentURLRequest, DocumentResponse, KBStatsResponse
)
from services.indexing_service import get_indexing_service
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/admin/documents", tags=["Knowledge Base"])

ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "markdown", "docx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


# ─── Upload File ─────────────────────────────────────────────────────────────

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form(default="other"),
    service_tag: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    # Validate extension
    ext = file.filename.split(".")[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type '{ext}' not allowed. Allowed: {ALLOWED_EXTENSIONS}")

    # Save file
    os.makedirs(settings.uploads_dir, exist_ok=True)
    doc_id = uuid.uuid4()
    file_path = os.path.join(settings.uploads_dir, f"{doc_id}.{ext}")

    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(413, "File too large (max 50MB)")
        await f.write(content)

    # Check for duplicate by hash
    file_hash = get_indexing_service().compute_file_hash(file_path)
    existing = await db.execute(select(Document).where(Document.file_hash == file_hash))
    if existing.scalar_one_or_none():
        os.remove(file_path)
        raise HTTPException(409, "This document has already been indexed")

    # Create DB record
    doc = Document(
        id=doc_id,
        filename=file.filename,
        title=file.filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title(),
        source=DocumentSource.FILE,
        file_path=file_path,
        file_type=ext,
        file_hash=file_hash,
        file_size=len(content),
        category=category,
        service_tag=service_tag,
        status=DocumentStatus.PROCESSING,
    )
    db.add(doc)
    await db.commit()

    # Index in background
    background_tasks.add_task(_index_file_task, doc_id, file_path, file.filename, category, service_tag)

    return DocumentUploadResponse(
        id=doc_id,
        filename=file.filename,
        status="processing",
        message="Document received and queued for indexing"
    )


# ─── Add URL ─────────────────────────────────────────────────────────────────

@router.post("/url", response_model=DocumentUploadResponse)
async def add_url(
    request: DocumentURLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    doc_id = uuid.uuid4()
    title = request.title or str(request.url)

    doc = Document(
        id=doc_id,
        filename=title,
        title=title,
        source=DocumentSource.URL,
        source_url=str(request.url),
        file_type="url",
        file_hash=str(uuid.uuid4()),  # URLs don't have a hash, use unique ID
        category=request.category,
        service_tag=request.service_tag,
        status=DocumentStatus.PROCESSING,
    )
    db.add(doc)
    await db.commit()

    background_tasks.add_task(
        _index_url_task, doc_id, str(request.url), title,
        request.category.value, request.service_tag
    )

    return DocumentUploadResponse(
        id=doc_id,
        filename=title,
        status="processing",
        message="URL queued for scraping and indexing"
    )


# ─── List Documents ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    category: Optional[str] = None,
    status: Optional[str] = None,
    service_tag: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).order_by(Document.created_at.desc())

    if category:
        query = query.where(Document.category == category)
    if status:
        query = query.where(Document.status == status)
    if service_tag:
        query = query.where(Document.service_tag == service_tag)

    result = await db.execute(query)
    return result.scalars().all()


# ─── Get Single Document ──────────────────────────────────────────────────────

@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(doc_id: UUID, db: AsyncSession = Depends(get_db)):
    doc = await _get_doc_or_404(doc_id, db)
    return doc


# ─── Get Document Content (view / open) ──────────────────────────────────────

@router.get("/{doc_id}/content")
async def get_document_content(doc_id: UUID, db: AsyncSession = Depends(get_db)):
    # Mismo patrón de consulta + 404 que usa GET /{doc_id} en este mismo archivo
    doc = await _get_doc_or_404(doc_id, db)

    if doc.source == DocumentSource.URL:
        raise HTTPException(
            400,
            "Los documentos de tipo URL se abren directamente desde su "
            "source_url, no tienen contenido servible por este endpoint."
        )

    if not doc.file_path or not os.path.exists(doc.file_path):
        raise HTTPException(
            404,
            "Este documento no tiene un archivo asociado (probablemente "
            "generado antes de que existiera esta función). Puedes "
            "eliminarlo desde el botón de papelera."
        )

    # El campo file_type puede venir vacío/None (p. ej. runbooks AUTO_GENERATED
    # antiguos); en ese caso lo derivamos de la extensión real del archivo en disco.
    effective_type = doc.file_type or Path(doc.file_path).suffix.lstrip(".").lower()

    if effective_type == "pdf":
        return FileResponse(
            path=doc.file_path,
            filename=doc.filename,
            media_type="application/pdf",
        )
    elif effective_type in ("md", "markdown", "txt"):
        with open(doc.file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content, "file_type": effective_type, "filename": doc.filename}
    else:
        return FileResponse(path=doc.file_path, filename=doc.filename)


# ─── Reindex ─────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/reindex", response_model=DocumentUploadResponse)
async def reindex_document(
    doc_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc_or_404(doc_id, db)
    # Borrar vectores antiguos en ChromaDB antes de volver a indexar
    await get_indexing_service().delete_document(str(doc_id))
    doc.status = DocumentStatus.PROCESSING
    doc.chunks_count = 0
    await db.commit()

    if doc.source == DocumentSource.URL:
        background_tasks.add_task(
            _index_url_task, doc_id, doc.source_url, doc.title or doc.filename,
            doc.category, doc.service_tag
        )
    else:
        background_tasks.add_task(
            _index_file_task, doc_id, doc.file_path, doc.filename,
            doc.category, doc.service_tag
        )

    return DocumentUploadResponse(
        id=doc_id,
        filename=doc.filename,
        status="processing",
        message="Document queued for reindexing"
    )


# ─── Delete ──────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}")
async def delete_document(doc_id: UUID, db: AsyncSession = Depends(get_db)):
    doc = await _get_doc_or_404(doc_id, db)

    # Remove from ChromaDB
    await get_indexing_service().delete_document(str(doc_id))

    # Remove file if exists
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    # Remove from DB
    await db.delete(doc)
    await db.commit()

    return {"message": f"Document '{doc.filename}' deleted successfully"}


# ─── KB Stats ────────────────────────────────────────────────────────────────

@router.get("/stats/summary", response_model=KBStatsResponse)
async def get_kb_stats(db: AsyncSession = Depends(get_db)):
    total_result = await db.execute(select(func.count(Document.id)))
    total = total_result.scalar()

    indexed_result = await db.execute(
        select(func.count(Document.id)).where(Document.status == DocumentStatus.INDEXED)
    )
    indexed = indexed_result.scalar()

    processing_result = await db.execute(
        select(func.count(Document.id)).where(Document.status == DocumentStatus.PROCESSING)
    )
    processing = processing_result.scalar()

    chunks_result = await db.execute(select(func.sum(Document.chunks_count)))
    total_chunks = chunks_result.scalar() or 0

    # Category breakdown
    cat_result = await db.execute(
        select(Document.category, func.count(Document.id))
        .group_by(Document.category)
    )
    categories = {row[0]: row[1] for row in cat_result.fetchall()}

    last_indexed_result = await db.execute(
        select(func.max(Document.indexed_at)).where(Document.status == DocumentStatus.INDEXED)
    )
    last_indexed = last_indexed_result.scalar()

    return KBStatsResponse(
        total_documents=total,
        total_chunks=total_chunks,
        indexed_documents=indexed,
        processing_documents=processing,
        categories=categories,
        last_updated=last_indexed,
    )


# ─── Background Tasks ────────────────────────────────────────────────────────

async def _index_file_task(doc_id, file_path, filename, category, service_tag):
    logger.info("TASK STARTED", doc_id=str(doc_id))
    from core.database import AsyncSessionLocal
    logger.info("LOCK ACQUIRED", doc_id=str(doc_id))
    async with AsyncSessionLocal() as db:
        try:
            chunks = await get_indexing_service().index_file(doc_id, file_path, filename, category, service_tag)
            doc = await _get_doc_or_404(doc_id, db)
            doc.status = DocumentStatus.INDEXED
            doc.chunks_count = chunks
            doc.indexed_at = datetime.utcnow()
            await db.commit()
        except Exception as e:
            doc = await _get_doc_or_404(doc_id, db)
            doc.status = DocumentStatus.ERROR
            doc.error_message = str(e)
            await db.commit()
            logger.error("index_file_task_failed", doc_id=str(doc_id), error=str(e))


async def _index_url_task(doc_id, url, title, category, service_tag):
    from core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            chunks = await get_indexing_service().index_url(doc_id, url, title, category, service_tag)
            doc = await _get_doc_or_404(doc_id, db)
            doc.status = DocumentStatus.INDEXED
            doc.chunks_count = chunks
            doc.indexed_at = datetime.utcnow()
            await db.commit()
        except Exception as e:
            doc = await _get_doc_or_404(doc_id, db)
            doc.status = DocumentStatus.ERROR
            doc.error_message = str(e)
            await db.commit()
            logger.error("index_url_task_failed", doc_id=str(doc_id), error=str(e))


async def _get_doc_or_404(doc_id: UUID, db: AsyncSession) -> Document:
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, f"Document {doc_id} not found")
    return doc
