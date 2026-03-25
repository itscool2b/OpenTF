"""Hybrid retriever combining vector similarity with BM25 keyword search.

Uses reciprocal rank fusion to merge results from both methods,
giving better recall than either alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from opentf.memory.store import MemoryEntry, MemoryStore

log = logging.getLogger(__name__)


@dataclass
class HybridRetriever:
    """Combines ChromaDB vector search with BM25 keyword search."""

    store: MemoryStore
    _corpus: list[str] = field(default_factory=list)
    _corpus_ids: list[str] = field(default_factory=list)
    _bm25: BM25Okapi | None = field(default=None, repr=False)
    _bm25_stale: bool = field(default=True)

    def _rebuild_bm25(self) -> None:
        """Rebuild the BM25 index from the ChromaDB collection."""
        collection = self.store.collection
        if collection.count() == 0:
            self._bm25 = None
            self._corpus = []
            self._corpus_ids = []
            self._bm25_stale = False
            return

        all_docs = collection.get()
        self._corpus = all_docs["documents"] or []  # type: ignore[assignment]
        self._corpus_ids = all_docs["ids"]
        tokenized = [doc.lower().split() for doc in self._corpus]
        self._bm25 = BM25Okapi(tokenized)
        self._bm25_stale = False

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """Hybrid search using reciprocal rank fusion.

        1. Vector search via ChromaDB (semantic similarity)
        2. BM25 keyword search (exact term matching)
        3. Reciprocal rank fusion to merge both result sets
        """
        vector_results = await self.store.search(query, top_k=top_k * 2)

        if self._bm25_stale or self._bm25 is None:
            self._rebuild_bm25()

        bm25_results: list[MemoryEntry] = []
        if self._bm25 and self._corpus:
            tokenized_query = query.lower().split()
            scores = self._bm25.get_scores(tokenized_query)
            ranked_indices = sorted(
                range(len(scores)), key=lambda i: scores[i], reverse=True
            )[:top_k * 2]
            for idx in ranked_indices:
                if scores[idx] > 0:
                    bm25_results.append(
                        MemoryEntry(
                            id=self._corpus_ids[idx],
                            content=self._corpus[idx],
                            distance=float(scores[idx]),
                        )
                    )

        fused = self._reciprocal_rank_fusion(vector_results, bm25_results, k=60)
        return fused[:top_k]

    def mark_stale(self) -> None:
        """Call after storing new memories to trigger BM25 rebuild on next search."""
        self._bm25_stale = True

    @staticmethod
    def _reciprocal_rank_fusion(
        *result_lists: list[MemoryEntry],
        k: int = 60,
    ) -> list[MemoryEntry]:
        """Merge multiple ranked lists using reciprocal rank fusion (RRF).

        RRF score = sum(1 / (k + rank)) across all lists.
        Higher k gives more weight to lower-ranked results.
        """
        scores: dict[str, float] = {}
        entries: dict[str, MemoryEntry] = {}

        for results in result_lists:
            for rank, entry in enumerate(results):
                scores[entry.id] = scores.get(entry.id, 0) + 1.0 / (k + rank + 1)
                entries[entry.id] = entry

        ranked_ids = sorted(scores, key=lambda eid: scores[eid], reverse=True)
        return [entries[eid] for eid in ranked_ids]
