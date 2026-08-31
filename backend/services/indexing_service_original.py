import os

from langchain_openai import OpenAIEmbeddings
import hashlib
import aiofiles
from datetime import datetime
from typing import Optional
from uuid import UUID
import asyncio

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, TextLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai import OpenAIEmbeddings
import trafilatura
import httpx
import structlog

from core.config import settings
from models.database import Document, DocumentStatus


logger = structlog.get_logger()


class IndexingService:
    def __init__(self):
        self.chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port
        )
        self.collection = self.chroma_client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"}
        )
        self.embedder = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=settings.google_api_key,
            # 'retrieval_document' es el estándar para guardar en la BD
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ".", "!", "?", " ", ""]
        )

    # ─── Public Methods ──────────────────────────────────────────────────────

    async def index_file(
        self,
        doc_id: UUID,
        file_path: str,
        filename: str,
        category: str = "other",
        service_tag: Optional[str] = None,
    ) -> int:
        """Index a file. Returns number of chunks created."""
        try:
            logger.info("indexing_file_start", doc_id=str(doc_id), filename=filename)

            # Load document based on file type
            ext = filename.split(".")[-1].lower()
            text = await self._load_file(file_path, ext)

            chunks_count = await self._index_text(
                doc_id=doc_id,
                text=text,
                filename=filename,
                category=category,
                service_tag=service_tag,
                source="file",
            )

            logger.info("indexing_file_done", doc_id=str(doc_id), chunks=chunks_count)
            return chunks_count

        except Exception as e:
            logger.error("indexing_file_error", doc_id=str(doc_id), error=str(e))
            raise

    async def index_url(
        self,
        doc_id: UUID,
        url: str,
        title: str,
        category: str = "other",
        service_tag: Optional[str] = None,
    ) -> int:
        """Scrape and index a wiki URL."""
        try:
            logger.info("indexing_url_start", doc_id=str(doc_id), url=url)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()

            # trafilatura extracts clean content from HTML
            text = trafilatura.extract(
                response.text,
                include_tables=True,
                include_links=False,
                output_format="markdown",
                favor_precision=True,
            )

            if not text:
                raise ValueError(f"Could not extract content from {url}")

            # Add title as context
            full_text = f"# {title}\n\nSource: {url}\n\n{text}"

            chunks_count = await self._index_text(
                doc_id=doc_id,
                text=full_text,
                filename=title,
                category=category,
                service_tag=service_tag,
                source="url",
                source_url=url,
            )

            logger.info("indexing_url_done", doc_id=str(doc_id), chunks=chunks_count)
            return chunks_count

        except Exception as e:
            logger.error("indexing_url_error", doc_id=str(doc_id), error=str(e))
            raise

    async def delete_document(self, doc_id: UUID) -> None:
        """Remove all chunks of a document from ChromaDB."""
        try:
            results = self.collection.get(where={"doc_id": str(doc_id)})
            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                logger.info("document_deleted_from_chroma", doc_id=str(doc_id), chunks=len(results["ids"]))
        except Exception as e:
            logger.error("delete_document_error", doc_id=str(doc_id), error=str(e))
            raise

    async def reindex_document(
        self,
        doc_id: UUID,
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        filename: str = "",
        category: str = "other",
        service_tag: Optional[str] = None,
    ) -> int:
        """Delete existing chunks and reindex."""
        await self.delete_document(doc_id)

        if url:
            return await self.index_url(doc_id, url, filename, category, service_tag)
        elif file_path:
            return await self.index_file(doc_id, file_path, filename, category, service_tag)
        else:
            raise ValueError("Either file_path or url must be provided")

    def search(
        self,
        query: str,
        n_results: int = 5,
        category_filter: Optional[str] = None,
        service_filter: Optional[str] = None,
    ) -> list[dict]:
        """Search the knowledge base. Returns ranked chunks with metadata."""
        query_embedding = self.embedder.embed_query(query)

        # Build where filter
        where = {}
        conditions = []
        if category_filter:
            conditions.append({"category": {"$eq": category_filter}})
        if service_filter:
            conditions.append({"service_tag": {"$eq": service_filter}})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where if where else None,
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"][0]:
            return []

        chunks = []
        for i, chunk_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            relevance_score = 1 - distance  # cosine: lower distance = higher relevance

            if relevance_score >= settings.rag_score_threshold:
                chunks.append({
                    "chunk_id": chunk_id,
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "relevance_score": round(relevance_score * 100, 1),
                    "doc_id": results["metadatas"][0][i].get("doc_id"),
                    "filename": results["metadatas"][0][i].get("filename"),
                    "category": results["metadatas"][0][i].get("category"),
                    "page": results["metadatas"][0][i].get("page", 0),
                    "source_url": results["metadatas"][0][i].get("source_url"),
                })

        return sorted(chunks, key=lambda x: x["relevance_score"], reverse=True)

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """Compute MD5 hash of a file for deduplication."""
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()

    # ─── Private Methods ─────────────────────────────────────────────────────

    async def _load_file(self, file_path: str, ext: str) -> str:
        """Load and extract text from a file."""
        if ext == "pdf":
            loader = PyMuPDFLoader(file_path)
            pages = loader.load()
            return "\n\n".join([p.page_content for p in pages])
        elif ext in ["txt", "md", "markdown"]:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                return await f.read()
        elif ext == "docx":
            from docx import Document as DocxDocument
            doc = DocxDocument(file_path)
            return "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        else:
            # Fallback: try as text
            async with aiofiles.open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return await f.read()

    async def _index_text(
        self,
        doc_id: UUID,
        text: str,
        filename: str,
        category: str,
        service_tag: Optional[str],
        source: str,
        source_url: Optional[str] = None,
    ) -> int:
        """Split text into chunks, embed and store in ChromaDB."""
        from langchain_core.documents import Document as LCDocument

        lc_doc = LCDocument(page_content=text, metadata={"source": filename})
        chunks = self.splitter.split_documents([lc_doc])

        if not chunks:
            raise ValueError("No content could be extracted from document")

        texts = [chunk.page_content for chunk in chunks]
        embeddings = self.embedder.embed_documents(texts)
        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id": str(doc_id),
                "filename": filename,
                "category": category,
                "service_tag": service_tag or "",
                "source": source,
                "source_url": source_url or "",
                "chunk_index": i,
                "page": chunk.metadata.get("page", 0),
                "indexed_at": datetime.utcnow().isoformat(),
            }
            for i, chunk in enumerate(chunks)
        ]

        # Batch insert to ChromaDB
        # PROCESAMIENTO POR LOTES CON PAUSA (Para evitar RESOURCE_EXHAUSTED)
        batch_size = 15  # Reducimos el tamaño para no saturar la cuota gratuita
        for i in range(0, len(ids), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_ids = ids[i:i + batch_size]
            batch_metadatas = metadatas[i:i + batch_size]

            # 1. Generamos los embeddings SOLO para este lote pequeño
            # Usamos task_type="retrieval_document" aquí por ser indexación
            batch_embeddings = self.embedder.embed_documents(
                batch_texts, 
                task_type="retrieval_document"
            )

            # 2. Guardamos en ChromaDB
            self.collection.add(
                ids=batch_ids,
                embeddings=batch_embeddings,
                documents=batch_texts,
                metadatas=batch_metadatas,
            )
            
            # 3. Pausa de seguridad: permite que la API de Google resetee el límite
            await asyncio.sleep(1.5) 

        return len(chunks)


# Singleton instance
# Lazy singleton — se instancia cuando se usa por primera vez
_indexing_service = None

def get_indexing_service():
    global _indexing_service
    if _indexing_service is None:
        _indexing_service = IndexingService()
    return _indexing_service

# Mantener compatibilidad con el nombre anterior
indexing_service = None  # se inicializa en startup
