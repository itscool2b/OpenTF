"""Persistent memory store using ChromaDB and sentence-transformers.

Stores completed solutions, user preferences, error patterns, and cached
skills. Uses local embeddings (all-MiniLM-L6-v2, 22MB) -- no external API needed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class MemoryEntry(BaseModel):
    """A single memory record."""

    id: str
    content: str
    metadata: dict[str, Any] = {}
    distance: float = 0.0


@dataclass
class MemoryStore:
    """Persistent memory with vector search."""

    persist_dir: str = ".opentf/memory"
    embedding_model: str = EMBEDDING_MODEL
    _embedder: SentenceTransformer | None = field(default=None, repr=False)
    _client: chromadb.ClientAPI | None = field(default=None, repr=False)
    _collection: chromadb.Collection | None = field(default=None, repr=False)

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            log.info("Loading embedding model: %s", self.embedding_model)
            self._embedder = SentenceTransformer(self.embedding_model)
        return self._embedder

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name="opentf_memory",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def store(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a memory entry with its embedding. Returns the entry ID."""
        entry_id = uuid.uuid4().hex
        embedding = self.embedder.encode(content).tolist()
        meta = {
            "stored_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        # ChromaDB requires string values in metadata
        meta = {k: str(v) for k, v in meta.items()}
        self.collection.add(
            ids=[entry_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
        log.debug("Stored memory %s: %s...", entry_id[:8], content[:60])
        return entry_id

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """Search memory by vector similarity."""
        if self.collection.count() == 0:
            return []

        embedding = self.embedder.encode(query).tolist()
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, self.collection.count()),
        )

        entries = []
        for i in range(len(results["ids"][0])):
            entries.append(
                MemoryEntry(
                    id=results["ids"][0][i],
                    content=results["documents"][0][i],  # type: ignore[index]
                    metadata=results["metadatas"][0][i] or {},  # type: ignore[index]
                    distance=results["distances"][0][i] if results["distances"] else 0.0,  # type: ignore[index]
                )
            )
        return entries

    async def delete(self, entry_id: str) -> None:
        """Delete a memory entry by ID."""
        self.collection.delete(ids=[entry_id])
