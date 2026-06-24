"""
Vector-only RAG baseline over the SAME corpus chunks.

Required by the assignment to justify the GraphRAG investment. Deliberately a
competent-but-vanilla baseline: local embeddings -> Chroma -> top-k -> LLM answer.
It will struggle on query types #2 (units/range/default), #6 (numeric constraints)
and #7 (ranking) because it cannot do numeric joins — that gap is the whole point.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..ingestion.inventory import unzip_corpus
from ..ingestion.parse import parse_pdf
from ..ingestion.segment import segment_pages

_COLLECTION = "cpap_chunks"


def _client():
    import chromadb
    from chromadb.utils import embedding_functions
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=settings.embed_model)
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client, ef


def build_index() -> int:
    """Chunk the same corpus and embed into Chroma. Returns chunk count."""
    client, ef = _client()
    coll = client.get_or_create_collection(_COLLECTION, embedding_function=ef)
    corpus = unzip_corpus()
    ids, docs, metas = [], [], []
    for pdf in sorted(corpus.rglob("*.pdf")):
        for i, seg in enumerate(segment_pages(parse_pdf(pdf))):
            ids.append(f"{pdf.name}-{seg.page}-{i}")
            docs.append(seg.content)
            metas.append({"source_doc": seg.source_doc, "page": seg.page, "kind": seg.kind})
    if ids:
        coll.upsert(ids=ids, documents=docs, metadatas=metas)
    return len(ids)


def answer_question(question: str, k: int = 5) -> dict:
    """Top-k retrieve + LLM answer with chunk citations (no graph, no numeric joins)."""
    client, ef = _client()
    coll = client.get_or_create_collection(_COLLECTION, embedding_function=ef)
    res = coll.query(query_texts=[question], n_results=k)
    chunks = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    context = "\n\n---\n".join(
        f"[{m['source_doc']} p.{m['page']}] {c}" for c, m in zip(chunks, metas)
    )

    from ..llm import call
    res = call(
        settings.agent_model,
        max_tokens=1024,
        system="Answer ONLY from the provided chunks. Cite [doc p.PAGE]. If not present, say you cannot answer.",
        messages=[{"role": "user", "content": f"Question: {question}\n\nChunks:\n{context}"}],
    )
    return {"answer": res.text, "citations": metas, "chunks": chunks, "cost_usd": res.cost_usd}
