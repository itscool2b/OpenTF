"""Tests for MemoryStore and HybridRetriever (mocked dependencies)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import numpy as np

from opentf.memory.store import MemoryStore, MemoryEntry
from opentf.memory.retriever import HybridRetriever


# --- MemoryEntry model ---

def test_memory_entry_defaults() -> None:
    entry = MemoryEntry(id="1", content="test")
    assert entry.distance == 0.0
    assert entry.metadata == {}


def test_memory_entry_with_metadata() -> None:
    entry = MemoryEntry(id="1", content="test", metadata={"type": "solution"}, distance=0.5)
    assert entry.metadata["type"] == "solution"
    assert entry.distance == 0.5


# --- MemoryStore ---

@pytest.mark.asyncio
async def test_store_returns_id() -> None:
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = np.zeros(384)
    mock_collection = MagicMock()
    mock_collection.add = MagicMock()

    store = MemoryStore()
    store._embedder = mock_embedder
    store._collection = mock_collection

    entry_id = await store.store("test content")
    assert isinstance(entry_id, str)
    assert len(entry_id) > 0


@pytest.mark.asyncio
async def test_store_calls_embedder_and_collection() -> None:
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = np.zeros(384)
    mock_collection = MagicMock()
    mock_collection.add = MagicMock()

    store = MemoryStore()
    store._embedder = mock_embedder
    store._collection = mock_collection

    await store.store("content", metadata={"type": "test"})
    mock_embedder.encode.assert_called_once_with("content")
    mock_collection.add.assert_called_once()


@pytest.mark.asyncio
async def test_search_empty_collection() -> None:
    mock_collection = MagicMock()
    mock_collection.count.return_value = 0

    store = MemoryStore()
    store._collection = mock_collection

    results = await store.search("query")
    assert results == []


@pytest.mark.asyncio
async def test_search_returns_entries() -> None:
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = np.zeros(384)
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2
    mock_collection.query.return_value = {
        "ids": [["id1", "id2"]],
        "documents": [["doc1", "doc2"]],
        "metadatas": [[{"type": "a"}, {"type": "b"}]],
        "distances": [[0.1, 0.5]],
    }

    store = MemoryStore()
    store._embedder = mock_embedder
    store._collection = mock_collection

    results = await store.search("query", top_k=2)
    assert len(results) == 2
    assert results[0].id == "id1"
    assert results[0].content == "doc1"
    assert results[0].distance == 0.1


@pytest.mark.asyncio
async def test_delete_calls_collection() -> None:
    mock_collection = MagicMock()
    mock_collection.delete = MagicMock()

    store = MemoryStore()
    store._collection = mock_collection

    await store.delete("entry_123")
    mock_collection.delete.assert_called_once_with(ids=["entry_123"])


@pytest.mark.asyncio
async def test_close_clears_state() -> None:
    store = MemoryStore()
    store._embedder = MagicMock()
    store._collection = MagicMock()
    store._client = MagicMock()

    await store.close()
    assert store._embedder is None
    assert store._collection is None
    assert store._client is None


@pytest.mark.asyncio
async def test_max_document_eviction() -> None:
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = np.zeros(384)
    mock_collection = MagicMock()
    mock_collection.add = MagicMock()
    mock_collection.delete = MagicMock()

    store = MemoryStore(max_documents=3)
    store._embedder = mock_embedder
    store._collection = mock_collection

    # Store 4 items (limit is 3)
    for i in range(4):
        await store.store(f"content {i}")

    # First item should have been evicted
    assert mock_collection.delete.called
    assert len(store._id_queue) == 3


# --- HybridRetriever ---

def test_rrf_fusion_ranking() -> None:
    """RRF should rank entries in both lists higher."""
    shared = MemoryEntry(id="shared", content="both lists", distance=0.1)
    vec_only = MemoryEntry(id="vec", content="vector only", distance=0.2)
    bm25_only = MemoryEntry(id="bm25", content="bm25 only", distance=0.3)

    result = HybridRetriever._reciprocal_rank_fusion(
        [shared, vec_only],
        [shared, bm25_only],
        k=60,
    )
    assert result[0].id == "shared"  # Should be ranked highest (in both lists)


def test_rrf_single_list_entries_ranked_lower() -> None:
    both = MemoryEntry(id="both", content="a", distance=0.1)
    one = MemoryEntry(id="one", content="b", distance=0.2)

    result = HybridRetriever._reciprocal_rank_fusion(
        [both, one],
        [both],
        k=60,
    )
    ids = [e.id for e in result]
    assert ids.index("both") < ids.index("one")


@pytest.mark.asyncio
async def test_retriever_close() -> None:
    mock_store = MagicMock()
    mock_store.close = AsyncMock()
    retriever = HybridRetriever(store=mock_store)
    retriever._corpus = ["doc1"]
    retriever._corpus_ids = ["id1"]
    retriever._bm25 = MagicMock()

    await retriever.close()
    mock_store.close.assert_called_once()
    assert retriever._bm25 is None
    assert len(retriever._corpus) == 0


def test_mark_stale() -> None:
    retriever = HybridRetriever(store=MagicMock())
    retriever._bm25_stale = False
    retriever.mark_stale()
    assert retriever._bm25_stale is True
