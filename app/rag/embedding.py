"""
app/rag/embedding.py
DocumentIngester — splits a KnowledgeDocument into chunks,
generates embeddings, and upserts them into Qdrant.
"""

import hashlib
import logging
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import DocumentChunk, KnowledgeDocument
from app.rag.retriever import EmbeddingClient
from app.repositories.repository import KnowledgeRepository

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 120  # tokens (approximate via word count)
_CHUNK_OVERLAP = 24  # token overlap between chunks


class DocumentIngester:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = KnowledgeRepository(db)
        self.embedder = EmbeddingClient()

    async def ingest(self, doc: KnowledgeDocument) -> int:
        """
        Full pipeline: load text → chunk → embed → upsert Qdrant → save DB records.
        Returns number of chunks created.
        """
        raw_text = (doc.doc_meta or {}).get("text") or await self._load_text(doc)
        if not raw_text:
            logger.warning("No text extracted from doc %d", doc.id)
            raise ValueError("Document has no extractable text")

        chunks = self._split(raw_text)
        logger.info("Doc %d → %d chunks", doc.id, len(chunks))

        saved = 0
        for idx, chunk_text in enumerate(chunks):
            content_hash = _sha256(chunk_text)

            # Skip duplicate chunks (re-ingestion safety)
            existing = await self._chunk_exists(doc.id, content_hash)
            if existing:
                continue

            vector = await self.embedder.embed(chunk_text)
            vector_id = await self._upsert_qdrant(
                doc=doc,
                chunk_index=idx,
                chunk_text=chunk_text,
                vector=vector,
            )

            chunk = DocumentChunk(
                document_id=doc.id,
                chunk_index=idx,
                text=chunk_text,
                category=doc.category,
                cycle_year=doc.cycle_year,
                content_hash=content_hash,
                embedding_model=settings.EMBEDDING_MODEL,
                vector_id=vector_id,
                token_count=len(chunk_text.split()),
                chunk_meta={"doc_title": doc.title},
            )
            await self.repo.add_chunk(chunk)
            await self.db.flush()
            saved += 1

        await self.db.flush()
        return len(await self.repo.get_chunks_by_document(doc.id))

    # -----------------------------------------------------------------------
    # Text loading
    # -----------------------------------------------------------------------

    async def _load_text(self, doc: KnowledgeDocument) -> Optional[str]:
        """Load raw text from storage path or URL."""
        if doc.storage_path:
            return await self._read_file(doc.storage_path, doc.file_type or "txt")
        if doc.source_url:
            return await self._fetch_url(doc.source_url)
        return None

    async def _read_file(self, path: str, file_type: str) -> Optional[str]:
        import aiofiles

        try:
            if file_type == "pdf":
                return await self._extract_pdf(path)
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return await f.read()
        except Exception as exc:
            logger.error("Failed to read %s: %s", path, exc)
            return None

    async def _extract_pdf(self, path: str) -> Optional[str]:
        import asyncio

        try:
            import pdfplumber

            loop = asyncio.get_event_loop()

            def _extract():
                with pdfplumber.open(path) as pdf:
                    return "\n\n".join(page.extract_text() or "" for page in pdf.pages)

            return await loop.run_in_executor(None, _extract)
        except Exception as exc:
            logger.error("PDF extraction failed for %s: %s", path, exc)
            return None

    async def _fetch_url(self, url: str) -> Optional[str]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.error("URL fetch failed for %s: %s", url, exc)
            return None

    # -----------------------------------------------------------------------
    # Chunking (simple sliding-window word splitter)
    # -----------------------------------------------------------------------

    def _split(self, text: str) -> list[str]:
        words = text.split()
        step = _CHUNK_SIZE - _CHUNK_OVERLAP
        chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + _CHUNK_SIZE])
            if chunk.strip():
                chunks.append(chunk.strip())
        return chunks

    # -----------------------------------------------------------------------
    # Qdrant upsert
    # -----------------------------------------------------------------------

    async def _upsert_qdrant(
        self,
        doc: KnowledgeDocument,
        chunk_index: int,
        chunk_text: str,
        vector: list[float],
    ) -> str:
        from qdrant_client.models import PointStruct

        vector_id = str(uuid5(NAMESPACE_URL, f"jamb:{doc.id}:{chunk_index}"))
        payload = {
            "chunk_id": chunk_index,
            "doc_id": doc.id,
            "doc_title": doc.title,
            "category": doc.category,
            "cycle_year": doc.cycle_year,
            "text": chunk_text,
            "source_url": doc.source_url,
        }

        from app.rag.retriever import ensure_collection, vector_client

        await ensure_collection()
        client = vector_client()
        try:
            await client.upsert(
                collection_name=settings.QDRANT_COLLECTION,
                points=[PointStruct(id=vector_id, vector=vector, payload=payload)],
                wait=True,
            )
        finally:
            await client.close()
        return vector_id

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _chunk_exists(self, doc_id: int, content_hash: str) -> bool:
        from sqlalchemy import select

        from app.models.knowledge import DocumentChunk

        result = await self.db.execute(
            select(DocumentChunk.id)
            .where(DocumentChunk.document_id == doc_id)
            .where(DocumentChunk.content_hash == content_hash)
        )
        return result.scalar_one_or_none() is not None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
