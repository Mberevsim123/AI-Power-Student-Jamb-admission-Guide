"""Local semantic embeddings and source-aware Qdrant retrieval."""

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Optional

from app.core.config import settings
from app.models.chat import QueryIntent

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: int
    doc_title: str
    category: str
    snippet: str
    score: float
    cycle_year: Optional[int] = None
    source_url: Optional[str] = None
    document_id: Optional[int] = None

    def to_dict(self):
        data = asdict(self)
        data["snippet"] = self.snippet[:300]
        data["score"] = round(self.score, 4)
        return data


@lru_cache(maxsize=2)
def _local_model(model_name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model_name)


class EmbeddingClient:
    async def embed(self, text: str) -> list[float]:
        if settings.EMBEDDING_PROVIDER == "openai":
            from openai import AsyncOpenAI

            async with AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0
            ) as client:
                resp = await client.embeddings.create(
                    input=text,
                    model=settings.EMBEDDING_MODEL,
                    dimensions=settings.EMBEDDING_DIMENSIONS,
                )
                vector = resp.data[0].embedding
        else:

            def encode():
                return next(_local_model(settings.EMBEDDING_MODEL).embed([text])).tolist()

            vector = await asyncio.to_thread(encode)
        if len(vector) != settings.EMBEDDING_DIMENSIONS:
            raise ValueError("Embedding dimension does not match EMBEDDING_DIMENSIONS")
        return vector


def vector_client():
    from qdrant_client import AsyncQdrantClient

    return AsyncQdrantClient(
        url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None, timeout=10
    )


async def ensure_collection():
    from qdrant_client.models import Distance, VectorParams

    client = vector_client()
    try:
        if not await client.collection_exists(settings.QDRANT_COLLECTION):
            await client.create_collection(
                settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=settings.EMBEDDING_DIMENSIONS, distance=Distance.COSINE
                ),
            )
        info = await client.get_collection(settings.QDRANT_COLLECTION)
        if info.config.params.vectors.size != settings.EMBEDDING_DIMENSIONS:
            raise ValueError("Qdrant dimensions differ; use a new collection and reindex sources.")
    finally:
        await client.close()


class RAGRetriever:
    def __init__(self, db=None):
        self.db = db
        self.embedder = EmbeddingClient()

    async def retrieve(
        self, query: str, intent: QueryIntent, top_k: int = 3, cycle_year: int | None = None
    ) -> list[RetrievedChunk]:
        if intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
            return []
        try:
            # SQL is authoritative: inactive, failed, and previous-cycle documents
            # must never continue serving from old vector payloads.
            if self.db is None:
                return []
            from sqlalchemy import or_, select

            from app.models.admission import AdmissionCycle
            from app.models.knowledge import DocumentChunk, KnowledgeDocument

            year = cycle_year or await self.db.scalar(
                select(AdmissionCycle.year).where(AdmissionCycle.is_current.is_(True))
            )
            stmt = select(KnowledgeDocument.id).where(
                KnowledgeDocument.is_active.is_(True), KnowledgeDocument.status == "indexed"
            )
            stmt = stmt.where(
                or_(KnowledgeDocument.cycle_year.is_(None), KnowledgeDocument.cycle_year == year)
            )
            ids = list((await self.db.scalars(stmt)).all())
            if not ids:
                return []
            # SQL text search remains usable with no embedding service or Qdrant.
            # Keep the same indexed/active/year gates as semantic retrieval.
            terms = set(re.findall(r"[a-z0-9]+", query.casefold())) - {
                "what",
                "which",
                "when",
                "where",
                "please",
                "about",
                "have",
                "does",
                "with",
                "from",
                "this",
                "that",
                "the",
                "for",
                "and",
                "are",
                "how",
                "can",
                "you",
                "tell",
                "me",
                "is",
                "to",
                "of",
                "a",
                "i",
                "my",
            }
            lexical = []
            if terms:
                candidates = await self.db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id.in_(ids))
                    .where(
                        or_(*[DocumentChunk.text.ilike(f"%{term}%") for term in sorted(terms)[:24]])
                    )
                    .order_by(DocumentChunk.id)
                    .limit(200)
                )
                for chunk in candidates:
                    words = set(re.findall(r"[a-z0-9]+", chunk.text.casefold()))
                    overlap = terms & words
                    if len(overlap) < min(2, len(terms)):
                        continue
                    doc = chunk.document
                    lexical.append(
                        RetrievedChunk(
                            chunk_id=chunk.id,
                            doc_title=doc.title,
                            category=doc.category,
                            snippet=chunk.text,
                            score=len(overlap) / len(terms),
                            cycle_year=doc.cycle_year,
                            source_url=doc.source_url,
                            document_id=doc.id,
                        )
                    )
                lexical.sort(key=lambda c: c.score, reverse=True)
            # Confident keyword evidence needs no external call. Weak or absent keyword
            # matches are fused with semantic hits, so paraphrases still find evidence.
            if lexical and lexical[0].score >= LEXICAL_CONFIDENT:
                return lexical[:top_k]
            try:
                semantic = await self._semantic(query, ids, top_k)
            except Exception:
                if not lexical:
                    raise
                logger.warning("Semantic retrieval unavailable; using keyword evidence")
                return lexical[:top_k]
            return reciprocal_rank_fusion(lexical, semantic)[:top_k] if lexical else semantic
        except Exception:
            logger.exception("Knowledge retrieval unavailable")
            return []

    async def _semantic(self, query, ids, top_k):
        from qdrant_client import models

        client = vector_client()
        try:
            response = await client.query_points(
                collection_name=settings.QDRANT_COLLECTION,
                query=await self.embedder.embed(query),
                limit=top_k,
                score_threshold=settings.RAG_SCORE_THRESHOLD,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchAny(any=ids))]
                ),
                with_payload=True,
            )
            return [
                RetrievedChunk(
                    chunk_id=h.payload.get("chunk_id", 0),
                    doc_title=h.payload.get("doc_title", ""),
                    category=h.payload.get("category", ""),
                    snippet=h.payload.get("text", ""),
                    score=h.score,
                    cycle_year=h.payload.get("cycle_year"),
                    source_url=h.payload.get("source_url"),
                    document_id=h.payload.get("doc_id"),
                )
                for h in response.points
            ]
        finally:
            await client.close()


LEXICAL_CONFIDENT = 0.6


def reciprocal_rank_fusion(*rankings, k=60):
    """Merge ranked lists by RRF; identical passages (doc + text) count once."""
    fused, best = {}, {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            key = (chunk.document_id, chunk.snippet[:200])
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank + 1)
            best.setdefault(key, chunk)
    return [best[key] for key in sorted(fused, key=fused.get, reverse=True)]
