import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from observability.metrics import emit_external_api, emit_tool_call

try:
    from azure.core.credentials import AzureKeyCredential  # type: ignore
    from azure.search.documents import SearchClient  # type: ignore
    from azure.search.documents.models import VectorizedQuery  # type: ignore
except ImportError:  # pragma: no cover
    AzureKeyCredential = None  # type: ignore
    SearchClient = None  # type: ignore
    VectorizedQuery = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AZURE_OPENAI_API_VERSION = "2024-02-01"
EMBEDDING_MODEL = "text-embedding-ada-002"
MAX_TOP_K = 20


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class ProjectKnowledgeInput(BaseModel):
    query: str
    document_types: Optional[List[str]] = None
    domains: Optional[List[str]] = None
    top_k: int = Field(default=6, ge=1, le=MAX_TOP_K)


# ---------------------------------------------------------------------------
# Observability helpers
# ---------------------------------------------------------------------------


def _emit_rag_metrics(top_score: float, chunks_returned: int) -> None:
    """Emit RAG-specific DD gauges (best-effort)."""
    try:
        from observability.metrics import statsd  # type: ignore

        if statsd is None:
            return
        statsd.gauge("rag.retrieval.top_score", top_score, tags=["service:infratools-mcp"])
        statsd.gauge(
            "rag.retrieval.chunks_returned", chunks_returned, tags=["service:infratools-mcp"]
        )
        statsd.gauge(
            "rag.index.last_updated",
            time.time(),
            tags=["service:infratools-mcp", "index:infra-advisor-knowledge"],
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Azure OpenAI embedding
# ---------------------------------------------------------------------------


async def _embed_query(query: str) -> list[float]:
    """
    Embed the search query using Azure OpenAI text-embedding-ada-002.

    Required env vars:
        AZURE_OPENAI_ENDPOINT
        AZURE_OPENAI_API_KEY
        AZURE_OPENAI_EMBEDDING_DEPLOYMENT  (default: text-embedding-ada-002)
    """
    try:
        from openai import AsyncAzureOpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("openai package is required for project_knowledge tool") from exc

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", EMBEDDING_MODEL)

    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=AZURE_OPENAI_API_VERSION,
    )

    api_start = time.monotonic()
    try:
        response = await client.embeddings.create(input=query, model=deployment)
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("azure_openai_embedding", api_latency_ms)
        return response.data[0].embedding
    except Exception as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("azure_openai_embedding", api_latency_ms, error_type="embedding_error")
        raise RuntimeError(f"Failed to embed query: {exc}") from exc


# ---------------------------------------------------------------------------
# OData filter builder
# ---------------------------------------------------------------------------


def _build_filter(
    document_types: Optional[List[str]],
    domains: Optional[List[str]],
) -> Optional[str]:
    """
    Construct an OData $filter expression for Azure AI Search.

    Example output:
        (document_type eq 'water_plan_project') and (domain eq 'water' or domain eq 'energy')
    """
    clauses: list[str] = []

    if document_types:
        dt_parts = " or ".join(f"document_type eq '{dt}'" for dt in document_types)
        clauses.append(f"({dt_parts})")

    if domains:
        dom_parts = " or ".join(f"domain eq '{d}'" for d in domains)
        clauses.append(f"({dom_parts})")

    return " and ".join(clauses) if clauses else None


# ---------------------------------------------------------------------------
# Chunk normaliser
# ---------------------------------------------------------------------------


def _normalise_chunk(result: Any, rank: int) -> dict[str, Any]:
    """Map an Azure AI Search SearchResult to the standard chunk output schema."""
    score = getattr(result, "@search.score", None)
    if score is None:
        score = getattr(result, "score", None)

    return {
        "content": getattr(result, "content", "") or "",
        "source": getattr(result, "source", "") or "",
        "document_type": getattr(result, "document_type", "") or "",
        "domain": getattr(result, "domain", "") or "",
        "score": float(score) if score is not None else 0.0,
        "source_url": getattr(result, "source_url", None),
        "chunk_index": getattr(result, "chunk_index", rank),
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def search_project_knowledge(
    input_data: ProjectKnowledgeInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Hybrid semantic + keyword search against the Azure AI Search knowledge base.

    Embeds the query with text-embedding-ada-002, then executes a hybrid search
    (VectorizedQuery + keyword search_text) on the infra-advisor-knowledge index.

    Returns a list of normalised chunk dicts; on error returns a structured
    error dict (never raises).

    DD metrics emitted:
        rag.retrieval.top_score       (gauge)
        rag.retrieval.chunks_returned (gauge)
        rag.index.last_updated        (gauge)
    """
    tool_start = time.monotonic()

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
    api_key = os.environ.get("AZURE_SEARCH_API_KEY", "")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")

    if not endpoint or not api_key:
        emit_tool_call(
            "search_project_knowledge",
            (time.monotonic() - tool_start) * 1000,
            "error",
        )
        return {
            "error": "Azure AI Search credentials not configured.",
            "source": "azure_ai_search",
            "retriable": False,
        }

    # Step 1: embed the query
    try:
        query_vector = await _embed_query(input_data.query)
    except RuntimeError as exc:
        emit_tool_call(
            "search_project_knowledge",
            (time.monotonic() - tool_start) * 1000,
            "error",
        )
        return {
            "error": str(exc),
            "source": "azure_openai",
            "retriable": True,
        }

    # Step 2: hybrid search
    if SearchClient is None or AzureKeyCredential is None or VectorizedQuery is None:
        emit_tool_call(
            "search_project_knowledge",
            (time.monotonic() - tool_start) * 1000,
            "error",
        )
        return {
            "error": "azure-search-documents package not available.",
            "source": "azure_ai_search",
            "retriable": False,
        }

    search_client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(api_key),
    )

    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=input_data.top_k,
        fields="content_vector",
    )

    odata_filter = _build_filter(input_data.document_types, input_data.domains)

    api_start = time.monotonic()
    try:
        results_iter = search_client.search(
            search_text=input_data.query,
            vector_queries=[vector_query],
            filter=odata_filter,
            top=input_data.top_k,
            include_total_count=True,
            select=["content", "source", "document_type", "domain", "source_url", "chunk_index"],
        )
        chunks = [_normalise_chunk(r, i) for i, r in enumerate(results_iter)]
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("azure_ai_search", api_latency_ms)

    except Exception as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("azure_ai_search", api_latency_ms, error_type="search_error")
        logger.error("Azure AI Search hybrid search failed: %s", exc)
        emit_tool_call(
            "search_project_knowledge",
            (time.monotonic() - tool_start) * 1000,
            "error",
        )
        return {
            "error": f"Azure AI Search query failed: {exc}",
            "source": "azure_ai_search",
            "retriable": True,
        }

    if not chunks:
        logger.info("Azure AI Search returned zero chunks for query: %s", input_data.query)
        emit_tool_call(
            "search_project_knowledge",
            (time.monotonic() - tool_start) * 1000,
            "success",
            result_count=0,
        )
        return []

    top_score = max(c["score"] for c in chunks)
    _emit_rag_metrics(top_score=top_score, chunks_returned=len(chunks))

    emit_tool_call(
        "search_project_knowledge",
        (time.monotonic() - tool_start) * 1000,
        "success",
        result_count=len(chunks),
    )
    logger.info(
        "search_project_knowledge: %d chunks returned, top_score=%.4f",
        len(chunks),
        top_score,
    )
    return chunks
