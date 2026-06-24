# AI Models & Extraction Services

Answers two questions directly: **which AI models the system uses (and where)**, and
**what services extraction actually needs**. Configuration lives in
[`config.py`](../src/cpap_graphrag/config.py) / [`.env.example`](../.env.example).

---

## 1. Which models are used, and where

The system uses AI models at **three points** — two LLM roles (Claude) and one embedding
role (local). They are deliberately *different* tiers, because the jobs have different
accuracy/cost profiles.

| # | Stage | Model (default) | Why this choice | Could we skip it? |
|---|---|---|---|---|
| 1 | **Ingestion: extraction** | **Claude Opus 4.8** (`EXTRACTION_MODEL`) | Reads messy PDF/table text and emits schema-forced `{value/min/max/default, unit, page}`. Run **once per document**, so we pay for the most accurate tier where correctness compounds into the whole graph. | Not realistically — see §3. Rules/regex alone can't handle vendor-varied prose/tables. |
| 2 | **Serving: query planning** | **Claude Sonnet 4.6** (`AGENT_MODEL`) | Classifies a question into one of 7 query types + fills slots. Runs per query; needs to be fast/cheap. Routing after it is deterministic. | Partially — simple cases could be regex, but NL questions (#7 recommendation) need an LLM. |
| 3 | **Serving: answer synthesis** | **Claude Sonnet 4.6** (`AGENT_MODEL`) | Writes the grounded prose answer **from graph rows only**, with citations, or refuses. | Could template answers for #1–#6, but #7 and natural phrasing benefit from the LLM. |
| 4 | **Baseline only: embeddings** | **`all-MiniLM-L6-v2`** (local, sentence-transformers) | Embeds chunks for the **vector-only baseline** we benchmark against. Runs locally, **no API, no cost**. | It exists only to justify GraphRAG; not part of the GraphRAG answer path. |

**Key point for the presentation:** the GraphRAG *answer path* never embeds anything. Vectors
appear **only in the baseline** ([`baseline/vector_rag.py`](../src/cpap_graphrag/baseline/vector_rag.py)).
GraphRAG retrieves by **structured Cypher over typed nodes**, which is exactly why it beats the
baseline on numeric queries (#2, #6, #7) — no embedding similarity guessing at numbers.

### Two-tier rationale (Opus vs Sonnet)
- **Extraction = Opus.** One-off cost, accuracy errors are permanent (they poison the graph and
  every downstream answer). Worth the top tier.
- **Serving = Sonnet.** Per-query cost, latency-sensitive ("seconds, not minutes"), and the
  graph already did the hard work — the model just classifies and phrases. Cheaper tier fits.

All three are swappable via env vars; nothing is hard-coded to a model.

---

## 2. Do we *need* AI models at all?

**Yes for the system as specified — but only the LLM extraction is non-negotiable.** Mapping
to what the assignment grades:

- **Extraction (must have a model).** "Ingest the full corpus… capture specifications, features,
  modes, values, units, ranges." Vendor PDFs are unstructured and inconsistent; an LLM is the
  pragmatic way to get ontology-aligned, unit-rigorous extraction. This is the irreducible AI need.
- **Planning + answering (model strongly preferred).** Query types #1–#6 *could* be served by
  rules once the graph exists, but #7 (free-text patient recommendation) and natural,
  cited answers want an LLM. We keep it as a cheap tier rather than removing it.
- **Embeddings (optional, baseline only).** Required by the assignment *as a comparison*, not
  for the product.

So: **one mandatory model (extraction)**, two pragmatic ones (planning, answering), and one
local model that exists purely to prove the GraphRAG advantage.

---

## 3. What services does extraction need?

### 3.1 Local PoC — exactly one external service

Extraction is **parse (local) → LLM call (hosted) → normalize (local)**. Concretely:

| Need | Service | Local? |
|---|---|---|
| PDF text + tables | PyMuPDF + pdfplumber (Python libs) | ✅ local, no service |
| Unit normalization | `pint` + our UCUM/QUDT table | ✅ local, no service |
| **Structured extraction** | **Anthropic API (Claude)** | ☁️ **hosted — the one external dependency** |
| Concurrency | in-process `ThreadPoolExecutor` | ✅ local, no service |
| Persistence | Neo4j (Docker container) | ✅ local container |

**The only service extraction depends on is the Anthropic API.** No queue, no worker fleet, no
vector DB for extraction. Parsing and unit handling are plain libraries; concurrency is a
bounded thread pool ([`extract.py`](../src/cpap_graphrag/ingestion/extract.py)).

> Requirement: a reachable `ANTHROPIC_API_KEY`. Everything else runs on the box.

### 3.2 Avoiding the hosted API (if required)

If SleepMedCorp can't send datasheets to a hosted API (data-residency/compliance), extraction
can target a **self-hosted model server** instead — e.g. **vLLM**, **Ollama**, or **TGI**
serving an open model — exposing an OpenAI/Anthropic-compatible endpoint. That swaps "hosted
API" for "a model-server *service* you run", with a quality trade-off vs Claude. The code change
is just the client/base-URL; the schema-forced extraction contract is unchanged.

### 3.3 At scale — extraction grows services (per [`SCALE_ARCHITECTURE.md`](./SCALE_ARCHITECTURE.md))

When ingestion becomes continuous/multi-source, extraction gains supporting services:

| Service | Role |
|---|---|
| **Message broker** (RabbitMQ / SQS) | distribute `extract` jobs across workers, retries, DLQ |
| **Worker fleet** (Celery) | run extraction concurrently; autoscaled on queue depth |
| **Object storage** (S3) | hold source PDFs; messages carry references, not bytes |
| **Rate-limit coordination** | global prefetch/concurrency cap so the fleet respects the LLM TPM/RPM limit |
| **Sentry / New Relic** | failed-task capture (DLQ) + queue depth/age/throughput metrics |
| **LLM** | still Claude API (or the self-hosted server from §3.2) |

The model itself doesn't change at scale — the **orchestration around it** does. The PoC's
idempotency (content-hash + `MERGE`) is what makes that safe to distribute.

---

## 4. Summary

- **Models:** Claude **Opus** for extraction (accuracy, one-off), Claude **Sonnet** for
  planning + answering (cheap, fast, per-query), local **MiniLM** embeddings for the baseline only.
- **Need them?** Extraction genuinely needs an LLM; planning/answering pragmatically use one;
  embeddings are comparison-only. The GraphRAG answer path uses **no embeddings**.
- **Services for extraction?** Locally, just the **Anthropic API** (PDF parsing + units are
  local libraries). Optionally a self-hosted model server for compliance. At scale, add a
  broker + workers + object storage around the same model.
